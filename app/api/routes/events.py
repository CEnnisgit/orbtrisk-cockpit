from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db

router = APIRouter()


@router.get("/events", response_model=list[schemas.EventListItem])
def list_events(
    since: Optional[datetime] = Query(default=None),
    status: Optional[str] = Query(default=None),
    risk_band: Optional[str] = Query(default=None),
    window: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    query = db.query(models.ConjunctionEvent)
    if since:
        query = query.filter(models.ConjunctionEvent.tca >= since)
    if status:
        query = query.filter(models.ConjunctionEvent.status == status)
    if window in {"24h", "72h", "7d"}:
        hours = {"24h": 24, "72h": 72, "7d": 168}[window]
        cutoff = datetime.utcnow() + timedelta(hours=hours)
        query = query.filter(models.ConjunctionEvent.tca <= cutoff)
    events = query.all()

    response: list[schemas.EventListItem] = []
    for event in events:
        risk_assessment = (
            db.query(models.RiskAssessment)
            .filter(models.RiskAssessment.event_id == event.id)
            .order_by(models.RiskAssessment.id.desc())
            .first()
        )
        risk_score = risk_assessment.risk_score if risk_assessment else None
        if risk_band:
            if risk_score is None:
                continue
            if risk_band == "high" and risk_score < 0.7:
                continue
            if risk_band == "medium" and not (0.4 <= risk_score < 0.7):
                continue
            if risk_band == "low" and risk_score >= 0.4:
                continue
        response.append(
            schemas.EventListItem(
                event=schemas.ConjunctionEventOut.model_validate(event),
                risk_score=risk_score,
            )
        )

    response.sort(
        key=lambda item: (
            (item.event.tca - datetime.utcnow()).total_seconds(),
            -(item.risk_score or 0.0),
        )
    )
    return response


@router.get("/events/{event_id}", response_model=schemas.EventDetailOut)
def get_event(event_id: int, db: Session = Depends(get_db)):
    event = db.get(models.ConjunctionEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    risk_assessment = (
        db.query(models.RiskAssessment)
        .filter(models.RiskAssessment.event_id == event_id)
        .order_by(models.RiskAssessment.id.desc())
        .first()
    )
    maneuvers = (
        db.query(models.ManeuverOption)
        .filter(models.ManeuverOption.event_id == event_id)
        .order_by(models.ManeuverOption.risk_after.asc())
        .all()
    )
    decision = (
        db.query(models.Decision)
        .filter(models.Decision.event_id == event_id)
        .order_by(models.Decision.id.desc())
        .first()
    )

    return schemas.EventDetailOut(
        event=schemas.ConjunctionEventOut.model_validate(event),
        risk=schemas.RiskAssessmentOut.model_validate(risk_assessment) if risk_assessment else None,
        maneuvers=[schemas.ManeuverOptionOut.model_validate(m) for m in maneuvers],
        decision=schemas.DecisionOut.model_validate(decision) if decision else None,
    )


@router.get("/events/{event_id}/recommendations", response_model=list[schemas.ManeuverOptionOut])
def get_recommendations(event_id: int, db: Session = Depends(get_db)):
    event = db.get(models.ConjunctionEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    maneuvers = (
        db.query(models.ManeuverOption)
        .filter(models.ManeuverOption.event_id == event_id)
        .order_by(models.ManeuverOption.risk_after.asc())
        .all()
    )
    return [schemas.ManeuverOptionOut.model_validate(m) for m in maneuvers]


@router.post("/events/{event_id}/status")
def update_status(event_id: int, status: str, db: Session = Depends(get_db)):
    event = db.get(models.ConjunctionEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if status not in {"open", "in_review", "closed"}:
        raise HTTPException(status_code=400, detail="Invalid status")
    event.status = status
    db.commit()
    return {"event_id": event_id, "status": status}
