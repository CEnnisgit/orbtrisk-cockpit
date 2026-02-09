from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import auth
from app import models, schemas
from app.database import get_db
from app.services import ingestion, screening, propagation, webhooks

router = APIRouter()


@router.post("/ingest/orbit-state", response_model=schemas.OrbitStateOut)
async def ingest_orbit_state(
    request: Request,
    payload: schemas.OrbitStateCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    auth.require_business(request)
    if payload.satellite_id is None and payload.satellite is None:
        raise HTTPException(status_code=400, detail="Provide satellite_id or satellite")

    raw_path = ingestion.write_raw_snapshot(payload.model_dump())

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

    if payload.satellite_id:
        satellite = db.get(models.Satellite, payload.satellite_id)
        if not satellite:
            raise HTTPException(status_code=404, detail="Satellite not found")
    else:
        satellite = models.Satellite(**payload.satellite.model_dump())
        db.add(satellite)
        db.flush()

    space_object = None
    norad_id = None
    if satellite.catalog_id and str(satellite.catalog_id).isdigit():
        norad_id = int(str(satellite.catalog_id))
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
        # Upgrade to operator asset if this satellite is operator-owned.
        if not space_object.is_operator_asset:
            space_object.is_operator_asset = True
        if norad_id is not None and space_object.norad_cat_id is None:
            space_object.norad_cat_id = norad_id

    if satellite.space_object_id != space_object.id:
        satellite.space_object_id = space_object.id

    covariance = payload.covariance
    if covariance is None:
        covariance = propagation.default_covariance(source.type)

    orbit_state = models.OrbitState(
        satellite_id=satellite.id,
        space_object_id=space_object.id if space_object else None,
        epoch=payload.epoch,
        frame="ECI",
        valid_from=payload.epoch,
        valid_to=None,
        state_vector=payload.state_vector,
        covariance=covariance,
        provenance_json={"raw_path": raw_path},
        source_id=source.id,
        confidence=payload.confidence,
    )
    db.add(orbit_state)
    db.flush()

    db.commit()

    # Trigger screening for this satellite to produce/update conjunction events.
    result = screening.screen_satellite(db, satellite.id)
    if result.updates_created:
        background_tasks.add_task(webhooks.dispatch_event, "screening.completed", result.__dict__)

    return orbit_state
