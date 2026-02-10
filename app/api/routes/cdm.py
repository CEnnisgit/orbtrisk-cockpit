from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app import auth, models, schemas
from app.database import get_db
from app.services import conjunction, frames, ingestion, risk
from app.services.cdm_kvn import CdmKvnError, parse_cdm_kvn

router = APIRouter()


async def _read_cdm_text(request: Request) -> tuple[str, Optional[bool]]:
    content_type = (request.headers.get("content-type") or "").lower()
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        override_raw = str(form.get("override_secondary") or "").strip().lower()
        override = override_raw in {"1", "true", "yes", "y", "on"}

        upload = form.get("file")
        if upload is not None and hasattr(upload, "read"):
            raw_bytes = await upload.read()
            text = raw_bytes.decode("utf-8", errors="replace")
            return text, override

        text = str(form.get("kvn") or "")
        return text, override

    raw_bytes = await request.body()
    return raw_bytes.decode("utf-8", errors="replace"), None


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


@router.post("/events/{event_id}/cdm", response_model=schemas.CdmAttachOut)
async def attach_cdm_kvn(
    event_id: int,
    request: Request,
    override_secondary: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    auth.require_business(request)
    event = db.get(models.ConjunctionEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    raw_text, override_from_form = await _read_cdm_text(request)
    if override_from_form is not None:
        override_secondary = bool(override_from_form)

    try:
        parsed = parse_cdm_kvn(raw_text)
    except CdmKvnError as exc:
        raise HTTPException(status_code=400, detail={"errors": exc.errors})

    secondary = db.get(models.SpaceObject, event.space_object_id) if event.space_object_id else None
    if not override_secondary and secondary is not None:
        if parsed.object2.norad_cat_id is not None and secondary.norad_cat_id is not None:
            if int(parsed.object2.norad_cat_id) != int(secondary.norad_cat_id):
                raise HTTPException(status_code=400, detail="Secondary NORAD ID does not match event")
        elif parsed.object2.name and secondary.name:
            if parsed.object2.name.strip().lower() != secondary.name.strip().lower():
                raise HTTPException(status_code=400, detail="Secondary name does not match event")

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
        s1 = frames.convert_state_vector_km(parsed.object1.state_km, parsed.ref_frame, "GCRS", parsed.tca)
        s2 = frames.convert_state_vector_km(parsed.object2.state_km, parsed.ref_frame, "GCRS", parsed.tca)
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

    return schemas.CdmAttachOut(
        event_id=event.id,
        update_id=update.id,
        cdm_record_id=cdm.id,
        tca=parsed.tca,
        originator=parsed.originator,
        ref_frame=parsed.ref_frame,
        covariance_present=parsed.covariance_rtn_km2 is not None,
    )
