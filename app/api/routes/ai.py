from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import auth, models
from app.database import get_db
from app.services import llm_client, catalog_sync

router = APIRouter()


class SummaryRequest(BaseModel):
    object_id: int
    style: Optional[str] = "concise"


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    object_id: int
    messages: List[ChatMessage] = Field(default_factory=list)


def _build_context(db: Session, object_id: int) -> dict:
    space_object = db.get(models.SpaceObject, object_id)
    if not space_object:
        raise HTTPException(status_code=404, detail="SpaceObject not found")
    tle = (
        db.query(models.TleRecord)
        .filter(models.TleRecord.space_object_id == space_object.id)
        .order_by(models.TleRecord.epoch.desc())
        .first()
    )
    source = db.get(models.Source, tle.source_id) if tle else None
    metadata = db.get(models.SpaceObjectMetadata, space_object.id)
    tier, age_hours = catalog_sync._quality_tier(source.name if source else None, tle.epoch if tle else None)
    context = {
        "object": {
            "id": space_object.id,
            "name": space_object.name,
            "norad_cat_id": space_object.norad_cat_id,
            "object_type": space_object.object_type,
            "international_designator": space_object.international_designator,
            "is_operator_asset": space_object.is_operator_asset,
        },
        "tle": {
            "epoch": tle.epoch.isoformat() if tle else None,
            "source": source.name if source else None,
            "age_hours": age_hours,
            "quality_tier": tier,
        },
        "satcat": metadata.satcat_json if metadata else {},
    }
    return context


def _citations(context: dict) -> List[str]:
    sources = set()
    tle_source = context.get("tle", {}).get("source") or ""
    if "space-track" in tle_source:
        sources.add("Space-Track")
    if "celestrak" in tle_source:
        sources.add("CelesTrak")
    if context.get("satcat"):
        sources.add("CelesTrak SATCAT")
    return sorted(sources)


@router.post("/ai/object-summary")
def object_summary(request: Request, payload: SummaryRequest, db: Session = Depends(get_db)):
    auth.require_business(request)
    context = _build_context(db, payload.object_id)
    try:
        data = llm_client.generate_summary(context)
    except llm_client.LlmError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {
        "summary": data.get("summary", ""),
        "key_facts": data.get("key_facts", []),
        "citations": _citations(context),
    }


@router.post("/ai/object-chat")
def object_chat(request: Request, payload: ChatRequest, db: Session = Depends(get_db)):
    auth.require_business(request)
    context = _build_context(db, payload.object_id)
    messages = [msg.model_dump() for msg in payload.messages]
    try:
        reply = llm_client.chat(context, messages)
    except llm_client.LlmError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"reply": reply, "citations": _citations(context)}
