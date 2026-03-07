import os
import json
import typing as t
import asyncio

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import httpx

from website_index import build_index, refresh_loop, load_index  

print("Loading environment variables...")
load_dotenv()
print("Environment variables loaded.")

BASE_DIR = os.path.dirname(__file__)
CLIENT_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "client"))

print("RUNNING APP FROM:", __file__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com")
MODEL = os.getenv("MODEL", "gpt-realtime")

print("AFTER LOAD_DOTENV")
print("OPENAI_API_KEY set:", bool(OPENAI_API_KEY))

CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:8000").split(",")
    if o.strip()
]

app = FastAPI(title="voice-agent-realtime-mcp-sip")
print("APP object created")

@app.get("/health")
def health():
    return {"status": "ok"}

Uncomment if the startup tasks are needed
 @app.on_event("startup")
 async def startup():
     print("Startup entered")
     if not load_index():
         asyncio.create_task(build_index())
     asyncio.create_task(refresh_loop())
     print("Startup tasks scheduled")

# ----- Basic CORS for local dev -----------------------------------
print("Initializing CORS middleware...")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
print("CORS middleware initialized.")

# Serve the client files -------------------------------------
print("Mounting static files...")
app.mount("/client", StaticFiles(directory=CLIENT_DIR, html=True), name="client")
print("Static files mounted.")

@app.get("/", response_class=HTMLResponse)
async def root_index():
    with open(os.path.join(CLIENT_DIR, "index.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.post("/v1/voice/session")
async def create_ephemeral_session(request: Request):
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured")

    req_json = await request.json() if request.headers.get("content-type","").startswith("application/json") else {}
    
    voice = req_json.get("voice", "verse")  # example voice name
    
    instructions = """
    You are a voice assistant for a website.
    You must answer ONLY using information retrieved from your knowledge base.
    
    Always call the tool 'search_knowledge' before answering questions.
    If the information cannot be found in the website knowledge base, say:
    "Sorry, I could not find that information that information."

    Do not invent answers. """
        
    tools = [
        {
            "type": "function",
            "name": "search_knowledge",
            "description": "Search the company website knowledge base",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"}
                },
                "required": ["query"]
            }
       }  
    ]

    payload = {
        "model": MODEL,
        "voice": voice,
        "instructions": instructions,
        "tools": tools,
        "turn_detection": {
            "type": "server_vad"
         }
    }

    url = f"{OPENAI_BASE_URL}/v1/realtime/sessions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    print("Making request to OpenAI API...")
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
        has_rtc = any(k in data for k in ("rtc_url","url","web_rtc_url","webrtc_url"))
    except Exception:
        has_rtc = False
    if not has_rtc:
        data["rtc_url"] = f"{OPENAI_BASE_URL}/v1/realtime?model={MODEL}"

    return JSONResponse(data)

# --- Example "remote tools" HTTP endpoints (MCP-like) ---
from tools import router as tools_router  # TO KEEP AFTER TESTING
app.include_router(tools_router, prefix="/v1/tools", tags=["tools"])  # TO KEEP AFTER TESTING

# --- Optional: Twilio/CPaaS SIP stubs (NOT enabled by default) ---
# from sip_webhooks import router as sip_router
# app.include_router(sip_router, prefix="/v1/sip", tags=["sip"])

# -------------------- FUNCTION-CALLING REGISTRY (server-executed) --------------------
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
    return {
        "order_id": order_id,
        "status": "shipped",
        "carrier": "DHL",
        "eta": "2025-09-20",
    }

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
        raise HTTPException(status_code=500, detail=str(e))

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
