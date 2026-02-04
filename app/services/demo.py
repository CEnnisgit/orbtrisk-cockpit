from datetime import datetime
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app import models
from app.services import conjunction, risk, maneuver, propagation


def _get_or_create_source(db: Session) -> models.Source:
    source = (
        db.query(models.Source)
        .filter(models.Source.name == "public-tle")
        .filter(models.Source.type == "public")
        .first()
    )
    if source:
        return source
    source = models.Source(name="public-tle", type="public")
    db.add(source)
    db.flush()
    return source


def _get_or_create_satellite(db: Session, name: str) -> models.Satellite:
    satellite = db.query(models.Satellite).filter(models.Satellite.name == name).first()
    if satellite:
        return satellite
    satellite = models.Satellite(name=name, orbit_regime="LEO", status="active")
    db.add(satellite)
    db.flush()
    return satellite


def _get_or_create_space_object(
    db: Session, name: str, norad_id: Optional[int], is_operator_asset: bool
) -> models.SpaceObject:
    query = db.query(models.SpaceObject).filter(models.SpaceObject.name == name)
    if norad_id is not None:
        query = db.query(models.SpaceObject).filter(models.SpaceObject.norad_cat_id == norad_id)
    space_object = query.filter(models.SpaceObject.is_operator_asset.is_(is_operator_asset)).first()
    if space_object:
        return space_object
    space_object = models.SpaceObject(
        norad_cat_id=norad_id,
        name=name,
        object_type="PAYLOAD",
        international_designator=None,
        source_id=None,
        is_operator_asset=is_operator_asset,
    )
    db.add(space_object)
    db.flush()
    return space_object


def seed_demo(db: Session) -> Dict[str, int]:
    source = _get_or_create_source(db)
    sat_a = _get_or_create_satellite(db, "Alpha")
    _get_or_create_satellite(db, "Beta")

    operator_object = _get_or_create_space_object(db, "Alpha", None, True)
    catalog_object = _get_or_create_space_object(db, "Catalog-Delta", 12345, False)

    epoch = datetime.utcnow()
    cov = propagation.default_covariance(source.type)

    state_a = models.OrbitState(
        satellite_id=sat_a.id,
        space_object_id=operator_object.id,
        epoch=epoch,
        state_vector=[7000, 0, 0, 0, 7.5, 0],
        covariance=cov,
        source_id=source.id,
        confidence=0.7,
    )
    state_b = models.OrbitState(
        satellite_id=None,
        space_object_id=catalog_object.id,
        epoch=epoch,
        state_vector=[7000.005, 0.002, 0.001, 0, 7.49, 0.01],
        covariance=cov,
        source_id=source.id,
        confidence=0.6,
    )

    db.add(state_a)
    db.flush()
    db.add(state_b)
    db.flush()

    events = []
    events.extend(conjunction.detect_events_for_state(db, state_a))

    for event in events:
        sigma = propagation.extract_sigma(cov)
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

    return {
        "satellites": 2,
        "events": len(events),
    }



def seed_runbooks(db: Session) -> None:
    existing = db.query(models.Runbook).count()
    if existing:
        return
    defaults = [
        (
            "high",
            "High-Risk Collision Workflow",
            [
                "Notify mission lead and open incident bridge",
                "Verify latest ephemerides from operator",
                "Review maneuver options and delta-V budget",
                "Select maneuver and log decision rationale",
            ],
        ),
        (
            "medium",
            "Medium-Risk Review Workflow",
            [
                "Validate conjunction geometry",
                "Check upcoming maneuvers and constraints",
                "Monitor for updated tracking data",
                "Escalate if risk increases",
            ],
        ),
        (
            "low",
            "Low-Risk Monitoring Workflow",
            [
                "Log event and monitor updates",
                "No immediate action required",
                "Reassess at next data update",
            ],
        ),
    ]
    for risk_band, template_name, steps in defaults:
        db.add(
            models.Runbook(
                risk_band=risk_band,
                template_name=template_name,
                steps_json=steps,
            )
        )
