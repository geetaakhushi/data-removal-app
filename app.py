from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DB_PATH = Path(os.getenv("DATA_REMOVAL_DB", "data_removal.sqlite3"))
TERMINAL_DOC_STATUSES = {"completed", "partially_completed", "failed", "rejected"}


@dataclass
class ExposedRecord:
    user_id: str
    broker: str
    fields_found: list[str]
    source_file: str = ""
    status: str = "Exposed"
    reference_id: str = ""
    grievance_phone: str = ""
    request_text: str = ""
    translated_text: str = ""
    audio_path: str = ""
    call_attempt_id: str = ""


@dataclass
class UserProfile:
    user_id: str
    full_name: str
    phone: str
    email: str
    city: str = ""
    language: str = "hi-IN"


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS records (
        reference_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        broker TEXT NOT NULL,
        status TEXT NOT NULL,
        fields_found TEXT NOT NULL,
        source_file TEXT,
        request_text TEXT,
        translated_text TEXT,
        audio_path TEXT,
        call_attempt_id TEXT,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    return conn


def save_record(record: ExposedRecord) -> None:
    with db_connect() as conn:
        conn.execute("""INSERT INTO records
            (reference_id,user_id,broker,status,fields_found,source_file,request_text,
             translated_text,audio_path,call_attempt_id,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(reference_id) DO UPDATE SET
             status=excluded.status, fields_found=excluded.fields_found,
             source_file=excluded.source_file, request_text=excluded.request_text,
             translated_text=excluded.translated_text, audio_path=excluded.audio_path,
             call_attempt_id=excluded.call_attempt_id, updated_at=CURRENT_TIMESTAMP
        """, (record.reference_id, record.user_id, record.broker, record.status,
              json.dumps(record.fields_found, ensure_ascii=False), record.source_file,
              record.request_text, record.translated_text, record.audio_path,
              record.call_attempt_id))


def get_client():
    key = os.getenv("SARVAM_API_KEY")
    if not key:
        raise RuntimeError("SARVAM_API_KEY is missing. Copy .env.example to .env and set your key, or export it in the terminal.")
    try:
        from sarvamai import SarvamAI
    except ImportError as exc:
        raise RuntimeError("Sarvam SDK not installed. Run: python3 -m pip install -r requirements.txt") from exc
    return SarvamAI(api_subscription_key=key, timeout=60.0)


def scan_document(client, document_path: Path, user: UserProfile, broker: str) -> ExposedRecord:
    """Use Sarvam Document AI to extract fields from an uploaded listing/document."""
    if not document_path.is_file():
        raise FileNotFoundError(f"Document not found: {document_path}")
    mime_by_suffix = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".zip": "application/zip"}
    mime = mime_by_suffix.get(document_path.suffix.lower())
    if not mime:
        raise ValueError("Supported document types: PDF, PNG, JPG/JPEG, ZIP.")

    schema = {
        "type": "object",
        "properties": {
            "full_name": {"type": "string", "description": "Full name of the person whose data appears in the listing"},
            "phone": {"type": "string", "description": "Phone number of that person, including country code if shown"},
            "email": {"type": "string", "description": "Email address of that person if shown"},
            "city": {"type": "string", "description": "City or location associated with the person"},
            "other_fields": {"type": "array", "description": "Other personal data fields visible in the listing", "items": {"type": "string"}},
        },
    }
    with document_path.open("rb") as f:
        job = client.doc_ai.extract(
            file=[(document_path.name, f, mime)],
            schema=json.dumps(schema),
            language="en-IN",
            output_format="json",
        )
    print(f"Document AI job created: {job.job_id} (status: {job.status})")
    deadline = time.time() + 300
    while True:
        status = client.doc_ai.get_status(job_id=job.job_id)
        state = str(status.status).lower()
        print(f"  Document scan status: {state}")
        if state in TERMINAL_DOC_STATUSES:
            break
        if time.time() > deadline:
            raise TimeoutError("Document extraction took longer than 5 minutes; check the job in Sarvam and retry.")
        time.sleep(5)
    if state not in {"completed", "partially_completed"}:
        raise RuntimeError(f"Document extraction ended with status: {state}")
    result_obj = client.doc_ai.get_results(job_id=job.job_id)
    data = getattr(result_obj, "result", None)
    if data is None and isinstance(result_obj, dict):
        data = result_obj.get("result", result_obj)
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            data = {"raw_result": data}
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected Document AI result shape: {type(data).__name__}")

    fields = []
    for key in ("full_name", "phone", "email", "city"):
        if data.get(key):
            fields.append(key)
    other_fields = data.get("other_fields", [])
    if isinstance(other_fields, list):
        fields.extend(str(x) for x in other_fields if x)
    if not fields:
        fields = [f"extracted:{k}" for k, v in data.items() if v]
    if not fields:
        raise RuntimeError("Document AI returned no personal data fields. Review the document and schema.")
    return ExposedRecord(user_id=user.user_id, broker=broker, fields_found=fields, source_file=str(document_path.resolve()))


def draft_erasure_request(client, user: UserProfile, record: ExposedRecord) -> str:
    prompt = f"""Draft a concise, formal email requesting erasure of personal data under Section 12 of India's Digital Personal Data Protection Act, 2023. Do not claim that the law guarantees erasure in every circumstance; phrase it as a request. Do not invent facts.
Platform/company: {record.broker}
Sender name: {user.full_name}
Sender phone: {user.phone}
Sender email: {user.email}
Known data fields found: {', '.join(record.fields_found)}
Include a subject, greeting to the grievance/data-protection officer, request to erase the listed data, reference to Section 12, and request for written confirmation by email. Keep under 200 words."""
    response = client.chat.completions(
        model="sarvam-105b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    text = response.choices[0].message.content
    if not text or not text.strip():
        raise RuntimeError("Chat API returned an empty erasure request.")
    return text.strip()


def localise(client, text: str, target_language_code: str) -> str:
    if target_language_code == "en-IN":
        return text
    response = client.text.translate(
        input=text,
        source_language_code="en-IN",
        target_language_code=target_language_code,
    )
    translated = getattr(response, "translated_text", None)
    if not translated:
        raise RuntimeError("Translation API returned no translated_text. Check the selected language code and API response.")
    return translated


def speak_status(client, record: ExposedRecord, language: str, out_path: Path) -> Path:
    status_texts = {
        "hi-IN": f"आपका डेटा हटाने का अनुरोध तैयार है। संदर्भ संख्या {record.reference_id} है।",
        "en-IN": f"Your data removal request has been prepared. Reference number {record.reference_id}.",
        "ta-IN": f"உங்கள் தரவு நீக்கக் கோரிக்கை தயாராக உள்ளது. குறிப்பு எண் {record.reference_id}.",
        "bn-IN": f"আপনার ডেটা মুছে ফেলার অনুরোধ প্রস্তুত। রেফারেন্স নম্বর {record.reference_id}।",
    }
    line = status_texts.get(language, status_texts["en-IN"])
    audio = client.text_to_speech.convert(
        text=line,
        language_code=language if language in {"hi-IN", "en-IN", "ta-IN", "bn-IN", "te-IN", "mr-IN", "gu-IN", "kn-IN", "ml-IN", "pa-IN", "od-IN"} else "en-IN",
        model="bulbul:v4-flash",
    )
    try:
        from sarvamai.play import save
        save(audio, str(out_path))
    except ImportError:
        # SDK response contains base64 audio; this fallback avoids requiring the helper.
        import base64
        audios = getattr(audio, "audios", None)
        if audios is None and isinstance(audio, dict):
            audios = audio.get("audios")
        if not audios:
            raise RuntimeError("TTS response had no audio data.")
        out_path.write_bytes(base64.b64decode(audios[0]))
    return out_path


def trigger_outbound_call(record: ExposedRecord, destination_phone: str) -> str:
    """Place a real call through the documented Sarvam Voice Agents outbound endpoint.

    Requires workspace-specific Voice Agents configuration. Calling is never automatic.
    """
    required = ["SARVAM_VOICE_API_KEY", "SARVAM_ORG_ID", "SARVAM_WORKSPACE_ID", "SARVAM_AGENT_APP_ID", "SARVAM_AGENT_APP_VERSION", "SARVAM_CONNECTION_ID", "SARVAM_AGENT_PHONE_NUMBER"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Outbound calling is not configured. Missing environment variables: " + ", ".join(missing))
    try:
        app_version = int(os.environ["SARVAM_AGENT_APP_VERSION"])
    except ValueError as exc:
        raise RuntimeError("SARVAM_AGENT_APP_VERSION must be an integer.") from exc
    url = ("https://apps.sarvam.ai/api/outbounds/v1/orgs/" + os.environ["SARVAM_ORG_ID"] +
           "/workspaces/" + os.environ["SARVAM_WORKSPACE_ID"] + "/outbounds")
    payload = {
        "app_config": {
            "app_id": os.environ["SARVAM_AGENT_APP_ID"],
            "app_version": app_version,
            "connection_config": {
                "connection_id": os.environ["SARVAM_CONNECTION_ID"],
                "agent_phone_number": os.environ["SARVAM_AGENT_PHONE_NUMBER"],
            },
        },
        "user_config": {"user_phone_number": destination_phone},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"X-API-Key": os.environ["SARVAM_VOICE_API_KEY"], "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Voice API returned HTTP {exc.code}: {details}") from exc
    attempt_id = body.get("attempt_id")
    if not attempt_id:
        raise RuntimeError(f"Voice API response did not contain attempt_id: {body}")
    return str(attempt_id)


def run_live(args: argparse.Namespace) -> None:
    if not args.document:
        raise ValueError("Live mode requires --document PATH to a real PDF/PNG/JPG/JPEG/ZIP listing.")
    if not args.broker:
        raise ValueError("Live mode requires --broker NAME.")
    if not args.full_name or not args.phone or not args.email:
        raise ValueError("Live mode requires --full-name, --phone and --email.")
    user = UserProfile(args.user_id, args.full_name, args.phone, args.email, args.city or "", args.language)
    client = get_client()
    record = scan_document(client, Path(args.document), user, args.broker)
    record.reference_id = "DR-" + str(int(time.time()))
    print("\nExtracted fields:", ", ".join(record.fields_found))
    print("Source file:", record.source_file)

    record.request_text = draft_erasure_request(client, user, record)
    record.status = "Drafted"
    print("\n=== DRAFT ERASURE REQUEST (review before sending) ===\n")
    print(record.request_text)

    record.translated_text = localise(client, record.request_text, user.language)
    print(f"\n=== TRANSLATED REQUEST ({user.language}) ===\n")
    print(record.translated_text)

    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    record.audio_path = str(speak_status(client, record, user.language, output_dir / f"{record.reference_id}.wav"))
    print(f"\nAudio status saved to: {record.audio_path}")

    if args.place_call:
        if not args.destination_phone:
            raise ValueError("--place-call requires --destination-phone with an authorised destination number.")
        print("\nA real outbound call will be placed to:", args.destination_phone)
        confirm = input("Type PLACE CALL to confirm: ").strip()
        if confirm != "PLACE CALL":
            print("Call cancelled; no call was placed.")
        else:
            record.call_attempt_id = trigger_outbound_call(record, args.destination_phone)
            record.status = "CallQueued"
            print("Call attempt created. Attempt ID:", record.call_attempt_id)
    else:
        print("\nNo phone call was placed. Use --place-call only after configuring Voice Agents and obtaining permission.")

    save_record(record)
    print("\nSaved record to SQLite database:", DB_PATH.resolve())
    print("Reference:", record.reference_id, "| status:", record.status)


def run_demo() -> None:
    print("OFFLINE DEMO — no API calls, no real document scanning, no emails/calls, no data deletion.\n")
    sample = {
        "user_id": "demo-user",
        "broker": "ExampleDirectory",
        "fields_found": ["name", "phone", "city"],
        "source_file": "sample listing (mock data)",
        "status": "DemoOnly",
        "reference_id": "DEMO-001",
    }
    print(json.dumps(sample, indent=2))
    print("\nTo use live APIs, see README.md and run with --live plus a real document.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Data Removal Assistant powered by Sarvam AI")
    parser.add_argument("--live", action="store_true", help="Call real Sarvam APIs (uses API credits).")
    parser.add_argument("--document", help="Path to a listing PDF/PNG/JPG/JPEG/ZIP.")
    parser.add_argument("--broker", help="Platform/company name where the listing appears.")
    parser.add_argument("--user-id", default="user-001")
    parser.add_argument("--full-name", help="Your name as it should appear in the request.")
    parser.add_argument("--phone", help="Your contact phone number.")
    parser.add_argument("--email", help="Your contact email.")
    parser.add_argument("--city", default="")
    parser.add_argument("--language", default="hi-IN", help="Target language code, e.g. hi-IN or en-IN.")
    parser.add_argument("--place-call", action="store_true", help="Enable optional real outbound call; requires explicit confirmation.")
    parser.add_argument("--destination-phone", help="Authorised phone number for the grievance desk.")
    args = parser.parse_args()
    try:
        if args.live:
            run_live(args)
        else:
            run_demo()
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
