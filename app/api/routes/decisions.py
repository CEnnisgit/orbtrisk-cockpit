from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import auth, models, schemas
from app.database import get_db
from app.services import audit

router = APIRouter()


@router.post("/events/{event_id}/decisions", response_model=schemas.DecisionOut)
def create_decision(request: Request, event_id: int, payload: schemas.DecisionCreate, db: Session = Depends(get_db)):
    auth.require_business(request)
    event = db.get(models.ConjunctionEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    decision = models.Decision(
        event_id=event_id,
        action=payload.action,
        approved_by=payload.approved_by,
        approved_at=payload.approved_at,
        rationale_text=payload.rationale_text,
        decision_driver=payload.decision_driver,
        assumption_notes=payload.assumption_notes,
        override_reason=payload.override_reason,
        checklist_json=payload.checklist_json,
        status_after=payload.status_after or "closed",
    )
    db.add(decision)
    db.flush()

    event.status = decision.status_after

    audit.append_audit_log(db, "decision", decision.id)
    db.commit()

    return decision
