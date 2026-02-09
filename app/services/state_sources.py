from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from sqlalchemy.orm import Session

from app import models
from app.services import propagation


@dataclass(frozen=True)
class StateEstimate:
    orbit_state_id: int
    epoch: datetime
    frame: str
    source_name: str
    source_type: str
    confidence: float
    valid_from: Optional[datetime]
    valid_to: Optional[datetime]
    tle_record_id: Optional[int]
    propagate: Callable[[datetime], list[float]]


def _best_tle_for_orbit_state(db: Session, orbit_state: models.OrbitState) -> tuple[Optional[models.TleRecord], Optional[int]]:
    provenance = orbit_state.provenance_json or {}
    tle_record_id = provenance.get("tle_record_id") if isinstance(provenance, dict) else None
    if isinstance(tle_record_id, int):
        tle_record = db.get(models.TleRecord, tle_record_id)
        if tle_record is not None:
            return tle_record, tle_record.id

    # Only treat TEME states as TLE-derived (unless an explicit tle_record_id was provided).
    frame = str(orbit_state.frame or "").upper()
    if frame != "TEME":
        return None, None
    if orbit_state.space_object_id is None:
        return None, None

    query = db.query(models.TleRecord).filter(models.TleRecord.space_object_id == orbit_state.space_object_id)
    # Prefer a TLE at-or-before the orbit state's epoch.
    tle_record = (
        query.filter(models.TleRecord.epoch <= orbit_state.epoch)
        .order_by(models.TleRecord.epoch.desc())
        .first()
    )
    if tle_record is None:
        tle_record = query.order_by(models.TleRecord.epoch.desc()).first()
    return (tle_record, tle_record.id) if tle_record is not None else (None, None)


def build_state_estimate(db: Session, orbit_state: models.OrbitState) -> StateEstimate:
    source = orbit_state.source
    source_name = source.name if source else "unknown"
    source_type = source.type if source else "unknown"

    tle_record, tle_record_id = _best_tle_for_orbit_state(db, orbit_state)

    propagator: Callable[[datetime], list[float]]
    if tle_record is not None:
        try:
            propagator = propagation.make_sgp4_propagator(tle_record.line1, tle_record.line2)
        except Exception:
            propagator = propagation.make_two_body_propagator(orbit_state.epoch, orbit_state.state_vector)
    else:
        propagator = propagation.make_two_body_propagator(orbit_state.epoch, orbit_state.state_vector)

    return StateEstimate(
        orbit_state_id=int(orbit_state.id),
        epoch=orbit_state.epoch,
        frame=str(orbit_state.frame or "ECI"),
        source_name=str(source_name),
        source_type=str(source_type),
        confidence=float(orbit_state.confidence or 0.0),
        valid_from=orbit_state.valid_from,
        valid_to=orbit_state.valid_to,
        tle_record_id=int(tle_record_id) if isinstance(tle_record_id, int) else None,
        propagate=propagator,
    )
