# voice-agent-realtime-mcp-sip

Production-friendly starter to ship a **Realtime Voice Agent** with:
- **OpenAI Realtime** (browser WebRTC) — talk to the model with <1–2s round trip.
- **FastAPI** backend — serves ephemeral tokens and hosts stubs for tools.
- **Remote tools server (MCP-like)** — simple HTTP tools you can wire as function calls.
- **SIP bridge (optional stub)** — Twilio/CPaaS webhook skeletons to extend into telephony.

> ✅ Out of the box, you can **run a local browser voice demo** (mic → model voice).
> 🧰 Next, you can add **function calls/tools** and a **SIP provider** if needed.

---

## 1) Quick Start (Browser Voice Demo)

### Prereqs
- Python 3.10+
- An OpenAI API key with access to **Realtime** models
- (Optional) `ffmpeg` if you later experiment with SIP/media

### Setup
```bash
git clone <this-repo>
cd voice-agent-realtime-mcp-sip
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r server/requirements.txt

# copy env and set your keys
cp .env.example .env
# Edit .env: put OPENAI_API_KEY, choose MODEL (see notes below)
```

### Run
```bash
# in one terminal
uvicorn server.app:app --reload --port 8000

# open the client page
# Option A: simple file:// open
#   Open client/index.html in your browser
# Option B: serve via FastAPI static (already enabled)
#   http://localhost:8000
```

Click **Connect** → grant mic. You should hear the model respond in real time.

> If you see errors about model names or permissions, update **MODEL** in `.env`
> to a Realtime-capable model released for your account.

---

## 2) Environment Variables

Edit `.env`:
```
OPENAI_API_KEY=sk-...
# Choose a Realtime-capable model (verify in your account docs/dashboard)
MODEL=gpt-realtime
# or MODEL=gpt-4o-realtime-preview  (example; names can change)
OPENAI_BASE_URL=https://api.openai.com
# CORS origins for dev
CORS_ORIGINS=http://localhost:8000,http://127.0.0.1:8000,file://
```

> **Note:** Model names **change over time**. Check your dashboard/docs for the latest
> Realtime model identifier and update `MODEL` accordingly.

---

## 3) What’s included

```
/server
  app.py               # FastAPI app: token endpoint, static hosting, simple tools
  requirements.txt     # FastAPI + python-dotenv + httpx
  sip_webhooks.py      # (Optional) Twilio/CPaaS webhook skeletons
  tools.py             # Example "remote tools" endpoints
/client
  index.html           # Minimal WebRTC client (mic on/off, connect/disconnect)
  scripts/
    app.js             # Fetch ephemeral token; start WebRTC PeerConnection
  styles/
    main.css
```

### Browser → Realtime overview
1. Client fetches an **ephemeral session token** from `POST /v1/voice/session`.
2. Browser creates a **WebRTC** connection directly with OpenAI Realtime using the token.
3. Audio streams both ways. You speak; the model responds with synthesized voice.
4. (Optional) Function calls/tools are added through session options or via your backend.

---

## 4) SIP Bridge (Optional, Advanced)

We include **skeleton** webhooks in `server/sip_webhooks.py` for Twilio-like providers. The
common production pattern:
- Inbound PSTN call → SIP trunk → (provider webhook) → your server
- Your server streams audio (via CPaaS Media Streams or SIP media) to the Realtime model
- Bridge model audio back to the call; add DTMF capture & warm transfer.

**This repo ships a browser-first demo**. Extend the SIP stubs when you’re ready.

---

## 5) Tools / Functions

See `server/tools.py` for a minimal “remote tools” HTTP API (e.g., `create_ticket`, `lookup_order`).
You can stitch these into the model either as **function tools** at session creation time or
as **remote MCP servers** (depending on your provider’s supported patterns).

---

## 6) Security & Production Notes

- Never expose your **secret** API key to the browser. The server only mints **ephemeral tokens**.
- Add **CORS** allowlists and auth to `/v1/voice/session` in production.
- Log transcripts & tool calls with PII redaction.
- For telephony, handle codec negotiation, jitter buffers, barge-in, and DTMF carefully.

---

## 7) Troubleshooting

- **403/401 creating session:** your key lacks Realtime access or the model name is wrong.
- **No audio out:** ensure your output device isn’t blocked; check browser autoplay policy.
- **Mic denied:** allow microphone access in the browser.
- **CORS errors:** add your origin to `CORS_ORIGINS` in `.env`.

---

## 8) License

MIT


---

## Function-Calling (Server-Executed)
Use `GET /v1/voice/tools/schema` to fetch tool JSON schemas and `POST /v1/voice/tools/call` to execute a tool safely on the server.

### Example
```bash
curl -s http://localhost:8000/v1/voice/tools/schema | jq
```
```bash
curl -s -X POST http://localhost:8000/v1/voice/tools/call \
  -H 'Content-Type: application/json' \
  -d '{"name":"lookup_order","arguments":{"order_id":"A123"}}' | jq
```


## Twilio Elastic SIP / Media Streams (Example)
See `server/twilio/connect_stream.twiml` and `server/twilio/README.md` for a starting point.
