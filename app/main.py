import math
from datetime import datetime, timedelta
from typing import Dict, Optional
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.routes import (
    ingestion,
    satellites,
    events,
    decisions,
    audit,
    webhooks,
    catalog,
    ai,
    solar,
    screening,
    cdm,
)
from app.database import get_db, init_db
from app import models
from app import auth
from app.services import demo, propagation, catalog_sync
from app.settings import settings

app = FastAPI(title="Space Risk & Collision Avoidance MVP")
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=False,
)

app.include_router(ingestion.router, tags=["ingestion"])
app.include_router(satellites.router, tags=["satellites"])
app.include_router(events.router, tags=["events"])
app.include_router(decisions.router, tags=["decisions"])
app.include_router(audit.router, tags=["audit"])
app.include_router(webhooks.router, tags=["webhooks"])
app.include_router(catalog.router, tags=["catalog"])
app.include_router(ai.router, tags=["ai"])
app.include_router(solar.router, tags=["solar"])
app.include_router(screening.router, tags=["screening"])
app.include_router(cdm.router, tags=["cdm"])

app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")


@app.middleware("http")
async def add_template_globals(request: Request, call_next):
    # Make auth state available in every template.
    request.state.is_business = auth.is_business(request)
    return await call_next(request)


@app.get("/auth/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/dashboard"):
    configured = auth.business_access_configured(settings.business_access_code)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "next": next, "configured": configured, "error": None},
    )


@app.post("/auth/login")
def login_submit(
    request: Request,
    access_code: str = Form(None),
    next: str = Form("/dashboard"),
):
    configured = auth.business_access_configured(settings.business_access_code)
    if not configured:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "next": next,
                "configured": configured,
                "error": "Business login is not configured on this server.",
            },
            status_code=400,
        )

    if not access_code or access_code.strip() != (settings.business_access_code or "").strip():
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "next": next,
                "configured": configured,
                "error": "Invalid access code.",
            },
            status_code=401,
        )

    request.session["role"] = "business"
    return RedirectResponse(url=next or "/dashboard", status_code=303)


@app.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


@app.on_event("startup")
def on_startup():
    init_db()
    db = next(get_db())
    try:
        demo.seed_runbooks(db)
        db.commit()
        try:
            from app.services import screening

            screening.cleanup_retention(db)
        except Exception:
            pass
    finally:
        db.close()
    catalog_sync.start_scheduler()


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    satellite_count = db.query(models.Satellite).count()
    event_count = db.query(models.ConjunctionEvent).count()
    high_risk = db.query(models.ConjunctionEvent).filter(models.ConjunctionEvent.risk_tier == "high").count()
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


def _require_business_ui(request: Request) -> None:
    if not auth.is_business(request):
        # Raise an HTTPException so FastAPI turns it into a redirect.
        raise HTTPException(
            status_code=303,
            headers={"Location": f"/auth/login?next={request.url.path}"},
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
    active_only = True
    if window == "all":
        active_only = False
        window = None
    if window in {"24h", "72h", "7d"}:
        hours = {"24h": 24, "72h": 72, "7d": 168}[window]
        cutoff = datetime.utcnow() + timedelta(hours=hours)
        query = query.filter(models.ConjunctionEvent.tca <= cutoff)
    if active_only:
        query = query.filter(models.ConjunctionEvent.is_active.is_(True))
    events = query.all()

    # No separate risk assessment table: fields live on ConjunctionEvent.

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
        risk_score = float(event.risk_score or 0.0)
        band = event.risk_tier or "unknown"
        if risk_band:
            if risk_band != band:
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
    selected_update = None
    selected_decision = None
    selected_runbook = None
    selected_object = None
    if event_id:
        selected = db.get(models.ConjunctionEvent, event_id)
        if selected:
            if selected.current_update_id:
                selected_update = db.get(models.ConjunctionEventUpdate, int(selected.current_update_id))
            selected_decision = (
                db.query(models.Decision)
                .filter(models.Decision.event_id == event_id)
                .order_by(models.Decision.id.desc())
                .first()
            )
            if selected.space_object_id:
                selected_object = db.get(models.SpaceObject, selected.space_object_id)
            band = selected.risk_tier
            if band in {"high", "watch", "low"}:
                rb = "medium" if band == "watch" else band
                selected_runbook = (
                    db.query(models.Runbook)
                    .filter(models.Runbook.risk_band == rb)
                    .order_by(models.Runbook.id.desc())
                    .first()
                )

    filter_params = {"status": status, "risk_band": risk_band, "window": window}
    filter_query = urlencode({k: v for k, v in filter_params.items() if v})
    presets = {
        "High Risk": urlencode({"risk_band": "high"}),
        "Watch": urlencode({"risk_band": "watch"}),
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
            "selected_update": selected_update,
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
    update = db.get(models.ConjunctionEventUpdate, int(event.current_update_id)) if event.current_update_id else None
    updates = (
        db.query(models.ConjunctionEventUpdate)
        .filter(models.ConjunctionEventUpdate.event_id == event_id)
        .order_by(models.ConjunctionEventUpdate.computed_at.desc())
        .limit(20)
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
    band = event.risk_tier
    if band in {"high", "watch", "low"}:
        rb = "medium" if band == "watch" else band
        runbook = (
            db.query(models.Runbook)
            .filter(models.Runbook.risk_band == rb)
            .order_by(models.Runbook.id.desc())
            .first()
        )
    return templates.TemplateResponse(
        "event_detail.html",
        {
            "request": request,
            "event": event,
            "time_to_tca_hours": (event.tca - datetime.utcnow()).total_seconds() / 3600.0,
            "update": update,
            "updates": updates,
            "decision": decision,
            "runbook": runbook,
            "space_object": space_object,
            "geometry": update,
        },
    )



@app.post("/demo/seed")
async def seed_demo_data(db: Session = Depends(get_db)):
    demo.seed_demo(db)
    db.commit()
    return RedirectResponse(url="/dashboard?seeded=1", status_code=303)



@app.get("/satellites-ui", response_class=HTMLResponse)
def satellites_ui(request: Request, db: Session = Depends(get_db)):
    _require_business_ui(request)
    satellites = db.query(models.Satellite).order_by(models.Satellite.id.asc()).all()
    return templates.TemplateResponse(
        "satellites.html",
        {"request": request, "satellites": satellites},
    )


@app.get("/satellites-ui/{satellite_id}", response_class=HTMLResponse)
def satellite_dashboard_ui(satellite_id: int, request: Request, db: Session = Depends(get_db)):
    _require_business_ui(request)
    satellite = db.get(models.Satellite, satellite_id)
    if not satellite:
        return templates.TemplateResponse("satellite_dashboard.html", {"request": request, "satellite": None})

    now = datetime.utcnow()
    horizon = int(settings.screening_horizon_days)
    cutoff = now + timedelta(days=horizon)
    events = (
        db.query(models.ConjunctionEvent)
        .filter(models.ConjunctionEvent.satellite_id == satellite_id)
        .filter(models.ConjunctionEvent.is_active.is_(True))
        .filter(models.ConjunctionEvent.tca <= cutoff)
        .order_by(models.ConjunctionEvent.tca.asc())
        .all()
    )

    so_ids = {e.space_object_id for e in events if e.space_object_id}
    so_map = {}
    if so_ids:
        space_objects = db.query(models.SpaceObject).filter(models.SpaceObject.id.in_(so_ids)).all()
        so_map = {so.id: so for so in space_objects}

    items = []
    for event in events:
        space_object = so_map.get(event.space_object_id) if event.space_object_id else None
        items.append(
            {
                "event": event,
                "time_to_tca_hours": (event.tca - now).total_seconds() / 3600.0,
                "object_name": space_object.name if space_object else None,
            }
        )

    last_seen = (
        db.query(func.max(models.ConjunctionEvent.last_seen_at))
        .filter(models.ConjunctionEvent.satellite_id == satellite_id)
        .scalar()
    )

    return templates.TemplateResponse(
        "satellite_dashboard.html",
        {
            "request": request,
            "satellite": satellite,
            "events": items,
            "horizon_days": horizon,
            "last_screened_at": last_seen,
        },
    )


@app.post("/satellites-ui/{satellite_id}/screen")
def satellite_screen_ui(satellite_id: int, request: Request, db: Session = Depends(get_db)):
    _require_business_ui(request)
    sat = db.get(models.Satellite, satellite_id)
    if not sat:
        return RedirectResponse(url="/satellites-ui", status_code=303)
    from app.services import screening

    screening.screen_satellite(db, satellite_id)
    return RedirectResponse(url=f"/satellites-ui/{satellite_id}", status_code=303)


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
    _require_business_ui(request)
    if not name:
        return RedirectResponse(url="/satellites-ui", status_code=303)
    # Link satellite to a single SpaceObject identity.
    norad_id = int(str(catalog_id)) if catalog_id and str(catalog_id).isdigit() else None
    space_object = None
    if norad_id is not None:
        space_object = db.query(models.SpaceObject).filter(models.SpaceObject.norad_cat_id == norad_id).first()
    if space_object is None:
        space_object = db.query(models.SpaceObject).filter(models.SpaceObject.name == name).first()
    if space_object is None:
        space_object = models.SpaceObject(
            norad_cat_id=norad_id,
            name=name,
            object_type="PAYLOAD",
            international_designator=None,
            source_id=None,
            is_operator_asset=True,
        )
        db.add(space_object)
        db.flush()
    else:
        if not space_object.is_operator_asset:
            space_object.is_operator_asset = True
        if norad_id is not None and space_object.norad_cat_id is None:
            space_object.norad_cat_id = norad_id

    satellite = models.Satellite(
        name=name,
        operator_id=operator_id,
        catalog_id=catalog_id,
        orbit_regime=orbit_regime,
        status=status,
        space_object_id=space_object.id,
    )
    db.add(satellite)
    db.commit()
    return RedirectResponse(url="/satellites-ui", status_code=303)


@app.get("/catalog-ui", response_class=HTMLResponse)
def catalog_ui(
    request: Request,
    db: Session = Depends(get_db),
    q: Optional[str] = None,
    show: str = "catalog",
    page: int = 1,
):
    _require_business_ui(request)
    per_page = 50
    page = max(1, int(page))
    offset = (page - 1) * per_page

    is_operator_asset = None
    if show == "catalog":
        is_operator_asset = False
    elif show == "operator":
        is_operator_asset = True
    elif show == "all":
        is_operator_asset = None
    else:
        show = "catalog"
        is_operator_asset = False

    data = catalog_sync.catalog_objects_page(
        db,
        q=q,
        is_operator_asset=is_operator_asset,
        limit=per_page,
        offset=offset,
    )
    status = catalog_sync.catalog_status(db)

    total_with_tle = int(data.get("total_with_tle") or 0)
    pages = max(1, int(math.ceil(total_with_tle / per_page))) if total_with_tle else 1
    if page > pages:
        page = pages
        offset = (page - 1) * per_page
        data = catalog_sync.catalog_objects_page(
            db,
            q=q,
            is_operator_asset=is_operator_asset,
            limit=per_page,
            offset=offset,
        )

    base_params = {"q": q or None, "show": show}
    base_params = {k: v for k, v in base_params.items() if v}
    query_base = urlencode(base_params)

    def page_link(p: int) -> str:
        params = dict(base_params)
        params["page"] = p
        return "/catalog-ui?" + urlencode(params)

    return templates.TemplateResponse(
        "catalog.html",
        {
            "request": request,
            "items": data.get("items", []),
            "q": q or "",
            "show": show,
            "page": page,
            "pages": pages,
            "total": data.get("total", 0),
            "total_with_tle": total_with_tle,
            "missing_tle": data.get("missing_tle", 0),
            "catalog_status": status,
            "page_prev": page_link(page - 1) if page > 1 else None,
            "page_next": page_link(page + 1) if page < pages else None,
            "query_base": query_base,
        },
    )


@app.get("/catalog-ui/{object_id}", response_class=HTMLResponse)
def catalog_detail_ui(object_id: int, request: Request, db: Session = Depends(get_db)):
    _require_business_ui(request)
    detail = catalog_sync.catalog_object_detail(db, object_id)
    if not detail:
        return templates.TemplateResponse(
            "catalog_detail.html",
            {"request": request, "object": None},
        )
    return templates.TemplateResponse(
        "catalog_detail.html",
        {"request": request, "object": detail},
    )


@app.get("/ingest-ui", response_class=HTMLResponse)
def ingest_ui(request: Request, db: Session = Depends(get_db)):
    _require_business_ui(request)
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
    _require_business_ui(request)
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

    space_object = satellite.space_object
    norad_id = int(str(satellite.catalog_id)) if satellite.catalog_id and str(satellite.catalog_id).isdigit() else None
    if space_object is None and norad_id is not None:
        space_object = db.query(models.SpaceObject).filter(models.SpaceObject.norad_cat_id == norad_id).first()
    if space_object is None:
        space_object = db.query(models.SpaceObject).filter(models.SpaceObject.name == satellite.name).first()
    if space_object is None:
        space_object = models.SpaceObject(
            norad_cat_id=norad_id,
            name=satellite.name,
            object_type="PAYLOAD",
            international_designator=None,
            source_id=source.id,
            is_operator_asset=True,
        )
        db.add(space_object)
        db.flush()
    else:
        if not space_object.is_operator_asset:
            space_object.is_operator_asset = True
        if norad_id is not None and space_object.norad_cat_id is None:
            space_object.norad_cat_id = norad_id
    if satellite.space_object_id != space_object.id:
        satellite.space_object_id = space_object.id

    vector = [float(x.strip()) for x in state_vector.split(",") if x.strip()]
    if len(vector) != 6:
        return RedirectResponse(url="/ingest-ui", status_code=303)

    orbit_state = models.OrbitState(
        satellite_id=satellite.id,
        space_object_id=space_object.id if space_object else None,
        epoch=datetime.fromisoformat(epoch.replace("Z", "+00:00")),
        frame="ECI",
        valid_from=datetime.fromisoformat(epoch.replace("Z", "+00:00")),
        valid_to=None,
        state_vector=vector,
        covariance=propagation.default_covariance(source.type),
        provenance_json={"raw_path": ingestion.write_raw_snapshot({"epoch": epoch, "state_vector": vector})},
        source_id=source.id,
        confidence=confidence,
    )
    db.add(orbit_state)
    db.flush()

    db.commit()
    from app.services import screening

    screening.screen_satellite(db, satellite.id)
    return RedirectResponse(url=f"/satellites-ui/{satellite.id}", status_code=303)


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
    _require_business_ui(request)
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
        context = {"entry": entry, "decision": None, "event": None}
        if entry.entity_type == "decision":
            decision = db.get(models.Decision, entry.entity_id)
            if decision:
                event = db.get(models.ConjunctionEvent, decision.event_id)
                context.update(
                    {
                        "decision": decision,
                        "event": event,
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
