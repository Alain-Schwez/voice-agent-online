from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional

from website_index import search

router = APIRouter()

# --------------------------------------------------
# Ticket creation tool
# --------------------------------------------------

class CreateTicketBody(BaseModel):
    email: str = Field(..., example="user@example.com")
    subject: str
    description: str
    priority: str = Field("normal", description="low|normal|high|urgent")


@router.post("/create_ticket")
async def create_ticket(body: CreateTicketBody):

    # In real life this would call Zendesk/Jira/etc
    fake_id = "TCK-" + str(abs(hash((body.email, body.subject))))[:8]

    return JSONResponse({
        "ok": True,
        "ticket_id": fake_id
    })


# --------------------------------------------------
# Order lookup tool
# --------------------------------------------------

class OrderLookupBody(BaseModel):
    order_id: str


@router.post("/lookup_order")
async def lookup_order(body: OrderLookupBody):

    # Stub example
    return JSONResponse({
        "ok": True,
        "order": {
            "order_id": body.order_id,
            "status": "shipped",
            "carrier": "DHL",
            "eta": "2025-09-20"
        }
    })


# --------------------------------------------------
# Website knowledge search tool
# --------------------------------------------------

class KnowledgeSearchBody(BaseModel):
    query: str


@router.post("/search_knowledge")
async def search_knowledge(body: KnowledgeSearchBody):

    results = search(body.query)

    return JSONResponse({
        "ok": True,
        "content": results
    })
