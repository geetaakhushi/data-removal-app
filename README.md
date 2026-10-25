# Data Removal Assistant — Sarvam AI

This is a corrected, runnable Python CLI version of the uploaded starter skeleton. It uses Sarvam's official Python SDK for live document extraction, chat drafting, translation, and text-to-speech. It saves a local status record to SQLite. Outbound calling is optional and disabled unless explicitly requested.

## Important limitations

- This app does **not** automatically find a person's data across the internet. You must supply a real listing screenshot/PDF that you are authorised to process.
- It drafts an erasure request but does **not** send an email. Review the draft and send it through your chosen email client.
- A call is never placed unless you pass `--place-call`, provide a destination number, configure the Voice Agents workspace, and type `PLACE CALL` at the confirmation prompt.
- A call attempt ID means the platform accepted an attempt, not that a person answered or that data was removed.
- This project does not make legal determinations. Review all drafts and verify applicable law and company procedures.

## 1. Open in VS Code

Unzip the project and open the `data_removal_app` folder in VS Code. Open **Terminal → New Terminal**.

## 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 3. Configure your API key

Copy the example environment file:

```bash
cp .env.example .env
```

Edit `.env` and replace `put_your_model_api_key_here` with your key from the Sarvam dashboard. Do not share the key or commit `.env` to GitHub.

The app loads `.env` automatically only if you add this at the top of `app.py` after imports:

```python
from dotenv import load_dotenv
load_dotenv()
```

(Or export the key in the same terminal session instead: `export SARVAM_API_KEY="your-key"`.)

## 4. Test the offline demo

```bash
python app.py
```

This mode makes no API calls and uses no personal data.

## 5. Run live APIs

Place a listing screenshot or PDF you are authorised to process in the project folder. Supported formats are PDF, PNG, JPG/JPEG, and ZIP; Document AI currently limits PDFs/ZIPs to 10 pages/images per job.

Run, replacing the sample values:

```bash
python app.py --live \
  --document "fixtures/listing.pdf" \
  --broker "Example Directory" \
  --full-name "Your Full Name" \
  --phone "+91XXXXXXXXXX" \
  --email "you@example.com" \
  --city "Varanasi" \
  --language "hi-IN"
```

The app will call Document AI, draft the request with Sarvam-105B, translate it, generate a WAV status announcement, and save the record to a local SQLite database. API usage may incur charges/consume credits. Review the generated request carefully. The code does not send it automatically.

Use `--language en-IN` if you want the request and status audio in English. Other supported translation languages depend on Sarvam's current model/API availability.

## 6. Optional outbound call

Do not enable this until a Voice Agent has been created and deployed/configured in your Sarvam Voice Agents workspace and you have permission to call the destination. Fill the optional `SARVAM_VOICE_*` values in `.env` using the IDs and phone number from your workspace, and add `load_dotenv()` as shown above.

Then add `--place-call --destination-phone "+91XXXXXXXXXX"` to the live command. The app asks you to type `PLACE CALL` before it submits a real call attempt. The outbound endpoint uses a **Voice Agents workspace API key** (`SARVAM_VOICE_API_KEY`), which may be different from the Model APIs key.

The call endpoint returns an attempt ID; it does not confirm the call was answered. Production tracking requires a configured webhook or checking call logs in Voice Agents.

## Troubleshooting

- `SARVAM_API_KEY is missing`: export the key in the current terminal or enable `.env` loading.
- HTTP 403: check key validity and that it is a Model API key.
- HTTP 429: check account credits/rate limits.
- Document AI failure: check file path, format, size, page limit, and API permissions.
- Outbound call missing configuration: fill all `SARVAM_VOICE_*` values and confirm the agent/telephony connection is deployed.

## Official references

- SDK and Document AI call shape: https://docs.sarvam.ai/api/getting-started/sdks
- Quickstart: https://docs.sarvam.ai/api/getting-started/quickstart
- Document extraction: https://docs.sarvam.ai/api/api-guides-tutorials/document-intelligence/overview
- Text-to-speech: https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/rest-api
- Voice outbound call endpoint: https://docs.sarvam.ai/voice-agents/api/instant-outbound/create
