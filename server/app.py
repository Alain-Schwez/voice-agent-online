import os
import logging
import asyncio
from typing import Any, Dict

from fastapi import FastAPI, Request, HTTPException, Body
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import httpx

# --- Basic logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

# --- Load env (lightweight) ---
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com")
MODEL = os.getenv("MODEL", "gpt-realtime")
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:8000").split(",") if o.strip()]

logger.info("Environment loaded. OPENAI_API_KEY set: %s", bool(OPENAI_API_KEY))

# --- FastAPI app (minimal at import time) ---
app = FastAPI(title="voice-agent-realtime-mcp-sip")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files using relative path (simpler in many deploy layouts)
app.mount("/client", StaticFiles(directory="client", html=True), name="client")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def root_index():
    # Keep this simple and lightweight
    try:
        with open("client/index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except Exception as e:
        logger.exception("Failed to read client/index.html")
        raise HTTPException(status_code=500, detail="index.html not available")


# --- Lightweight session endpoint (no heavy imports) ---
@app.post("/v1/voice/session")
async def create_ephemeral_session(request: Request):
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured")

    req_json = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    voice = req_json.get("voice", "verse")
    instructions = req_json.get("instructions", "You are a helpful real-time voice assistant. Keep responses brief.")

    payload: Dict[str, Any] = {
        "model": MODEL,
        "voice": voice,
        "instructions": instructions,
    }

    url = f"{OPENAI_BASE_URL}/v1/realtime/sessions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}

    logger.info("Requesting realtime session from provider...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            try:
                data = r.json()
            except Exception:
                data = {"error": r.text}
            raise HTTPException(status_code=r.status_code, detail=data)
        data = r.json()

    try:
        has_rtc = any(k in data for k in ("rtc_url", "url", "web_rtc_url", "webrtc_url"))
    except Exception:
        has_rtc = False
    if not has_rtc:
        data["rtc_url"] = f"{OPENAI_BASE_URL}/v1/realtime?model={MODEL}"

    return JSONResponse(data)


# --- Tool endpoints (light definitions only) ---
from fastapi import Body

FUNCTION_REGISTRY = {}

def tool(name):
    def _wrap(fn):
        FUNCTION_REGISTRY[name] = fn
        return fn
    return _wrap

@tool("create_ticket")
def tool_create_ticket(email: str, subject: str, description: str, priority: str = "normal"):
    fake_id = "TCK-" + str(abs(hash((email, subject))))[:8]
    return {"ticket_id": fake_id, "priority": priority}

@tool("lookup_order")
def tool_lookup_order(order_id: str):
    return {"order_id": order_id, "status": "shipped", "carrier": "DHL", "eta": "2025-09-20"}

@app.post("/v1/voice/tools/call")
async def call_tool(payload: dict = Body(...)):
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
        logger.exception("Tool execution error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/v1/voice/tools/schema")
async def get_tool_schemas():
    return JSONResponse({"tools": [
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
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"]
            }
        }
    ]})


# --- Warm-up / delayed heavy initialization ---
# This background task runs after the app has started and the server has bound to the port.
# It performs expensive imports and schedules long-running background loops without blocking startup.
async def warmup_tasks():
    await asyncio.sleep(1)  # slight delay to ensure server is fully up
    logger.info("Warmup: starting heavy initialization (imports, indexing, background loops)...")

    # Import website_index lazily to avoid import-time side-effects
    try:
        from website_index import build_index, refresh_loop, load_index  # local import
    except Exception:
        logger.exception("Failed to import website_index; skipping warmup indexing.")
        return

    # Attempt to load index (fast path). If not present, schedule build in background.
    try:
        loaded = False
        try:
            loaded = load_index()
        except Exception:
            logger.exception("load_index() failed or raised an exception.")
            loaded = False

        if not loaded:
            logger.info("Index not loaded; scheduling build_index() as background task.")
            # schedule build_index but don't await it here
            asyncio.create_task(build_index())

        # schedule refresh loop as background task
        asyncio.create_task(refresh_loop())
        logger.info("Warmup: scheduled build_index and refresh_loop as background tasks.")
    except Exception:
        logger.exception("Unexpected error during warmup tasks.")


@app.on_event("startup")
async def startup_event():
    # Schedule warmup in background; do not block startup
    logger.info("Startup event: scheduling warmup tasks")
    asyncio.create_task(warmup_tasks())


# If you run with `python app.py` locally, support uvicorn.run for convenience
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    logger.info("Starting uvicorn for local debug on port %s", port)
    uvicorn.run("app:app", host="0.0.0.0", port=port, log_level="info")
