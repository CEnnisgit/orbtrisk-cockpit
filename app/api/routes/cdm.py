from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import auth, models, schemas
from app.database import get_db
from app.services import conjunction, frames, ingestion, risk, webhooks
from app.settings import settings
from app.services.cdm_kvn import CdmKvnError, parse_cdm_kvn

router = APIRouter()


async def _read_cdm_request(request: Request) -> tuple[str, Optional[bool], Optional[int]]:
    content_type = (request.headers.get("content-type") or "").lower()
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        override_raw = str(form.get("override_secondary") or "").strip().lower()
        override = override_raw in {"1", "true", "yes", "y", "on"}

        primary_satellite_id: Optional[int] = None
        raw_sat = str(form.get("primary_satellite_id") or "").strip()
        if raw_sat:
            try:
                primary_satellite_id = int(raw_sat)
            except ValueError:
                primary_satellite_id = None

        upload = form.get("file")
        if upload is not None and hasattr(upload, "read"):
            raw_bytes = await upload.read()
            text = raw_bytes.decode("utf-8", errors="replace")
            return text, override, primary_satellite_id

        text = str(form.get("kvn") or "")
        return text, override, primary_satellite_id

    raw_bytes = await request.body()
    return raw_bytes.decode("utf-8", errors="replace"), None, None


def _get_or_create_source(db: Session, originator: str) -> models.Source:
    # Treat CDM originators as high-fidelity sources for confidence decay rules.
    name = (originator or "cdm").strip() or "cdm"
    source = (
        db.query(models.Source)
        .filter(models.Source.name == name)
        .filter(models.Source.type == "commercial")
        .first()
    )
    if source:
        return source
    source = models.Source(name=name, type="commercial")
    db.add(source)
    db.flush()
    return source


def _objects_match_space_object(obj, space_object: models.SpaceObject) -> bool:
    if obj.norad_cat_id is not None and space_object.norad_cat_id is not None:
        return int(obj.norad_cat_id) == int(space_object.norad_cat_id)
    if obj.name and space_object.name:
        return obj.name.strip().lower() == space_object.name.strip().lower()
    return False


def _satellite_matches_obj(sat: models.Satellite, obj) -> bool:
    if getattr(sat, "space_object", None) is not None:
        so = sat.space_object
        if so and so.norad_cat_id is not None and obj.norad_cat_id is not None:
            return int(so.norad_cat_id) == int(obj.norad_cat_id)
        if so and so.name and obj.name:
            return so.name.strip().lower() == obj.name.strip().lower()
    if sat.catalog_id and str(sat.catalog_id).isdigit() and obj.norad_cat_id is not None:
        return int(str(sat.catalog_id)) == int(obj.norad_cat_id)
    if sat.name and obj.name:
        return sat.name.strip().lower() == obj.name.strip().lower()
    return False


def _find_operator_satellite(db: Session, *, obj, name_hint: Optional[str] = None) -> Optional[models.Satellite]:
    if obj.norad_cat_id is not None:
        sat = (
            db.query(models.Satellite)
            .join(models.SpaceObject, models.Satellite.space_object_id == models.SpaceObject.id)
            .filter(models.SpaceObject.is_operator_asset.is_(True))
            .filter(models.SpaceObject.norad_cat_id == int(obj.norad_cat_id))
            .first()
        )
        if sat:
            return sat

    candidate_name = (obj.name or name_hint or "").strip()
    if candidate_name:
        lowered = candidate_name.lower()
        sat = (
            db.query(models.Satellite)
            .filter(func.lower(models.Satellite.name) == lowered)
            .order_by(models.Satellite.id.asc())
            .first()
        )
        if sat:
            return sat
        sat = (
            db.query(models.Satellite)
            .join(models.SpaceObject, models.Satellite.space_object_id == models.SpaceObject.id)
            .filter(models.SpaceObject.is_operator_asset.is_(True))
            .filter(func.lower(models.SpaceObject.name) == lowered)
            .order_by(models.Satellite.id.asc())
            .first()
        )
        if sat:
            return sat

    return None


def _get_or_create_space_object(db: Session, *, obj) -> models.SpaceObject:
    if obj.norad_cat_id is not None:
        existing = db.query(models.SpaceObject).filter(models.SpaceObject.norad_cat_id == int(obj.norad_cat_id)).first()
        if existing:
            # Preserve any existing operator-asset designation.
            if obj.name and not existing.name:
                existing.name = obj.name
            return existing

    if obj.name:
        lowered = obj.name.strip().lower()
        existing = (
            db.query(models.SpaceObject)
            .filter(func.lower(models.SpaceObject.name) == lowered)
            .order_by(models.SpaceObject.id.asc())
            .first()
        )
        if existing:
            if obj.norad_cat_id is not None and existing.norad_cat_id is None:
                existing.norad_cat_id = int(obj.norad_cat_id)
            return existing

    # Fall back to a minimal row so the event can be tracked.
    name = obj.name or (f"NORAD {obj.norad_cat_id}" if obj.norad_cat_id is not None else "Unknown")
    space_object = models.SpaceObject(
        norad_cat_id=int(obj.norad_cat_id) if obj.norad_cat_id is not None else None,
        name=name,
        object_type="UNKNOWN",
        international_designator=None,
        source_id=None,
        is_operator_asset=False,
    )
    db.add(space_object)
    db.flush()
    return space_object


def _find_matching_event(
    db: Session,
    *,
    satellite_id: int,
    space_object_id: int,
    tca: datetime,
    window_hours: float = 6.0,
) -> Optional[models.ConjunctionEvent]:
    from datetime import timedelta

    window = timedelta(hours=float(window_hours))
    candidates = (
        db.query(models.ConjunctionEvent)
        .filter(models.ConjunctionEvent.satellite_id == satellite_id)
        .filter(models.ConjunctionEvent.space_object_id == space_object_id)
        .filter(models.ConjunctionEvent.tca >= tca - window)
        .filter(models.ConjunctionEvent.tca <= tca + window)
        .all()
    )
    if not candidates:
        return None
    return min(candidates, key=lambda ev: abs((ev.tca - tca).total_seconds()))


def _dispatch_change_webhook(
    background_tasks: BackgroundTasks,
    *,
    source: str,
    event: models.ConjunctionEvent,
    update_id: int,
    computed_at: datetime,
    prev_tier: Optional[str],
    prev_conf: Optional[str],
    prev_miss_km: Optional[float],
) -> None:
    tier_from = str(prev_tier or "unknown")
    tier_to = str(event.risk_tier or "unknown")
    conf_from = str(prev_conf or "D")
    conf_to = str(event.confidence_label or "D")
    if tier_from == tier_to and conf_from == conf_to:
        return
    payload = {
        "source": source,
        "computed_at": computed_at.isoformat(),
        "changes": [
            {
                "event_id": int(event.id),
                "satellite_id": int(event.satellite_id),
                "space_object_id": int(event.space_object_id) if event.space_object_id is not None else None,
                "update_id": int(update_id),
                "tca": event.tca.isoformat(),
                "miss_distance_km": float(event.miss_distance),
                "miss_distance_from_km": float(prev_miss_km) if prev_miss_km is not None else None,
                "risk_tier_from": tier_from,
                "risk_tier_to": tier_to,
                "confidence_from": conf_from,
                "confidence_to": conf_to,
            }
        ],
    }
    background_tasks.add_task(webhooks.dispatch_event, "conjunction.changed", payload)


@router.post("/events/{event_id}/cdm", response_model=schemas.CdmAttachOut)
async def attach_cdm_kvn(
    event_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    override_secondary: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    auth.require_business(request)
    event = db.get(models.ConjunctionEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    raw_text, override_from_form, _primary_satellite_id = await _read_cdm_request(request)
    if override_from_form is not None:
        override_secondary = bool(override_from_form)

    try:
        parsed = parse_cdm_kvn(raw_text)
    except CdmKvnError as exc:
        raise HTTPException(status_code=400, detail={"errors": exc.errors})

    secondary = db.get(models.SpaceObject, event.space_object_id) if event.space_object_id else None
    if secondary is None and not override_secondary:
        raise HTTPException(status_code=400, detail="Event has no secondary object; use override_secondary=true")

    # Map CDM OBJECT1/OBJECT2 onto (primary satellite, secondary object) rather than assuming order.
    parsed_primary = parsed.object1
    parsed_secondary = parsed.object2
    if secondary is not None:
        if _objects_match_space_object(parsed.object1, secondary):
            parsed_secondary = parsed.object1
            parsed_primary = parsed.object2
        elif _objects_match_space_object(parsed.object2, secondary):
            parsed_secondary = parsed.object2
            parsed_primary = parsed.object1
        elif not override_secondary:
            raise HTTPException(status_code=400, detail="Neither CDM object matches event secondary; use override_secondary=true")

    source = _get_or_create_source(db, parsed.originator)
    raw_path = ingestion.write_raw_text_snapshot("cdm", raw_text)

    cdm = models.CdmRecord(
        event_id=event.id,
        source_id=source.id,
        tca=parsed.tca,
        raw_path=raw_path,
        format="CCSDS_CDM_KVN",
        version=parsed.version,
        originator=parsed.originator,
        ref_frame=parsed.ref_frame,
        object1_norad_cat_id=parsed.object1.norad_cat_id,
        object2_norad_cat_id=parsed.object2.norad_cat_id,
        message_json={
            "kvn": parsed.kvn,
            "miss_distance_km_reported": parsed.miss_distance_km,
            "relative_speed_km_s_reported": parsed.relative_speed_km_s,
            "covariance_rtn_km2": parsed.covariance_rtn_km2,
        },
    )
    db.add(cdm)
    db.flush()

    try:
        s1 = frames.convert_state_vector_km(parsed_primary.state_km, parsed.ref_frame, "GCRS", parsed.tca)
        s2 = frames.convert_state_vector_km(parsed_secondary.state_km, parsed.ref_frame, "GCRS", parsed.tca)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"errors": [str(exc)]})

    r1 = s1[:3]
    v1 = s1[3:]
    r2 = s2[:3]
    v2 = s2[3:]
    r_rel = [float(r2[i] - r1[i]) for i in range(3)]
    v_rel = [float(v2[i] - v1[i]) for i in range(3)]

    miss_km = float(sum(x * x for x in r_rel) ** 0.5)
    rel_speed = float(sum(x * x for x in v_rel) ** 0.5)

    basis = conjunction.rtn_basis_from_primary_state(r1, v1)
    r_rtn = conjunction.project_to_rtn(r_rel, basis)
    v_rtn = conjunction.project_to_rtn(v_rel, basis)

    now = datetime.utcnow()
    base_conf = 0.85 if parsed.covariance_rtn_km2 is not None else 0.70
    scored = risk.assess_encounter(
        conjunction.Encounter(
            tca=parsed.tca,
            miss_distance_km=miss_km,
            relative_velocity_km_s=rel_speed,
            r_rel_eci_km=r_rel,
            v_rel_eci_km_s=v_rel,
        ),
        now=now,
        dt_hours=(parsed.tca - now).total_seconds() / 3600.0,
        primary_conf=base_conf,
        secondary_conf=base_conf,
        primary_source_type="commercial",
        secondary_source_type="commercial",
        primary_age_hours=0.0,
        secondary_age_hours=0.0,
        stability_std_km=None,
    )

    details = dict(scored.details)
    details["cdm"] = {
        "originator": parsed.originator,
        "creation_date": parsed.creation_date.isoformat(),
        "ref_frame": parsed.ref_frame,
        "miss_distance_km_reported": parsed.miss_distance_km,
        "relative_speed_km_s_reported": parsed.relative_speed_km_s,
        "covariance_present": parsed.covariance_rtn_km2 is not None,
    }

    update = models.ConjunctionEventUpdate(
        event_id=event.id,
        computed_at=now,
        cdm_record_id=cdm.id,
        tca=parsed.tca,
        miss_distance_km=miss_km,
        relative_velocity_km_s=rel_speed,
        screening_volume_km=float(event.screening_volume),
        r_rel_eci_km=r_rel,
        v_rel_eci_km_s=v_rel,
        r_rel_rtn_km=r_rtn,
        v_rel_rtn_km_s=v_rtn,
        risk_tier=scored.risk_tier,
        risk_score=float(scored.risk_score),
        confidence_score=float(scored.confidence_score),
        confidence_label=scored.confidence_label,
        drivers_json=scored.drivers,
        details_json=details,
    )
    db.add(update)
    db.flush()

    prev_tier = str(event.risk_tier or "unknown")
    prev_conf = str(event.confidence_label or "D")
    prev_miss = float(event.miss_distance) if event.miss_distance is not None else None

    event.tca = parsed.tca
    event.miss_distance = miss_km
    event.relative_velocity = rel_speed
    event.risk_tier = scored.risk_tier
    event.risk_score = float(scored.risk_score)
    event.confidence_score = float(scored.confidence_score)
    event.confidence_label = scored.confidence_label
    event.current_update_id = update.id
    event.last_seen_at = now
    event.is_active = True
    db.commit()

    _dispatch_change_webhook(
        background_tasks,
        source="cdm.attach",
        event=event,
        update_id=update.id,
        computed_at=now,
        prev_tier=prev_tier,
        prev_conf=prev_conf,
        prev_miss_km=prev_miss,
    )

    return schemas.CdmAttachOut(
        event_id=event.id,
        update_id=update.id,
        cdm_record_id=cdm.id,
        tca=parsed.tca,
        originator=parsed.originator,
        ref_frame=parsed.ref_frame,
        covariance_present=parsed.covariance_rtn_km2 is not None,
    )


@router.post("/cdm/inbox", response_model=schemas.CdmAttachOut)
async def cdm_inbox(
    request: Request,
    background_tasks: BackgroundTasks,
    primary_satellite_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    auth.require_business(request)

    raw_text, _override_secondary, primary_sat_from_form = await _read_cdm_request(request)
    if primary_sat_from_form is not None:
        primary_satellite_id = primary_sat_from_form

    try:
        parsed = parse_cdm_kvn(raw_text)
    except CdmKvnError as exc:
        raise HTTPException(status_code=400, detail={"errors": exc.errors})

    primary_sat: Optional[models.Satellite] = None
    if primary_satellite_id is not None:
        primary_sat = db.get(models.Satellite, int(primary_satellite_id))
        if primary_sat is None:
            raise HTTPException(status_code=404, detail="Primary satellite not found")
    else:
        sat1 = _find_operator_satellite(db, obj=parsed.object1)
        sat2 = _find_operator_satellite(db, obj=parsed.object2)
        if sat1 and sat2 and sat1.id != sat2.id:
            raise HTTPException(status_code=400, detail="Both CDM objects match operator satellites; provide primary_satellite_id")
        primary_sat = sat1 or sat2

    if primary_sat is None:
        raise HTTPException(status_code=400, detail="No matching operator satellite found; provide primary_satellite_id")

    # Decide which CDM object is primary based on satellite match.
    parsed_primary = parsed.object1
    parsed_secondary = parsed.object2
    matches_obj1 = _satellite_matches_obj(primary_sat, parsed.object1)
    matches_obj2 = _satellite_matches_obj(primary_sat, parsed.object2)
    if matches_obj1 and not matches_obj2:
        parsed_primary = parsed.object1
        parsed_secondary = parsed.object2
    elif matches_obj2 and not matches_obj1:
        parsed_primary = parsed.object2
        parsed_secondary = parsed.object1
    elif not matches_obj1 and not matches_obj2:
        raise HTTPException(status_code=400, detail="Primary satellite does not match CDM OBJECT1 or OBJECT2")
    else:
        raise HTTPException(status_code=400, detail="Primary satellite matches both CDM objects; provide primary_satellite_id")

    secondary_so = _get_or_create_space_object(db, obj=parsed_secondary)

    try:
        s1 = frames.convert_state_vector_km(parsed_primary.state_km, parsed.ref_frame, "GCRS", parsed.tca)
        s2 = frames.convert_state_vector_km(parsed_secondary.state_km, parsed.ref_frame, "GCRS", parsed.tca)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"errors": [str(exc)]})

    r1 = s1[:3]
    v1 = s1[3:]
    r2 = s2[:3]
    v2 = s2[3:]
    r_rel = [float(r2[i] - r1[i]) for i in range(3)]
    v_rel = [float(v2[i] - v1[i]) for i in range(3)]

    miss_km = float(sum(x * x for x in r_rel) ** 0.5)
    rel_speed = float(sum(x * x for x in v_rel) ** 0.5)

    now = datetime.utcnow()
    event = _find_matching_event(
        db,
        satellite_id=int(primary_sat.id),
        space_object_id=int(secondary_so.id),
        tca=parsed.tca,
    )
    created = False
    if event is None:
        event = models.ConjunctionEvent(
            satellite_id=int(primary_sat.id),
            space_object_id=int(secondary_so.id),
            object_id=int(secondary_so.id),
            tca=parsed.tca,
            miss_distance=miss_km,
            relative_velocity=rel_speed,
            screening_volume=float(settings.screening_volume_km),
            status="open",
            is_active=True,
            last_seen_at=now,
        )
        db.add(event)
        db.flush()
        created = True

    prev_tier = str(event.risk_tier or "unknown")
    prev_conf = str(event.confidence_label or "D")
    prev_miss = float(event.miss_distance) if event.miss_distance is not None else None

    source = _get_or_create_source(db, parsed.originator)
    raw_path = ingestion.write_raw_text_snapshot("cdm", raw_text)
    cdm = models.CdmRecord(
        event_id=event.id,
        source_id=source.id,
        tca=parsed.tca,
        raw_path=raw_path,
        format="CCSDS_CDM_KVN",
        version=parsed.version,
        originator=parsed.originator,
        ref_frame=parsed.ref_frame,
        object1_norad_cat_id=parsed.object1.norad_cat_id,
        object2_norad_cat_id=parsed.object2.norad_cat_id,
        message_json={
            "kvn": parsed.kvn,
            "miss_distance_km_reported": parsed.miss_distance_km,
            "relative_speed_km_s_reported": parsed.relative_speed_km_s,
            "covariance_rtn_km2": parsed.covariance_rtn_km2,
            "mapping": {"primary": "OBJECT1" if parsed_primary == parsed.object1 else "OBJECT2"},
        },
    )
    db.add(cdm)
    db.flush()

    basis = conjunction.rtn_basis_from_primary_state(r1, v1)
    r_rtn = conjunction.project_to_rtn(r_rel, basis)
    v_rtn = conjunction.project_to_rtn(v_rel, basis)

    base_conf = 0.85 if parsed.covariance_rtn_km2 is not None else 0.70
    scored = risk.assess_encounter(
        conjunction.Encounter(
            tca=parsed.tca,
            miss_distance_km=miss_km,
            relative_velocity_km_s=rel_speed,
            r_rel_eci_km=r_rel,
            v_rel_eci_km_s=v_rel,
        ),
        now=now,
        dt_hours=(parsed.tca - now).total_seconds() / 3600.0,
        primary_conf=base_conf,
        secondary_conf=base_conf,
        primary_source_type="commercial",
        secondary_source_type="commercial",
        primary_age_hours=0.0,
        secondary_age_hours=0.0,
        stability_std_km=None,
    )

    details = dict(scored.details)
    details["cdm"] = {
        "originator": parsed.originator,
        "creation_date": parsed.creation_date.isoformat(),
        "ref_frame": parsed.ref_frame,
        "miss_distance_km_reported": parsed.miss_distance_km,
        "relative_speed_km_s_reported": parsed.relative_speed_km_s,
        "covariance_present": parsed.covariance_rtn_km2 is not None,
    }

    update = models.ConjunctionEventUpdate(
        event_id=event.id,
        computed_at=now,
        cdm_record_id=cdm.id,
        tca=parsed.tca,
        miss_distance_km=miss_km,
        relative_velocity_km_s=rel_speed,
        screening_volume_km=float(event.screening_volume),
        r_rel_eci_km=r_rel,
        v_rel_eci_km_s=v_rel,
        r_rel_rtn_km=r_rtn,
        v_rel_rtn_km_s=v_rtn,
        risk_tier=scored.risk_tier,
        risk_score=float(scored.risk_score),
        confidence_score=float(scored.confidence_score),
        confidence_label=scored.confidence_label,
        drivers_json=scored.drivers,
        details_json=details,
    )
    db.add(update)
    db.flush()

    event.tca = parsed.tca
    event.miss_distance = miss_km
    event.relative_velocity = rel_speed
    event.risk_tier = scored.risk_tier
    event.risk_score = float(scored.risk_score)
    event.confidence_score = float(scored.confidence_score)
    event.confidence_label = scored.confidence_label
    event.current_update_id = update.id
    event.last_seen_at = now
    event.is_active = True

    db.commit()

    if created:
        background_tasks.add_task(
            webhooks.dispatch_event,
            "conjunction.created",
            {
                "source": "cdm.inbox",
                "created_at": now.isoformat(),
                "event_id": int(event.id),
                "satellite_id": int(event.satellite_id),
                "space_object_id": int(event.space_object_id) if event.space_object_id is not None else None,
                "tca": event.tca.isoformat(),
            },
        )
    _dispatch_change_webhook(
        background_tasks,
        source="cdm.inbox",
        event=event,
        update_id=update.id,
        computed_at=now,
        prev_tier=prev_tier,
        prev_conf=prev_conf,
        prev_miss_km=prev_miss,
    )

    return schemas.CdmAttachOut(
        event_id=event.id,
        update_id=update.id,
        cdm_record_id=cdm.id,
        tca=parsed.tca,
        originator=parsed.originator,
        ref_frame=parsed.ref_frame,
        covariance_present=parsed.covariance_rtn_km2 is not None,
    )
