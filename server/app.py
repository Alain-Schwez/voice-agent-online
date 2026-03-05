import os
import json
import typing as t

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import httpx

# File contains target website and other scraping and refreshing parameters -----------------
from website_index import build_index, refresh_loop
import asyncio

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com")
MODEL = os.getenv("MODEL", "gpt-realtime")

print("AFTER LOAD_DOTENV")
print("OPENAI_API_KEY from env =", os.getenv("OPENAI_API_KEY"))
print("OPENAI_API_KEY variable =", OPENAI_API_KEY)

CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:8000").split(",") if o.strip()]

app = FastAPI(title="voice-agent-realtime-mcp-sip")

# Startup initialization -----------------------------------
@app.on_event("startup")
async def startup():
    print("Building website knowledge index...")
    await build_index()
    print("Website index ready")
    # Start automatic daily refresh -------------------------
    asyncio.create_task(refresh_loop())

# Basic CORS for local dev -----------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the client files -------------------------------------
app.mount("/client", StaticFiles(directory="client", html=True), name="client")

@app.get("/", response_class=HTMLResponse)
async def root_index():
    return HTMLResponse((open("client/index.html", "r", encoding="utf-8").read()))

@app.post("/v1/voice/session")
async def create_ephemeral_session(request: Request):
    """
    Create an ephemeral Realtime session token.
    The exact REST path and JSON shape can change; consult your provider docs.
    We POST to /v1/realtime/sessions with an Authorization: Bearer <server-key>.
    The response should include a 'client_secret' or similar token used by the browser
    to initiate a WebRTC session directly with the model.
    """
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured")

    req_json = await request.json() if request.headers.get("content-type","").startswith("application/json") else {}
    # You can pass voice, instructions, tool definitions, etc. from the client or set defaults here.
    voice = req_json.get("voice", "verse")  # example voice name
    instructions = req_json.get("instructions", "You are a helpful real-time voice assistant. Keep responses brief.")
    # Example: tool/function schemas could be injected here if needed.
    # tools = [...]

    payload = {
        "model": MODEL,
        "voice": voice,
        "instructions": instructions,
        # "tools": tools,
        # "modalities": ["audio","text"],  # provider-specific
    }

    # NOTE: Endpoint path and fields may evolve; verify with your provider's docs.
    url = f"{OPENAI_BASE_URL}/v1/realtime/sessions"

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            # Bubble up useful info for debugging
            try:
                data = r.json()
            except Exception:
                data = {"error": r.text}
            raise HTTPException(status_code=r.status_code, detail=data)

        data = r.json()

    # Attach a default rtc_url for WebRTC SDP exchange if the provider didn't include it.
    try:
        has_rtc = any(k in data for k in ("rtc_url","url","web_rtc_url","webrtc_url"))
    except Exception:
        has_rtc = False
    if not has_rtc:
        # Standard WebRTC SDP HTTP endpoint (check your provider docs for the latest path)
        data["rtc_url"] = f"{OPENAI_BASE_URL}/v1/realtime?model={MODEL}"

    # Commonly, providers return something like: {"client_secret": {"value": "..."}}
    # We'll forward the whole body to the browser.
    return JSONResponse(data)


# --- Example "remote tools" HTTP endpoints (MCP-like) ---
from .tools import router as tools_router
app.include_router(tools_router, prefix="/v1/tools", tags=["tools"])


# --- Optional: Twilio/CPaaS SIP stubs (NOT enabled by default) ---
# from .sip_webhooks import router as sip_router
# app.include_router(sip_router, prefix="/v1/sip", tags=["sip"])

# -------------------- FUNCTION-CALLING REGISTRY (server-executed) --------------------
# This pattern assumes you will proxy tool calls via your server.
# In a pure browser→provider WebRTC flow, tool calls are negotiated over the data channel.
# For now we expose an HTTP endpoint so a Realtime session (or your client) can POST tool calls
# to be executed on the server (which is safer for secrets and data access).

from fastapi import Body

# Simple registry: name -> callable
FUNCTION_REGISTRY = {}

def tool(name):
    def _wrap(fn):
        FUNCTION_REGISTRY[name] = fn
        return fn
    return _wrap

# Example tool: create a ticket (re-using the stub logic)
@tool("create_ticket")
def tool_create_ticket(email: str, subject: str, description: str, priority: str = "normal"):
    fake_id = "TCK-" + str(abs(hash((email, subject))))[:8]
    return {"ticket_id": fake_id, "priority": priority}

# Example tool: lookup order
@tool("lookup_order")
def tool_lookup_order(order_id: str):
    return {
        "order_id": order_id,
        "status": "shipped",
        "carrier": "DHL",
        "eta": "2025-09-20",
    }

@app.post("/v1/voice/tools/call")
async def call_tool(payload: dict = Body(...)):
    """
    Execute a tool by name with JSON arguments.
    Example body:
      {"name": "create_ticket", "arguments": {"email":"u@x.com","subject":"Help","description":"..."}}
    """
    name = payload.get("name")
    args = payload.get("arguments", {}) or {}
    if name not in FUNCTION_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {name}")
    try:
        result = FUNCTION_REGISTRY[name](**args)
        return JSONResponse({"ok": True, "result": result})
    except TypeError as te:
        raise HTTPException(status_code=400, detail=f"Bad arguments: {te}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Tool JSON schema (announce to your client or model init)
TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "create_ticket",
        "description": "Create a support ticket for the user.",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
                "subject": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string", "enum": ["low","normal","high","urgent"]}
            },
            "required": ["email","subject","description"]
        }
    },
    {
        "type": "function",
        "name": "lookup_order",
        "description": "Get shipment status for an order id.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"}
            },
            "required": ["order_id"]
        }
    }
]

@app.get("/v1/voice/tools/schema")
async def get_tool_schemas():
    return JSONResponse({"tools": TOOL_SCHEMAS})
