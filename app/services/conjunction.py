from datetime import datetime, timedelta
from typing import Callable, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.services import propagation

SCREENING_VOLUME_KM = 10.0
CATALOG_ALTITUDE_WINDOW_KM = 200.0
TCA_WINDOW_DAYS = 7

# Conjunction search parameters. These trade accuracy vs runtime.
# The algorithm uses segment anchors across the window, then a local refinement.
ANCHOR_STEP_HOURS = 12
REFINE_CANDIDATES = 2
REFINE_MAX_ITERS = 10
REFINE_MAX_STEP_SECONDS = 1800.0  # avoid unstable jumps
PREDICTED_MISS_PREFILTER_KM = SCREENING_VOLUME_KM * 20.0  # only refine likely candidates


def find_recent_states(db: Session, satellite_id: int) -> List[models.OrbitState]:
    cutoff = datetime.utcnow() - timedelta(days=TCA_WINDOW_DAYS)
    return (
        db.query(models.OrbitState)
        .filter(models.OrbitState.satellite_id == satellite_id)
        .filter(models.OrbitState.epoch >= cutoff)
        .order_by(models.OrbitState.epoch.desc())
        .all()
    )


def _latest_tles_by_object(db: Session, object_ids: List[int]) -> dict[int, models.TleRecord]:
    if not object_ids:
        return {}
    latest = (
        db.query(
            models.TleRecord.space_object_id,
            func.max(models.TleRecord.epoch).label("max_epoch"),
        )
        .filter(models.TleRecord.space_object_id.in_(object_ids))
        .group_by(models.TleRecord.space_object_id)
        .subquery()
    )
    rows = (
        db.query(models.TleRecord)
        .join(
            latest,
            (models.TleRecord.space_object_id == latest.c.space_object_id)
            & (models.TleRecord.epoch == latest.c.max_epoch),
        )
        .all()
    )
    return {row.space_object_id: row for row in rows}


def _build_propagator(
    orbit_state: models.OrbitState, tle_record: Optional[models.TleRecord]
) -> Callable[[datetime], List[float]]:
    if tle_record is not None:
        try:
            return propagation.make_sgp4_propagator(tle_record.line1, tle_record.line2)
        except Exception:
            # Fall back to 2-body if SGP4 is unavailable or the TLE is malformed.
            pass
    return propagation.make_two_body_propagator(orbit_state.epoch, orbit_state.state_vector)


def _relative_state_at(
    primary: Callable[[datetime], List[float]],
    secondary: Callable[[datetime], List[float]],
    t: datetime,
) -> Tuple[List[float], List[float]]:
    s1 = primary(t)
    s2 = secondary(t)
    r1 = propagation.position_from_state(s1)
    v1 = propagation.velocity_from_state(s1)
    r2 = propagation.position_from_state(s2)
    v2 = propagation.velocity_from_state(s2)
    r_rel = [r2[i] - r1[i] for i in range(3)]
    v_rel = [v2[i] - v1[i] for i in range(3)]
    return r_rel, v_rel


def _predict_segment_min(
    primary: Callable[[datetime], List[float]],
    secondary: Callable[[datetime], List[float]],
    anchor: datetime,
    half_segment_seconds: float,
    t_start: datetime,
    t_end: datetime,
) -> Tuple[datetime, float]:
    """Linear relative-motion prediction for a single time segment."""
    r_rel, v_rel = _relative_state_at(primary, secondary, anchor)
    v2 = propagation.dot(v_rel, v_rel)
    dt = 0.0 if v2 <= 1e-12 else -propagation.dot(r_rel, v_rel) / v2
    dt = max(-half_segment_seconds, min(half_segment_seconds, dt))
    guess = anchor + timedelta(seconds=dt)
    if guess < t_start:
        guess = t_start
    if guess > t_end:
        guess = t_end
    # Predicted miss distance under constant v_rel.
    r_pred = [r_rel[i] + v_rel[i] * dt for i in range(3)]
    miss = propagation.norm(r_pred)
    return guess, float(miss)


def _refine_tca(
    primary: Callable[[datetime], List[float]],
    secondary: Callable[[datetime], List[float]],
    initial_guess: datetime,
    t_start: datetime,
    t_end: datetime,
) -> Tuple[datetime, List[float], List[float]]:
    """Iteratively solve for d/dt ||r_rel||^2 = 0 using local linearization.

    Returns (tca, r_rel_km, v_rel_km_s) at the final iterate.
    """
    t = initial_guess
    for _ in range(REFINE_MAX_ITERS):
        r_rel, v_rel = _relative_state_at(primary, secondary, t)
        v2 = propagation.dot(v_rel, v_rel)
        if v2 <= 1e-12:
            break
        dt = -propagation.dot(r_rel, v_rel) / v2
        dt = max(-REFINE_MAX_STEP_SECONDS, min(REFINE_MAX_STEP_SECONDS, dt))
        if abs(dt) < 0.1:
            break
        t_next = t + timedelta(seconds=dt)
        if t_next < t_start:
            t_next = t_start
        if t_next > t_end:
            t_next = t_end
        # If we're pinned to the boundary, no point iterating.
        if t_next == t:
            break
        t = t_next

    r_rel, v_rel = _relative_state_at(primary, secondary, t)
    return propagation.utc_naive(t), [float(x) for x in r_rel], [float(x) for x in v_rel]


def detect_events_for_state(db: Session, state: models.OrbitState) -> List[models.ConjunctionEvent]:
    events: List[models.ConjunctionEvent] = []
    if state.satellite_id is None:
        return events

    t_start = propagation.utc_naive(state.epoch)
    t_end = t_start + timedelta(days=TCA_WINDOW_DAYS)

    latest_other = (
        db.query(
            models.OrbitState.space_object_id,
            func.max(models.OrbitState.epoch).label("max_epoch"),
        )
        .filter(models.OrbitState.space_object_id.isnot(None))
        .filter(models.OrbitState.satellite_id.is_(None))
        .group_by(models.OrbitState.space_object_id)
        .subquery()
    )
    other_states = (
        db.query(models.OrbitState)
        .join(
            latest_other,
            (models.OrbitState.space_object_id == latest_other.c.space_object_id)
            & (models.OrbitState.epoch == latest_other.c.max_epoch),
        )
        .all()
    )

    tle_object_ids = [o.space_object_id for o in other_states if o.space_object_id is not None]
    if state.space_object_id is not None:
        tle_object_ids.append(state.space_object_id)
    tle_by_object = _latest_tles_by_object(db, tle_object_ids)
    primary_tle = tle_by_object.get(state.space_object_id) if state.space_object_id else None
    primary_prop = _build_propagator(state, primary_tle)

    alt1 = propagation.altitude_km(primary_prop(t_start))

    # Cache primary states at anchor points to avoid re-propagating for every catalog object.
    anchor_step = timedelta(hours=ANCHOR_STEP_HOURS)
    anchors: List[datetime] = []
    anchor = t_start
    while anchor <= t_end:
        anchors.append(anchor)
        anchor += anchor_step
    primary_anchor = {}
    for a in anchors:
        try:
            s1 = primary_prop(a)
        except Exception:
            continue
        primary_anchor[a] = (
            propagation.position_from_state(s1),
            propagation.velocity_from_state(s1),
        )

    half_seg_seconds = (ANCHOR_STEP_HOURS * 3600.0) / 2.0
    for other in other_states:
        tle = tle_by_object.get(other.space_object_id) if other.space_object_id else None
        other_prop = _build_propagator(other, tle)

        alt2 = propagation.altitude_km(other_prop(t_start))
        if abs(alt1 - alt2) > CATALOG_ALTITUDE_WINDOW_KM:
            continue

        # Segment scan: find a few promising local minima candidates.
        guesses: List[Tuple[datetime, float]] = []
        for a in anchors:
            pv = primary_anchor.get(a)
            if pv is None:
                continue
            p1, v1 = pv
            try:
                s2 = other_prop(a)
            except Exception:
                continue
            p2 = propagation.position_from_state(s2)
            v2 = propagation.velocity_from_state(s2)
            r_rel = [p2[i] - p1[i] for i in range(3)]
            v_rel = [v2[i] - v1[i] for i in range(3)]
            v2_mag = propagation.dot(v_rel, v_rel)
            dt = 0.0 if v2_mag <= 1e-12 else -propagation.dot(r_rel, v_rel) / v2_mag
            dt = max(-half_seg_seconds, min(half_seg_seconds, dt))
            guess = a + timedelta(seconds=dt)
            if guess < t_start:
                guess = t_start
            if guess > t_end:
                guess = t_end
            r_pred = [r_rel[i] + v_rel[i] * dt for i in range(3)]
            predicted_miss = propagation.norm(r_pred)
            guesses.append((guess, float(predicted_miss)))

        if not guesses:
            continue
        guesses.sort(key=lambda item: item[1])
        guesses = guesses[:REFINE_CANDIDATES]
        if guesses[0][1] > PREDICTED_MISS_PREFILTER_KM:
            continue

        best: Optional[Tuple[datetime, float, float, List[float], List[float]]] = None
        for guess, _predicted in guesses:
            try:
                tca, r_rel, v_rel = _refine_tca(primary_prop, other_prop, guess, t_start, t_end)
            except Exception:
                continue
            miss_km = propagation.norm(r_rel)
            rel_v = propagation.norm(v_rel)
            if best is None or miss_km < best[1]:
                best = (tca, float(miss_km), float(rel_v), r_rel, v_rel)

        if best is None:
            continue
        tca, miss_km, rel_v, r_rel, v_rel = best
        if miss_km <= SCREENING_VOLUME_KM:
            # Combined (very simplified) uncertainty used for risk scoring.
            primary_cov = state.covariance or propagation.default_covariance("public")
            other_cov = other.covariance or propagation.default_covariance("public")
            hours_primary = (tca - propagation.utc_naive(state.epoch)).total_seconds() / 3600.0
            hours_other = (tca - propagation.utc_naive(other.epoch)).total_seconds() / 3600.0
            cov_primary = propagation.covariance_growth(primary_cov, hours_primary)
            cov_other = propagation.covariance_growth(other_cov, hours_other)
            combined_cov = propagation.add_covariances(cov_primary, cov_other)
            sigma_km = propagation.extract_sigma(combined_cov)
            combined_pos_cov = None
            if combined_cov is not None:
                combined_pos_cov = [
                    [float(combined_cov[i][j]) for j in range(3)] for i in range(3)
                ]

            event = models.ConjunctionEvent(
                satellite_id=state.satellite_id,
                object_id=other.space_object_id,
                space_object_id=other.space_object_id,
                tca=tca,
                miss_distance=miss_km,
                relative_velocity=rel_v,
                screening_volume=SCREENING_VOLUME_KM,
                status="open",
            )
            # Attach ephemeral context for immediate downstream risk assessment.
            event._sigma_km = sigma_km  # type: ignore[attr-defined]
            event._combined_covariance = combined_cov  # type: ignore[attr-defined]
            event._combined_pos_covariance_km2 = combined_pos_cov  # type: ignore[attr-defined]
            event._r_rel_km = r_rel  # type: ignore[attr-defined]
            event._v_rel_km_s = v_rel  # type: ignore[attr-defined]
            db.add(event)
            events.append(event)
    return events


def summarize_event(event: models.ConjunctionEvent) -> Tuple[float, float]:
    return event.miss_distance, event.relative_velocity
