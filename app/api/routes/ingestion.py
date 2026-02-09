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
        sigma = getattr(event, "_sigma_km", propagation.extract_sigma(covariance))
        poc, risk_score, components, sensitivity = risk.assess_event(event, sigma)
        assessment = models.RiskAssessment(
            event_id=event.id,
            poc=poc,
            risk_score=risk_score,
            components_json=components,
            sensitivity_json=sensitivity,
        )
        db.add(assessment)

        r_rel = getattr(event, "_r_rel_km", None)
        v_rel = getattr(event, "_v_rel_km_s", None)
        if isinstance(r_rel, list) and isinstance(v_rel, list) and len(r_rel) == 3 and len(v_rel) == 3:
            db.merge(
                models.EventGeometry(
                    event_id=event.id,
                    frame="ECI",
                    relative_position_km=r_rel,
                    relative_velocity_km_s=v_rel,
                    combined_pos_covariance_km2=getattr(event, "_combined_pos_covariance_km2", None),
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


@router.post("/ingest/cdm", response_model=schemas.CdmIngestOut)
async def ingest_cdm(
    payload: schemas.CdmIngestRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    if payload.satellite_id is None and payload.satellite is None:
        raise HTTPException(status_code=400, detail="Provide satellite_id or satellite")

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

    # Operator-owned space object for the primary satellite.
    operator_object = (
        db.query(models.SpaceObject)
        .filter(models.SpaceObject.name == satellite.name)
        .filter(models.SpaceObject.is_operator_asset.is_(True))
        .first()
    )
    if not operator_object:
        operator_object = models.SpaceObject(
            norad_cat_id=int(satellite.catalog_id) if satellite.catalog_id and str(satellite.catalog_id).isdigit() else None,
            name=satellite.name,
            object_type="PAYLOAD",
            international_designator=None,
            source_id=source.id,
            is_operator_asset=True,
        )
        db.add(operator_object)
        db.flush()

    # Secondary catalog object (debris / other payload).
    secondary = None
    if payload.secondary_norad_cat_id is not None:
        secondary = (
            db.query(models.SpaceObject)
            .filter(models.SpaceObject.norad_cat_id == payload.secondary_norad_cat_id)
            .filter(models.SpaceObject.is_operator_asset.is_(False))
            .first()
        )
    if secondary is None and payload.secondary_name:
        secondary = (
            db.query(models.SpaceObject)
            .filter(models.SpaceObject.name == payload.secondary_name)
            .filter(models.SpaceObject.is_operator_asset.is_(False))
            .first()
        )
    if secondary is None:
        name = payload.secondary_name or (
            f"OBJECT-{payload.secondary_norad_cat_id}" if payload.secondary_norad_cat_id is not None else "UNKNOWN"
        )
        secondary = models.SpaceObject(
            norad_cat_id=payload.secondary_norad_cat_id,
            name=name,
            object_type="UNKNOWN",
            international_designator=None,
            source_id=source.id,
            is_operator_asset=False,
        )
        db.add(secondary)
        db.flush()

    r_rel = [float(x) for x in payload.relative_position_km]
    v_rel = [float(x) for x in payload.relative_velocity_km_s]
    miss_km = propagation.norm(r_rel)
    rel_speed = propagation.norm(v_rel)

    event = models.ConjunctionEvent(
        satellite_id=satellite.id,
        object_id=secondary.id,
        space_object_id=secondary.id,
        tca=payload.tca,
        miss_distance=float(miss_km),
        relative_velocity=float(rel_speed),
        screening_volume=10.0,
        status="open",
    )
    # Provide geometry + covariance for the covariance-based PoC model.
    event._r_rel_km = r_rel  # type: ignore[attr-defined]
    event._v_rel_km_s = v_rel  # type: ignore[attr-defined]
    event._combined_pos_covariance_km2 = payload.combined_pos_covariance_km2  # type: ignore[attr-defined]
    if payload.hard_body_radius_m is not None:
        event._hard_body_radius_m = float(payload.hard_body_radius_m)  # type: ignore[attr-defined]
    db.add(event)
    db.flush()

    db.merge(
        models.EventGeometry(
            event_id=event.id,
            frame="ECI",
            relative_position_km=r_rel,
            relative_velocity_km_s=v_rel,
            combined_pos_covariance_km2=payload.combined_pos_covariance_km2,
        )
    )

    # Persist the original message for provenance/audit.
    db.add(
        models.CdmRecord(
            event_id=event.id,
            source_id=source.id,
            tca=payload.tca,
            message_json=payload.model_dump(mode="json"),
        )
    )

    cov = payload.combined_pos_covariance_km2
    sigma_km = max(0.1, ((float(cov[0][0]) + float(cov[1][1]) + float(cov[2][2])) / 3.0) ** 0.5)
    poc, risk_score, components, sensitivity = risk.assess_event(event, sigma_km)
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

    return schemas.CdmIngestOut(event_id=event.id, risk_score=float(risk_score), poc=float(poc))
