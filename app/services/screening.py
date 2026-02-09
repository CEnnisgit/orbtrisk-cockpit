from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.settings import settings
from app.services import conjunction, propagation, risk
from app.services.state_sources import StateEstimate, build_state_estimate


CATALOG_ALTITUDE_WINDOW_KM = 200.0
MATCH_TCA_WINDOW_HOURS = 6.0


@dataclass(frozen=True)
class ScreeningResult:
    satellite_id: int
    screened_at: datetime
    events_updated: int
    events_created: int
    updates_created: int


def _select_primary_state(db: Session, satellite_id: int, now: datetime) -> Optional[models.OrbitState]:
    query = (
        db.query(models.OrbitState)
        .filter(models.OrbitState.satellite_id == satellite_id)
        .filter((models.OrbitState.valid_to.is_(None)) | (models.OrbitState.valid_to >= now))
        .order_by(models.OrbitState.confidence.desc(), models.OrbitState.epoch.desc())
    )
    return query.first()


def _latest_valid_secondary_states(db: Session, now: datetime) -> list[models.OrbitState]:
    latest_other = (
        db.query(
            models.OrbitState.space_object_id,
            func.max(models.OrbitState.epoch).label("max_epoch"),
        )
        .filter(models.OrbitState.space_object_id.isnot(None))
        .filter(models.OrbitState.satellite_id.is_(None))
        .filter((models.OrbitState.valid_to.is_(None)) | (models.OrbitState.valid_to >= now))
        .group_by(models.OrbitState.space_object_id)
        .subquery()
    )
    return (
        db.query(models.OrbitState)
        .join(
            latest_other,
            (models.OrbitState.space_object_id == latest_other.c.space_object_id)
            & (models.OrbitState.epoch == latest_other.c.max_epoch),
        )
        .all()
    )


def _find_matching_event(
    db: Session,
    *,
    satellite_id: int,
    space_object_id: int,
    tca: datetime,
) -> Optional[models.ConjunctionEvent]:
    window = timedelta(hours=MATCH_TCA_WINDOW_HOURS)
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


def screen_satellite(db: Session, satellite_id: int, *, horizon_days: Optional[int] = None) -> ScreeningResult:
    now = datetime.utcnow()
    horizon = int(horizon_days or settings.screening_horizon_days)
    horizon = max(1, min(14, horizon))
    t_start = now
    t_end = now + timedelta(days=horizon)

    primary_state = _select_primary_state(db, satellite_id, now)
    if primary_state is None:
        return ScreeningResult(
            satellite_id=satellite_id,
            screened_at=now,
            events_updated=0,
            events_created=0,
            updates_created=0,
        )

    primary_est = build_state_estimate(db, primary_state)

    params = conjunction.ConjunctionParams(
        screening_volume_km=float(settings.screening_volume_km),
        predicted_miss_prefilter_km=float(settings.screening_volume_km) * 20.0,
    )

    # Precompute primary altitude to filter secondaries quickly.
    try:
        primary_alt = propagation.altitude_km(primary_est.propagate(t_start))
    except Exception:
        primary_alt = 0.0

    secondaries = _latest_valid_secondary_states(db, now)

    updated_event_ids: set[int] = set()
    events_updated = 0
    events_created = 0
    updates_created = 0

    for secondary_state in secondaries:
        if secondary_state.space_object_id is None:
            continue
        secondary_est = build_state_estimate(db, secondary_state)

        try:
            secondary_alt = propagation.altitude_km(secondary_est.propagate(t_start))
        except Exception:
            continue
        if abs(primary_alt - secondary_alt) > float(CATALOG_ALTITUDE_WINDOW_KM):
            continue

        encounter = conjunction.compute_close_approach(primary_est, secondary_est, t_start, t_end, params)
        if encounter is None:
            continue

        event = _find_matching_event(
            db,
            satellite_id=satellite_id,
            space_object_id=secondary_state.space_object_id,
            tca=encounter.tca,
        )
        if event is None:
            event = models.ConjunctionEvent(
                satellite_id=satellite_id,
                space_object_id=secondary_state.space_object_id,
                object_id=secondary_state.space_object_id,
                tca=encounter.tca,
                miss_distance=float(encounter.miss_distance_km),
                relative_velocity=float(encounter.relative_velocity_km_s),
                screening_volume=float(settings.screening_volume_km),
                status="open",
                is_active=True,
                last_seen_at=now,
            )
            db.add(event)
            db.flush()
            events_created += 1
        else:
            events_updated += 1

        # Compute RTN projections for trust-building visuals.
        s1 = primary_est.propagate(encounter.tca)
        basis = conjunction.rtn_basis_from_primary_state(
            propagation.position_from_state(s1),
            propagation.velocity_from_state(s1),
        )
        r_rtn = conjunction.project_to_rtn(encounter.r_rel_eci_km, basis)
        v_rtn = conjunction.project_to_rtn(encounter.v_rel_eci_km_s, basis)

        # Compute history-based stability (stddev of last 3 miss distances).
        recent_updates = (
            db.query(models.ConjunctionEventUpdate)
            .filter(models.ConjunctionEventUpdate.event_id == event.id)
            .order_by(models.ConjunctionEventUpdate.computed_at.desc())
            .limit(3)
            .all()
        )
        miss_hist = [float(u.miss_distance_km) for u in recent_updates if u.miss_distance_km is not None]
        stability_std = risk.stddev(miss_hist) if len(miss_hist) >= 2 else None

        # Data age (hours) for confidence scoring.
        primary_age_h = (now - propagation.utc_naive(primary_state.epoch)).total_seconds() / 3600.0
        secondary_age_h = (now - propagation.utc_naive(secondary_state.epoch)).total_seconds() / 3600.0

        scored = risk.assess_encounter(
            encounter,
            now=now,
            dt_hours=(encounter.tca - now).total_seconds() / 3600.0,
            primary_conf=float(primary_state.confidence or 0.0),
            secondary_conf=float(secondary_state.confidence or 0.0),
            primary_source_type=str(primary_est.source_type),
            secondary_source_type=str(secondary_est.source_type),
            primary_age_hours=float(primary_age_h),
            secondary_age_hours=float(secondary_age_h),
            stability_std_km=stability_std,
        )

        update = models.ConjunctionEventUpdate(
            event_id=event.id,
            computed_at=now,
            primary_orbit_state_id=primary_state.id,
            secondary_orbit_state_id=secondary_state.id,
            primary_tle_record_id=primary_est.tle_record_id,
            secondary_tle_record_id=secondary_est.tle_record_id,
            tca=encounter.tca,
            miss_distance_km=float(encounter.miss_distance_km),
            relative_velocity_km_s=float(encounter.relative_velocity_km_s),
            screening_volume_km=float(settings.screening_volume_km),
            r_rel_eci_km=encounter.r_rel_eci_km,
            v_rel_eci_km_s=encounter.v_rel_eci_km_s,
            r_rel_rtn_km=r_rtn,
            v_rel_rtn_km_s=v_rtn,
            risk_tier=scored.risk_tier,
            risk_score=float(scored.risk_score),
            confidence_score=float(scored.confidence_score),
            confidence_label=scored.confidence_label,
            drivers_json=scored.drivers,
            details_json=scored.details,
        )
        db.add(update)
        db.flush()
        updates_created += 1

        # Update parent event snapshot fields.
        event.tca = encounter.tca
        event.miss_distance = float(encounter.miss_distance_km)
        event.relative_velocity = float(encounter.relative_velocity_km_s)
        event.screening_volume = float(settings.screening_volume_km)
        event.risk_tier = scored.risk_tier
        event.risk_score = float(scored.risk_score)
        event.confidence_score = float(scored.confidence_score)
        event.confidence_label = scored.confidence_label
        event.current_update_id = update.id
        event.last_seen_at = now
        event.is_active = True

        updated_event_ids.add(event.id)

    # Noise reduction: mark unseen future events as inactive.
    stale = (
        db.query(models.ConjunctionEvent)
        .filter(models.ConjunctionEvent.satellite_id == satellite_id)
        .filter(models.ConjunctionEvent.tca >= now)
        .filter(models.ConjunctionEvent.is_active.is_(True))
        .all()
    )
    for event in stale:
        if event.id not in updated_event_ids:
            event.is_active = False

    db.commit()
    return ScreeningResult(
        satellite_id=satellite_id,
        screened_at=now,
        events_updated=events_updated,
        events_created=events_created,
        updates_created=updates_created,
    )


def cleanup_retention(db: Session) -> None:
    """Best-effort retention cleanup for SQLite/Postgres.

    Keeps tables bounded to reduce local-db bloat.
    """

    now = datetime.utcnow()
    orbit_cutoff = now - timedelta(days=int(settings.orbit_state_retention_days))
    tle_cutoff = now - timedelta(days=int(settings.tle_record_retention_days))

    db.query(models.OrbitState).filter(models.OrbitState.epoch < orbit_cutoff).delete(synchronize_session=False)
    db.query(models.TleRecord).filter(models.TleRecord.epoch < tle_cutoff).delete(synchronize_session=False)
    db.commit()
