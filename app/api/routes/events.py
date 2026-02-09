import io
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from fpdf import FPDF
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.settings import settings
from app.services import conjunction, propagation
from app.services.state_sources import StateEstimate, build_state_estimate

router = APIRouter()


def _build_state_estimates(
    db: Session, update: models.ConjunctionEventUpdate
) -> tuple[Optional[StateEstimate], Optional[StateEstimate]]:
    if not update.primary_orbit_state_id or not update.secondary_orbit_state_id:
        return None, None
    primary_state = db.get(models.OrbitState, int(update.primary_orbit_state_id))
    secondary_state = db.get(models.OrbitState, int(update.secondary_orbit_state_id))
    if primary_state is None or secondary_state is None:
        return None, None
    return build_state_estimate(db, primary_state), build_state_estimate(db, secondary_state)


def _relative_state_at(primary: StateEstimate, secondary: StateEstimate, t: datetime) -> tuple[list[float], list[float]]:
    s1 = primary.propagate(t)
    s2 = secondary.propagate(t)
    r1 = propagation.position_from_state(s1)
    v1 = propagation.velocity_from_state(s1)
    r2 = propagation.position_from_state(s2)
    v2 = propagation.velocity_from_state(s2)
    r_rel = [float(r2[i] - r1[i]) for i in range(3)]
    v_rel = [float(v2[i] - v1[i]) for i in range(3)]
    return r_rel, v_rel


@router.get("/events", response_model=list[schemas.EventListItem])
def list_events(
    since: Optional[datetime] = Query(default=None),
    status: Optional[str] = Query(default=None),
    risk_band: Optional[str] = Query(default=None),
    window: Optional[str] = Query(default=None),
    active_only: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    query = db.query(models.ConjunctionEvent)
    if since:
        query = query.filter(models.ConjunctionEvent.tca >= since)
    if status:
        query = query.filter(models.ConjunctionEvent.status == status)
    if active_only:
        query = query.filter(models.ConjunctionEvent.is_active.is_(True))
    if window in {"24h", "72h", "7d"}:
        hours = {"24h": 24, "72h": 72, "7d": 168}[window]
        cutoff = datetime.utcnow() + timedelta(hours=hours)
        query = query.filter(models.ConjunctionEvent.tca <= cutoff)
    events = query.all()

    response: list[schemas.EventListItem] = []
    now = datetime.utcnow()
    for event in events:
        if risk_band:
            if risk_band not in {"high", "watch", "low"}:
                continue
            if event.risk_tier != risk_band:
                continue
        response.append(
            schemas.EventListItem(
                event=schemas.ConjunctionEventOut.model_validate(event),
                time_to_tca_hours=(event.tca - now).total_seconds() / 3600.0,
            )
        )

    response.sort(
        key=lambda item: (
            (item.event.tca - now).total_seconds(),
            -(item.event.risk_score or 0.0),
        )
    )
    return response


@router.get("/events/{event_id}", response_model=schemas.EventDetailOut)
def get_event(event_id: int, db: Session = Depends(get_db)):
    event = db.get(models.ConjunctionEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    updates = (
        db.query(models.ConjunctionEventUpdate)
        .filter(models.ConjunctionEventUpdate.event_id == event_id)
        .order_by(models.ConjunctionEventUpdate.computed_at.desc())
        .limit(50)
        .all()
    )
    current_update = None
    if event.current_update_id:
        current_update = db.get(models.ConjunctionEventUpdate, int(event.current_update_id))

    decision = (
        db.query(models.Decision)
        .filter(models.Decision.event_id == event_id)
        .order_by(models.Decision.id.desc())
        .first()
    )

    cdm_records = (
        db.query(models.CdmRecord)
        .filter(models.CdmRecord.event_id == event_id)
        .order_by(models.CdmRecord.id.desc())
        .all()
    )

    return schemas.EventDetailOut(
        event=schemas.ConjunctionEventOut.model_validate(event),
        current_update=schemas.ConjunctionEventUpdateOut.model_validate(current_update)
        if current_update
        else None,
        updates=[schemas.ConjunctionEventUpdateOut.model_validate(u) for u in updates],
        decision=schemas.DecisionOut.model_validate(decision) if decision else None,
        cdm_records=[schemas.CdmRecordOut.model_validate(r) for r in cdm_records],
    )


@router.get("/events/{event_id}/series")
def event_series(
    event_id: int,
    update_id: Optional[int] = None,
    window_hours: Optional[float] = None,
    step_seconds: Optional[int] = None,
    db: Session = Depends(get_db),
):
    event = db.get(models.ConjunctionEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if update_id is None:
        update_id = int(event.current_update_id) if event.current_update_id else None
    if update_id is None:
        raise HTTPException(status_code=400, detail="No update_id available")
    update = db.get(models.ConjunctionEventUpdate, int(update_id))
    if not update or update.event_id != event.id:
        raise HTTPException(status_code=404, detail="Update not found")

    window_h = float(window_hours) if window_hours is not None else float(settings.series_window_hours)
    step_s = int(step_seconds) if step_seconds is not None else int(settings.series_step_seconds)
    window_h = max(0.5, min(24.0, window_h))
    step_s = max(10, min(600, step_s))

    t_start = update.tca - timedelta(hours=window_h)
    t_end = update.tca + timedelta(hours=window_h)
    times = []
    miss = []

    primary_est, secondary_est = _build_state_estimates(db, update)
    if primary_est is not None and secondary_est is not None:
        current = t_start
        while current <= t_end:
            try:
                r_rel, _v_rel = _relative_state_at(primary_est, secondary_est, current)
            except Exception:
                times = []
                miss = []
                break
            miss.append(float(propagation.norm(r_rel)))
            times.append(current.isoformat())
            current += timedelta(seconds=step_s)

    if not times:
        # Fallback: use a simple linearized relative motion model based on stored state at TCA.
        current = t_start
        while current <= t_end:
            dt = (current - update.tca).total_seconds()
            r = update.r_rel_eci_km
            v = update.v_rel_eci_km_s
            if not (isinstance(r, list) and isinstance(v, list) and len(r) == 3 and len(v) == 3):
                break
            r_t = [float(r[i]) + float(v[i]) * dt for i in range(3)]
            miss.append(float(propagation.norm(r_t)))
            times.append(current.isoformat())
            current += timedelta(seconds=step_s)

    return {"event_id": event_id, "update_id": update_id, "times": times, "miss_distance_km": miss}


@router.get("/events/{event_id}/rtn-series")
def event_rtn_series(
    event_id: int,
    update_id: Optional[int] = None,
    window_hours: Optional[float] = None,
    step_seconds: Optional[int] = None,
    db: Session = Depends(get_db),
):
    event = db.get(models.ConjunctionEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if update_id is None:
        update_id = int(event.current_update_id) if event.current_update_id else None
    if update_id is None:
        raise HTTPException(status_code=400, detail="No update_id available")
    update = db.get(models.ConjunctionEventUpdate, int(update_id))
    if not update or update.event_id != event.id:
        raise HTTPException(status_code=404, detail="Update not found")

    window_h = float(window_hours) if window_hours is not None else float(settings.series_window_hours)
    step_s = int(step_seconds) if step_seconds is not None else int(settings.series_step_seconds)
    window_h = max(0.5, min(24.0, window_h))
    step_s = max(10, min(600, step_s))

    t_start = update.tca - timedelta(hours=window_h)
    t_end = update.tca + timedelta(hours=window_h)
    times: list[str] = []
    r_vals: list[float] = []
    t_vals: list[float] = []
    n_vals: list[float] = []

    primary_est, secondary_est = _build_state_estimates(db, update)
    if primary_est is not None and secondary_est is not None:
        try:
            s1_tca = primary_est.propagate(update.tca)
            basis = conjunction.rtn_basis_from_primary_state(
                propagation.position_from_state(s1_tca),
                propagation.velocity_from_state(s1_tca),
            )
        except Exception:
            basis = None

        if basis is not None:
            current = t_start
            while current <= t_end:
                try:
                    r_rel, _v_rel = _relative_state_at(primary_est, secondary_est, current)
                except Exception:
                    times = []
                    r_vals = []
                    t_vals = []
                    n_vals = []
                    break
                vec = conjunction.project_to_rtn(r_rel, basis)
                times.append(current.isoformat())
                r_vals.append(float(vec[0]))
                t_vals.append(float(vec[1]))
                n_vals.append(float(vec[2]))
                current += timedelta(seconds=step_s)

    if not times:
        # Fallback: linear model in RTN using the stored projection (available for TLE screening updates).
        r0 = update.r_rel_rtn_km
        v0 = update.v_rel_rtn_km_s
        if not (isinstance(r0, list) and isinstance(v0, list) and len(r0) == 3 and len(v0) == 3):
            return {"event_id": event_id, "update_id": update_id, "times": [], "r": [], "t": [], "n": []}

        current = t_start
        while current <= t_end:
            dt = (current - update.tca).total_seconds()
            vec = [float(r0[i]) + float(v0[i]) * dt for i in range(3)]
            times.append(current.isoformat())
            r_vals.append(vec[0])
            t_vals.append(vec[1])
            n_vals.append(vec[2])
            current += timedelta(seconds=step_s)

    return {"event_id": event_id, "update_id": update_id, "times": times, "r": r_vals, "t": t_vals, "n": n_vals}


@router.get("/events/{event_id}/report")
def event_report(event_id: int, format: str = "pdf", db: Session = Depends(get_db)):
    event = db.get(models.ConjunctionEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    space_object = db.get(models.SpaceObject, event.space_object_id) if event.space_object_id else None

    updates = (
        db.query(models.ConjunctionEventUpdate)
        .filter(models.ConjunctionEventUpdate.event_id == event_id)
        .order_by(models.ConjunctionEventUpdate.computed_at.desc())
        .limit(5)
        .all()
    )
    current_update = db.get(models.ConjunctionEventUpdate, int(event.current_update_id)) if event.current_update_id else None

    decision = (
        db.query(models.Decision)
        .filter(models.Decision.event_id == event_id)
        .order_by(models.Decision.id.desc())
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
    pdf.multi_cell(0, 6, f"TCA: {event.tca.isoformat()}  Status: {event.status}  Active: {bool(event.is_active)}")
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
    pdf.cell(0, 8, "Screening Risk", ln=True)
    pdf.set_font("Helvetica", size=10)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(
        0,
        6,
        f"Tier: {event.risk_tier.upper()}   Score: {event.risk_score:.3f}   Confidence: {event.confidence_label} ({event.confidence_score:.3f})",
    )
    if current_update and current_update.drivers_json:
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 6, "Top drivers: " + ", ".join(current_update.drivers_json))

    if updates:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", size=12)
        pdf.cell(0, 8, "Recent Evolution", ln=True)
        pdf.set_font("Helvetica", size=9)
        for upd in updates:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(
                0,
                5,
                f"{upd.computed_at.isoformat()} | miss={upd.miss_distance_km:.3f} km | tier={upd.risk_tier} | conf={upd.confidence_label}",
            )

    cdm_records = (
        db.query(models.CdmRecord)
        .filter(models.CdmRecord.event_id == event_id)
        .order_by(models.CdmRecord.id.desc())
        .limit(3)
        .all()
    )
    if cdm_records:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", size=12)
        pdf.cell(0, 8, "CDM Attachments", ln=True)
        pdf.set_font("Helvetica", size=9)
        for rec in cdm_records:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, 5, f"CDM #{rec.id} | tca={rec.tca.isoformat()} | ingested={rec.created_at.isoformat()}")

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
