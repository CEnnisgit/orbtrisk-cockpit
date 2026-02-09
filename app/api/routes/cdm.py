from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import auth, models, schemas
from app.database import get_db
from app.services import conjunction, risk

router = APIRouter()


@router.post("/events/{event_id}/cdm", response_model=schemas.CdmAttachOut)
def attach_cdm(
    event_id: int,
    request: Request,
    payload: schemas.CdmAttachRequest,
    db: Session = Depends(get_db),
):
    auth.require_business(request)
    event = db.get(models.ConjunctionEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    source = (
        db.query(models.Source)
        .filter(models.Source.name == payload.source.name)
        .filter(models.Source.type == payload.source.type)
        .first()
    )
    if not source:
        source = models.Source(
            name=payload.source.name,
            type=payload.source.type,
            provenance_uri=payload.source.provenance_uri,
            license_terms=payload.source.license_terms,
        )
        db.add(source)
        db.flush()

    secondary = db.get(models.SpaceObject, event.space_object_id) if event.space_object_id else None

    if not payload.override_secondary:
        if payload.secondary_norad_cat_id is not None and secondary and secondary.norad_cat_id is not None:
            if int(payload.secondary_norad_cat_id) != int(secondary.norad_cat_id):
                raise HTTPException(status_code=400, detail="Secondary NORAD ID does not match event")
        if payload.secondary_name and secondary and secondary.name:
            if payload.secondary_name.strip() != secondary.name.strip():
                raise HTTPException(status_code=400, detail="Secondary name does not match event")

    # Persist the original message for provenance/audit.
    cdm = models.CdmRecord(
        event_id=event.id,
        source_id=source.id,
        tca=payload.tca,
        message_json=payload.model_dump(mode="json"),
    )
    db.add(cdm)
    db.flush()

    r_rel = [float(x) for x in payload.relative_position_km]
    v_rel = [float(x) for x in payload.relative_velocity_km_s]
    miss_km = float(sum(x * x for x in r_rel) ** 0.5)
    rel_speed = float(sum(x * x for x in v_rel) ** 0.5)

    # CDM: treat as commercial/ephemeris-like for confidence age factor (no TLE decay).
    now = datetime.utcnow()
    scored = risk.assess_encounter(
        conjunction.Encounter(
            tca=payload.tca,
            miss_distance_km=miss_km,
            relative_velocity_km_s=rel_speed,
            r_rel_eci_km=r_rel,
            v_rel_eci_km_s=v_rel,
        ),
        now=now,
        dt_hours=(payload.tca - now).total_seconds() / 3600.0,
        primary_conf=0.9,
        secondary_conf=0.9,
        primary_source_type="commercial",
        secondary_source_type="commercial",
        primary_age_hours=0.0,
        secondary_age_hours=0.0,
        stability_std_km=None,
    )

    # RTN projection is unknown from CDM alone; leave as null.
    update = models.ConjunctionEventUpdate(
        event_id=event.id,
        computed_at=now,
        cdm_record_id=cdm.id,
        tca=payload.tca,
        miss_distance_km=miss_km,
        relative_velocity_km_s=rel_speed,
        screening_volume_km=float(event.screening_volume),
        r_rel_eci_km=r_rel,
        v_rel_eci_km_s=v_rel,
        r_rel_rtn_km=None,
        v_rel_rtn_km_s=None,
        risk_tier=scored.risk_tier,
        risk_score=float(scored.risk_score),
        confidence_score=float(scored.confidence_score),
        confidence_label=scored.confidence_label,
        drivers_json=scored.drivers,
        details_json=scored.details,
    )
    db.add(update)
    db.flush()

    event.tca = payload.tca
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

    # Future: webhook/event bus could go here.
    return schemas.CdmAttachOut(event_id=event.id, update_id=update.id)
