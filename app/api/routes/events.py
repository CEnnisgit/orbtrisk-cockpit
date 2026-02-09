import io
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from fpdf import FPDF
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
    geometry = db.get(models.EventGeometry, event_id)

    return schemas.EventDetailOut(
        event=schemas.ConjunctionEventOut.model_validate(event),
        risk=schemas.RiskAssessmentOut.model_validate(risk_assessment) if risk_assessment else None,
        maneuvers=[schemas.ManeuverOptionOut.model_validate(m) for m in maneuvers],
        decision=schemas.DecisionOut.model_validate(decision) if decision else None,
        geometry=schemas.EventGeometryOut.model_validate(geometry) if geometry else None,
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


@router.get("/events/{event_id}/report")
def event_report(event_id: int, format: str = "pdf", db: Session = Depends(get_db)):
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
    geometry = db.get(models.EventGeometry, event_id)
    space_object = db.get(models.SpaceObject, event.space_object_id) if event.space_object_id else None

    runbook = None
    if risk_assessment:
        band = "low"
        if risk_assessment.risk_score >= 0.7:
            band = "high"
        elif risk_assessment.risk_score >= 0.4:
            band = "medium"
        runbook = (
            db.query(models.Runbook)
            .filter(models.Runbook.risk_band == band)
            .order_by(models.Runbook.id.desc())
            .first()
        )

    if format != "pdf":
        raise HTTPException(status_code=400, detail="format must be pdf")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", size=14)
    pdf.cell(0, 10, f"Conjunction Report - Event #{event.id}", ln=True)

    pdf.set_font("Helvetica", size=10)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(0, 6, f"TCA: {event.tca.isoformat()}  Status: {event.status}")
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(
        0,
        6,
        f"Primary satellite_id: {event.satellite_id}  Secondary: {space_object.name if space_object else (event.space_object_id or '-')}",
    )
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(0, 6, f"Miss distance: {event.miss_distance:.3f} km  Rel. speed: {event.relative_velocity:.3f} km/s")

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", size=12)
    pdf.cell(0, 8, "Risk", ln=True)
    pdf.set_font("Helvetica", size=10)
    if risk_assessment:
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 6, f"PoC: {risk_assessment.poc:.6g}   Risk score: {risk_assessment.risk_score:.3f}")
        for key, value in (risk_assessment.components_json or {}).items():
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, 5, f"- {key}: {value}")
        drivers = (risk_assessment.sensitivity_json or {}).get("top_drivers") or []
        if drivers:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, 6, "Top drivers: " + ", ".join(drivers))
    else:
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 6, "No risk assessment available.")

    if geometry:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", size=12)
        pdf.cell(0, 8, "Geometry (at TCA)", ln=True)
        pdf.set_font("Helvetica", size=10)
        r = geometry.relative_position_km
        v = geometry.relative_velocity_km_s
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 6, f"Frame: {geometry.frame}")
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 6, f"Relative position (km): [{r[0]:.3f}, {r[1]:.3f}, {r[2]:.3f}]")
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 6, f"Relative velocity (km/s): [{v[0]:.3f}, {v[1]:.3f}, {v[2]:.3f}]")

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", size=12)
    pdf.cell(0, 8, "Recommended Maneuvers", ln=True)
    pdf.set_font("Helvetica", size=10)
    if maneuvers:
        for option in maneuvers[:5]:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(
                0,
                6,
                f"- dV={option.delta_v * 1000.0:.2f} m/s  window=[{option.time_window_start.isoformat()} .. {option.time_window_end.isoformat()}]  risk_after={option.risk_after:.3f}  fuel={option.fuel_cost:.2f} m/s eq"
                + ("  (recommended)" if option.is_recommended else ""),
            )
    else:
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 6, "No maneuvers generated.")

    if decision:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", size=12)
        pdf.cell(0, 8, "Decision", ln=True)
        pdf.set_font("Helvetica", size=10)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 6, f"Action: {decision.action}  Approved by: {decision.approved_by}  At: {decision.approved_at.isoformat()}")
        if decision.decision_driver:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, 6, f"Primary driver: {decision.decision_driver}")
        if decision.assumption_notes:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, 6, f"Assumptions: {decision.assumption_notes}")
        if decision.override_reason:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, 6, f"Override: {decision.override_reason}")
        if decision.rationale_text:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, 6, f"Rationale: {decision.rationale_text}")

    if runbook:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", size=12)
        pdf.cell(0, 8, "Runbook", ln=True)
        pdf.set_font("Helvetica", size=10)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 6, runbook.template_name)
        for idx, step in enumerate(runbook.steps_json or [], start=1):
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, 5, f"{idx}. {step}")

    pdf_out = pdf.output(dest="S")
    pdf_bytes = pdf_out.encode("latin-1") if isinstance(pdf_out, str) else bytes(pdf_out)
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=event_{event.id}_report.pdf"},
    )


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
