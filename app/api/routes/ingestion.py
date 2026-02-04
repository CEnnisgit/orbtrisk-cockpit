from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.services import ingestion, conjunction, risk, maneuver, propagation, webhooks

router = APIRouter()


@router.post("/ingest/orbit-state", response_model=schemas.OrbitStateOut)
async def ingest_orbit_state(
    payload: schemas.OrbitStateCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    if payload.satellite_id is None and payload.satellite is None:
        raise HTTPException(status_code=400, detail="Provide satellite_id or satellite")

    ingestion.write_raw_snapshot(payload.model_dump())

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

    covariance = payload.covariance
    if covariance is None:
        covariance = propagation.default_covariance(source.type)

    orbit_state = models.OrbitState(
        satellite_id=satellite.id,
        space_object_id=space_object.id if space_object else None,
        epoch=payload.epoch,
        state_vector=payload.state_vector,
        covariance=covariance,
        source_id=source.id,
        confidence=payload.confidence,
    )
    db.add(orbit_state)
    db.flush()

    events = conjunction.detect_events_for_state(db, orbit_state)
    db.flush()
    for event in events:
        sigma = propagation.extract_sigma(covariance)
        poc, risk_score, components, sensitivity = risk.assess_event(event, sigma)
        assessment = models.RiskAssessment(
            event_id=event.id,
            poc=poc,
            risk_score=risk_score,
            components_json=components,
            sensitivity_json=sensitivity,
        )
        db.add(assessment)

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

        event_payload = {
            "event_id": event.id,
            "satellite_id": event.satellite_id,
            "space_object_id": event.space_object_id,
            "tca": event.tca.isoformat(),
            "miss_distance": event.miss_distance,
            "risk_score": risk_score,
            "created_at": datetime.utcnow().isoformat(),
        }
        background_tasks.add_task(webhooks.dispatch_event, "event.created", event_payload)

    db.commit()

    return orbit_state
