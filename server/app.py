import os
import logging
import asyncio
from typing import Any, Dict

from fastapi import FastAPI, Request, HTTPException, Body
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

# Load env
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com")
MODEL = os.getenv("MODEL", "gpt-realtime")
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:8000").split(",") if o.strip()]

logger.info("Environment loaded. OPENAI_API_KEY set: %s", bool(OPENAI_API_KEY))

app = FastAPI(title="voice-agent-realtime-mcp-sip")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files only if directory exists (prevents import-time crash)
CLIENT_DIR = os.path.join(os.path.dirname(__file__), "client")
if os.path.isdir(CLIENT_DIR):
    from fastapi.staticfiles import StaticFiles
    app.mount("/client", StaticFiles(directory=CLIENT_DIR, html=True), name="client")
    logger.info("Mounted static client from %s", CLIENT_DIR)
else:
    logger.warning("Client directory not found at %s — skipping StaticFiles mount", CLIENT_DIR)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/", response_class=HTMLResponse)
async def root_index():
    index_path = os.path.join(CLIENT_DIR, "index.html")
    if os.path.isfile(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<html><body><h1>App running (no client files)</h1></body></html>")

@app.post("/v1/voice/session")
async def create_ephemeral_session(request: Request):
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured")

    req_json = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    voice = req_json.get("voice", "verse")
    instructions = req_json.get("instructions", "You are a helpful real-time voice assistant. Keep responses brief.")

    payload: Dict[str, Any] = {"model": MODEL, "voice": voice, "instructions": instructions}
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

# Simple tool registry
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

# Warmup: lazy import of heavy modules after startup
async def warmup_tasks():
    await asyncio.sleep(1)
    if os.getenv("ENABLE_INDEXING", "false").lower() not in ("1", "true", "yes"):
        logger.info("Indexing disabled via ENABLE_INDEXING env (default false); skipping warmup")
        return

    logger.info("Warmup: starting heavy initialization...")
    try:
        from website_index import build_index, refresh_loop, load_index
    except Exception as e:
        # Log missing deps (e.g., sentence-transformers) clearly
        logger.exception("website_index import failed — skipping indexing warmup: %s", e)
        return

    try:
        loaded = False
        try:
            loaded = load_index()
        except Exception:
            logger.exception("load_index() failed")
            loaded = False

        if not loaded:
            asyncio.create_task(build_index())
        asyncio.create_task(refresh_loop())
        logger.info("Warmup: scheduled background index tasks")
    except Exception:
        logger.exception("Unexpected error during warmup")

@app.on_event("startup")
async def startup_event():
    logger.info("Startup event: scheduling warmup tasks (if enabled)")
    # schedule but only does heavy work if ENABLE_INDEXING is true
    asyncio.create_task(warmup_tasks())

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    logger.info("Starting uvicorn locally on port %s", port)
    uvicorn.run("app:app", host="0.0.0.0", port=port, log_level="info")
