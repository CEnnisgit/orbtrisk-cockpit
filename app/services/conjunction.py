from datetime import datetime, timedelta
from typing import List, Tuple

from sqlalchemy.orm import Session

from app import models
from app.services import propagation

SCREENING_VOLUME_KM = 10.0
CATALOG_ALTITUDE_WINDOW_KM = 200.0
TCA_WINDOW_DAYS = 7


def find_recent_states(db: Session, satellite_id: int) -> List[models.OrbitState]:
    cutoff = datetime.utcnow() - timedelta(days=TCA_WINDOW_DAYS)
    return (
        db.query(models.OrbitState)
        .filter(models.OrbitState.satellite_id == satellite_id)
        .filter(models.OrbitState.epoch >= cutoff)
        .order_by(models.OrbitState.epoch.desc())
        .all()
    )


def detect_events_for_state(db: Session, state: models.OrbitState) -> List[models.ConjunctionEvent]:
    events: List[models.ConjunctionEvent] = []
    if state.satellite_id is None:
        return events

    other_states = (
        db.query(models.OrbitState)
        .filter(models.OrbitState.space_object_id.isnot(None))
        .filter(models.OrbitState.satellite_id.is_(None))
        .order_by(models.OrbitState.epoch.desc())
        .all()
    )
    alt1 = propagation.altitude_km(state.state_vector)
    for other in other_states:
        alt2 = propagation.altitude_km(other.state_vector)
        if abs(alt1 - alt2) > CATALOG_ALTITUDE_WINDOW_KM:
            continue
        p1 = propagation.position_from_state(state.state_vector)
        p2 = propagation.position_from_state(other.state_vector)
        miss_km = propagation.miss_distance(p1, p2)
        if miss_km <= SCREENING_VOLUME_KM:
            v1 = propagation.velocity_from_state(state.state_vector)
            v2 = propagation.velocity_from_state(other.state_vector)
            rel_v = propagation.relative_velocity(v1, v2)
            event = models.ConjunctionEvent(
                satellite_id=state.satellite_id,
                object_id=other.space_object_id,
                space_object_id=other.space_object_id,
                tca=state.epoch,
                miss_distance=miss_km,
                relative_velocity=rel_v,
                screening_volume=SCREENING_VOLUME_KM,
                status="open",
            )
            db.add(event)
            events.append(event)
    return events


def summarize_event(event: models.ConjunctionEvent) -> Tuple[float, float]:
    return event.miss_distance, event.relative_velocity
