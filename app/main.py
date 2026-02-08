from datetime import datetime, timedelta
from typing import Dict, Optional
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.gzip import GZipMiddleware
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.routes import ingestion, satellites, events, decisions, audit, webhooks, catalog, ai, solar
from app.database import get_db, init_db
from app import models
from app.services import demo, conjunction, risk, maneuver, propagation, catalog_sync
from app.settings import settings

app = FastAPI(title="Space Risk & Collision Avoidance MVP")
app.add_middleware(GZipMiddleware, minimum_size=500)

app.include_router(ingestion.router, tags=["ingestion"])
app.include_router(satellites.router, tags=["satellites"])
app.include_router(events.router, tags=["events"])
app.include_router(decisions.router, tags=["decisions"])
app.include_router(audit.router, tags=["audit"])
app.include_router(webhooks.router, tags=["webhooks"])
app.include_router(catalog.router, tags=["catalog"])
app.include_router(ai.router, tags=["ai"])
app.include_router(solar.router, tags=["solar"])

app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
def on_startup():
    init_db()
    db = next(get_db())
    try:
        demo.seed_runbooks(db)
        db.commit()
    finally:
        db.close()
    catalog_sync.start_scheduler()


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    satellite_count = db.query(models.Satellite).count()
    event_count = db.query(models.ConjunctionEvent).count()
    high_risk = (
        db.query(models.RiskAssessment)
        .filter(models.RiskAssessment.risk_score >= 0.7)
        .count()
    )
    catalog_count = (
        db.query(models.SpaceObject)
        .filter(models.SpaceObject.is_operator_asset.is_(False))
        .count()
    )
    last_sync = db.query(func.max(models.TleRecord.ingested_at)).scalar()
    recent_cutoff = datetime.utcnow() - timedelta(days=7)
    recent_events = (
        db.query(models.ConjunctionEvent)
        .filter(models.ConjunctionEvent.tca >= recent_cutoff)
        .order_by(models.ConjunctionEvent.tca.desc())
        .limit(10)
        .all()
    )
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "satellite_count": satellite_count,
            "event_count": event_count,
            "high_risk": high_risk,
            "catalog_count": catalog_count,
            "last_sync": last_sync,
            "recent_events": recent_events,
        },
    )


@app.post("/catalog/sync-ui")
def catalog_sync_ui(request: Request, db: Session = Depends(get_db)):
    catalog_sync.sync_catalog(db, manual=True)
    return RedirectResponse(url="/dashboard?synced=1", status_code=303)


@app.get("/events-ui", response_class=HTMLResponse)
def events_ui(
    request: Request,
    db: Session = Depends(get_db),
    status: Optional[str] = None,
    risk_band: Optional[str] = None,
    window: Optional[str] = None,
    event_id: Optional[int] = None,
):
    query = db.query(models.ConjunctionEvent)
    if status:
        query = query.filter(models.ConjunctionEvent.status == status)
    if window in {"24h", "72h", "7d"}:
        hours = {"24h": 24, "72h": 72, "7d": 168}[window]
        cutoff = datetime.utcnow() + timedelta(hours=hours)
        query = query.filter(models.ConjunctionEvent.tca <= cutoff)
    events = query.all()

    # Batch-load latest RiskAssessment per event (fix N+1)
    event_ids = [e.id for e in events]
    risk_map: Dict[int, models.RiskAssessment] = {}
    if event_ids:
        latest_risk_subq = (
            db.query(
                models.RiskAssessment.event_id,
                func.max(models.RiskAssessment.id).label("max_id"),
            )
            .filter(models.RiskAssessment.event_id.in_(event_ids))
            .group_by(models.RiskAssessment.event_id)
            .subquery()
        )
        latest_risks = (
            db.query(models.RiskAssessment)
            .join(latest_risk_subq, models.RiskAssessment.id == latest_risk_subq.c.max_id)
            .all()
        )
        risk_map = {r.event_id: r for r in latest_risks}

    # Batch-load SpaceObjects
    so_ids = {e.space_object_id for e in events if e.space_object_id}
    so_map: Dict[int, models.SpaceObject] = {}
    if so_ids:
        space_objects = db.query(models.SpaceObject).filter(models.SpaceObject.id.in_(so_ids)).all()
        so_map = {so.id: so for so in space_objects}

    # Batch-load Satellites for fallback object lookups
    sat_ids = {e.object_id for e in events if e.object_id and not e.space_object_id}
    sat_map: Dict[int, models.Satellite] = {}
    if sat_ids:
        sats = db.query(models.Satellite).filter(models.Satellite.id.in_(sat_ids)).all()
        sat_map = {s.id: s for s in sats}

    items = []
    for event in events:
        risk_assessment = risk_map.get(event.id)
        risk_score = risk_assessment.risk_score if risk_assessment else None
        band = None
        if risk_score is not None:
            if risk_score >= 0.7:
                band = "high"
            elif risk_score >= 0.4:
                band = "medium"
            else:
                band = "low"
        if risk_band:
            if risk_score is None or risk_band != band:
                continue
        space_object = so_map.get(event.space_object_id) if event.space_object_id else None
        object_name = None
        object_type = None
        if space_object:
            object_name = space_object.name
            object_type = space_object.object_type
        elif event.object_id:
            other_sat = sat_map.get(event.object_id)
            if other_sat:
                object_name = other_sat.name
                object_type = "operator"
        time_to_tca_hours = (event.tca - datetime.utcnow()).total_seconds() / 3600.0
        items.append(
            {
                "event": event,
                "risk_score": risk_score,
                "risk_band": band,
                "time_to_tca_hours": time_to_tca_hours,
                "object_name": object_name,
                "object_type": object_type,
            }
        )

    items.sort(
        key=lambda item: (
            item["time_to_tca_hours"],
            -(item["risk_score"] or 0.0),
        )
    )

    selected = None
    selected_risk = None
    selected_maneuvers = []
    selected_decision = None
    selected_runbook = None
    selected_object = None
    if event_id:
        selected = db.get(models.ConjunctionEvent, event_id)
        if selected:
            selected_risk = (
                db.query(models.RiskAssessment)
                .filter(models.RiskAssessment.event_id == event_id)
                .order_by(models.RiskAssessment.id.desc())
                .first()
            )
            selected_maneuvers = (
                db.query(models.ManeuverOption)
                .filter(models.ManeuverOption.event_id == event_id)
                .order_by(models.ManeuverOption.risk_after.asc())
                .all()
            )
            selected_decision = (
                db.query(models.Decision)
                .filter(models.Decision.event_id == event_id)
                .order_by(models.Decision.id.desc())
                .first()
            )
            if selected.space_object_id:
                selected_object = db.get(models.SpaceObject, selected.space_object_id)
            band = None
            if selected_risk:
                if selected_risk.risk_score >= 0.7:
                    band = "high"
                elif selected_risk.risk_score >= 0.4:
                    band = "medium"
                else:
                    band = "low"
            if band:
                selected_runbook = (
                    db.query(models.Runbook)
                    .filter(models.Runbook.risk_band == band)
                    .order_by(models.Runbook.id.desc())
                    .first()
                )

    filter_params = {"status": status, "risk_band": risk_band, "window": window}
    filter_query = urlencode({k: v for k, v in filter_params.items() if v})
    presets = {
        "High Risk": urlencode({"risk_band": "high"}),
        "Time-Critical": urlencode({"window": "24h"}),
        "Unreviewed": urlencode({"status": "open"}),
    }

    return templates.TemplateResponse(
        "events.html",
        {
            "request": request,
            "events": items,
            "filters": {"status": status, "risk_band": risk_band, "window": window},
            "filter_query": filter_query,
            "selected": selected,
            "selected_risk": selected_risk,
            "selected_maneuvers": selected_maneuvers,
            "selected_decision": selected_decision,
            "selected_runbook": selected_runbook,
            "selected_object": selected_object,
            "presets": presets,
        },
    )


@app.get("/", response_class=HTMLResponse)
def globe_ui(request: Request):
    return templates.TemplateResponse(
        "globe.html",
        {
            "request": request,
            "title": "3D Globe",
            "cesium_token": settings.cesium_ion_token or "",
            "cesium_night_asset_id": settings.cesium_night_asset_id,
        },
    )


@app.get("/events-ui/{event_id}", response_class=HTMLResponse)
def event_detail_ui(event_id: int, request: Request, db: Session = Depends(get_db)):
    event = db.get(models.ConjunctionEvent, event_id)
    if not event:
        return templates.TemplateResponse(
            "event_detail.html",
            {"request": request, "event": None},
        )
    risk = (
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
    space_object = None
    if event.space_object_id:
        space_object = db.get(models.SpaceObject, event.space_object_id)
    runbook = None
    if risk:
        band = "low"
        if risk.risk_score >= 0.7:
            band = "high"
        elif risk.risk_score >= 0.4:
            band = "medium"
        runbook = (
            db.query(models.Runbook)
            .filter(models.Runbook.risk_band == band)
            .order_by(models.Runbook.id.desc())
            .first()
        )
    return templates.TemplateResponse(
        "event_detail.html",
        {
            "request": request,
            "event": event,
            "risk": risk,
            "maneuvers": maneuvers,
            "decision": decision,
            "runbook": runbook,
            "space_object": space_object,
        },
    )



@app.post("/demo/seed")
async def seed_demo_data(db: Session = Depends(get_db)):
    demo.seed_demo(db)
    db.commit()
    return RedirectResponse(url="/dashboard?seeded=1", status_code=303)



@app.get("/satellites-ui", response_class=HTMLResponse)
def satellites_ui(request: Request, db: Session = Depends(get_db)):
    satellites = db.query(models.Satellite).order_by(models.Satellite.id.asc()).all()
    return templates.TemplateResponse(
        "satellites.html",
        {"request": request, "satellites": satellites},
    )


@app.post("/satellites-ui")
def satellites_create_ui(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(None),
    operator_id: str = Form(None),
    catalog_id: str = Form(None),
    orbit_regime: str = Form("LEO"),
    status: str = Form("active"),
):
    if not name:
        return RedirectResponse(url="/satellites-ui", status_code=303)
    satellite = models.Satellite(
        name=name,
        operator_id=operator_id,
        catalog_id=catalog_id,
        orbit_regime=orbit_regime,
        status=status,
    )
    db.add(satellite)
    db.commit()
    return RedirectResponse(url="/satellites-ui", status_code=303)


@app.get("/ingest-ui", response_class=HTMLResponse)
def ingest_ui(request: Request, db: Session = Depends(get_db)):
    satellites = db.query(models.Satellite).order_by(models.Satellite.id.asc()).all()
    return templates.TemplateResponse(
        "ingest.html",
        {"request": request, "satellites": satellites},
    )


@app.post("/ingest-ui")
def ingest_ui_post(
    request: Request,
    db: Session = Depends(get_db),
    satellite_id: int = Form(None),
    epoch: str = Form(None),
    state_vector: str = Form(None),
    confidence: float = Form(0.6),
    source_name: str = Form("public-tle"),
    source_type: str = Form("public"),
):
    if not satellite_id or not epoch or not state_vector:
        return RedirectResponse(url="/ingest-ui", status_code=303)

    satellite = db.get(models.Satellite, int(satellite_id))
    if not satellite:
        return RedirectResponse(url="/ingest-ui", status_code=303)

    source = (
        db.query(models.Source)
        .filter(models.Source.name == source_name)
        .filter(models.Source.type == source_type)
        .first()
    )
    if not source:
        source = models.Source(name=source_name, type=source_type)
        db.add(source)
        db.flush()

    space_object = None
    if satellite.catalog_id and str(satellite.catalog_id).isdigit():
        norad_id = int(str(satellite.catalog_id))
        space_object = (
            db.query(models.SpaceObject)
            .filter(models.SpaceObject.norad_cat_id == norad_id)
            .filter(models.SpaceObject.is_operator_asset.is_(True))
            .first()
        )
    if not space_object:
        space_object = (
            db.query(models.SpaceObject)
            .filter(models.SpaceObject.name == satellite.name)
            .filter(models.SpaceObject.is_operator_asset.is_(True))
            .first()
        )
    if not space_object:
        space_object = models.SpaceObject(
            norad_cat_id=int(satellite.catalog_id) if satellite.catalog_id and str(satellite.catalog_id).isdigit() else None,
            name=satellite.name,
            object_type="PAYLOAD",
            international_designator=None,
            source_id=source.id,
            is_operator_asset=True,
        )
        db.add(space_object)
        db.flush()

    vector = [float(x.strip()) for x in state_vector.split(",") if x.strip()]
    if len(vector) != 6:
        return RedirectResponse(url="/ingest-ui", status_code=303)

    orbit_state = models.OrbitState(
        satellite_id=satellite.id,
        space_object_id=space_object.id if space_object else None,
        epoch=datetime.fromisoformat(epoch.replace("Z", "+00:00")),
        state_vector=vector,
        covariance=propagation.default_covariance(source.type),
        source_id=source.id,
        confidence=confidence,
    )
    db.add(orbit_state)
    db.flush()

    events = conjunction.detect_events_for_state(db, orbit_state)
    for event in events:
        sigma = propagation.extract_sigma(orbit_state.covariance)
        poc, risk_score, components, sensitivity = risk.assess_event(event, sigma)
        db.add(
            models.RiskAssessment(
                event_id=event.id,
                poc=poc,
                risk_score=risk_score,
                components_json=components,
                sensitivity_json=sensitivity,
            )
        )
        options = maneuver.generate_options(event, risk_score)
        for option in options:
            db.add(
                models.ManeuverOption(
                    event_id=event.id,
                    delta_v=option["delta_v"],
                    time_window_start=option["time_window_start"],
                    time_window_end=option["time_window_end"],
                    risk_after=option["risk_after"],
                    fuel_cost=option["fuel_cost"],
                    is_recommended=option["is_recommended"],
                )
            )

    db.commit()
    return RedirectResponse(url="/events-ui", status_code=303)


@app.post("/events-ui/{event_id}/decide")
def decide_ui(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    action: str = Form("do_nothing"),
    approved_by: str = Form(None),
    rationale_text: str = Form(None),
    decision_driver: str = Form(None),
    assumption_notes: str = Form(None),
    override_reason: str = Form(None),
    checklist: Optional[list[str]] = Form(None),
):
    event = db.get(models.ConjunctionEvent, event_id)
    if not event or not approved_by:
        return RedirectResponse(url=f"/events-ui?event_id={event_id}", status_code=303)
    decision = models.Decision(
        event_id=event_id,
        action=action,
        approved_by=approved_by,
        approved_at=datetime.utcnow(),
        rationale_text=rationale_text,
        decision_driver=decision_driver,
        assumption_notes=assumption_notes,
        override_reason=override_reason,
        checklist_json=checklist,
        status_after="closed",
    )
    db.add(decision)
    db.flush()
    event.status = "closed"
    from app.services import audit

    audit.append_audit_log(db, "decision", decision.id)
    db.commit()
    return RedirectResponse(url=f"/events-ui?event_id={event_id}", status_code=303)



@app.post("/events-ui/{event_id}/status")
def event_status_ui(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    status: str = Form("open"),
):
    event = db.get(models.ConjunctionEvent, event_id)
    if not event:
        return RedirectResponse(url=f"/events-ui?event_id={event_id}", status_code=303)
    if status not in {"open", "in_review", "closed"}:
        return RedirectResponse(url=f"/events-ui?event_id={event_id}", status_code=303)
    event.status = status
    db.commit()
    return RedirectResponse(url=f"/events-ui?event_id={event_id}", status_code=303)


@app.get("/audit-ui", response_class=HTMLResponse)
def audit_ui(
    request: Request,
    db: Session = Depends(get_db),
    event_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    query = db.query(models.AuditLog)
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date)
            query = query.filter(models.AuditLog.created_at >= start_dt)
        except ValueError:
            pass
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date)
            query = query.filter(models.AuditLog.created_at <= end_dt)
        except ValueError:
            pass

    if event_id:
        decision_ids = (
            db.query(models.Decision.id)
            .filter(models.Decision.event_id == event_id)
            .subquery()
        )
        query = query.filter(models.AuditLog.entity_type == "decision").filter(
            models.AuditLog.entity_id.in_(decision_ids)
        )

    entries = query.order_by(models.AuditLog.id.desc()).limit(100).all()

    context_entries = []
    for entry in entries:
        context = {"entry": entry, "decision": None, "event": None, "risk": None, "maneuvers": []}
        if entry.entity_type == "decision":
            decision = db.get(models.Decision, entry.entity_id)
            if decision:
                event = db.get(models.ConjunctionEvent, decision.event_id)
                risk = (
                    db.query(models.RiskAssessment)
                    .filter(models.RiskAssessment.event_id == decision.event_id)
                    .order_by(models.RiskAssessment.id.desc())
                    .first()
                )
                maneuvers = (
                    db.query(models.ManeuverOption)
                    .filter(models.ManeuverOption.event_id == decision.event_id)
                    .order_by(models.ManeuverOption.risk_after.asc())
                    .all()
                )
                context.update(
                    {
                        "decision": decision,
                        "event": event,
                        "risk": risk,
                        "maneuvers": maneuvers,
                    }
                )
        context_entries.append(context)

    return templates.TemplateResponse(
        "audit.html",
        {
            "request": request,
            "entries": context_entries,
            "filters": {"event_id": event_id, "start_date": start_date, "end_date": end_date},
        },
    )
