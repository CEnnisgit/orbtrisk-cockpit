from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence, Tuple

from app.services import frames, propagation
from app.services.state_sources import StateEstimate


@dataclass(frozen=True)
class ConjunctionParams:
    screening_volume_km: float = 10.0
    anchor_step_hours: int = 12
    refine_candidates: int = 2
    refine_max_iters: int = 10
    refine_max_step_seconds: float = 1800.0
    predicted_miss_prefilter_km: float = 200.0


@dataclass(frozen=True)
class Encounter:
    tca: datetime
    miss_distance_km: float
    relative_velocity_km_s: float
    r_rel_eci_km: list[float]
    v_rel_eci_km_s: list[float]


def _relative_state_at(
    primary: StateEstimate,
    secondary: StateEstimate,
    t: datetime,
) -> Tuple[list[float], list[float]]:
    s1 = frames.convert_state_vector_km(primary.propagate(t), primary.frame, "GCRS", t)
    s2 = frames.convert_state_vector_km(secondary.propagate(t), secondary.frame, "GCRS", t)
    r1 = propagation.position_from_state(s1)
    v1 = propagation.velocity_from_state(s1)
    r2 = propagation.position_from_state(s2)
    v2 = propagation.velocity_from_state(s2)
    r_rel = [r2[i] - r1[i] for i in range(3)]
    v_rel = [v2[i] - v1[i] for i in range(3)]
    return r_rel, v_rel


def _refine_tca(
    primary: StateEstimate,
    secondary: StateEstimate,
    initial_guess: datetime,
    t_start: datetime,
    t_end: datetime,
    params: ConjunctionParams,
) -> Tuple[datetime, list[float], list[float]]:
    t = initial_guess
    for _ in range(params.refine_max_iters):
        r_rel, v_rel = _relative_state_at(primary, secondary, t)
        v2 = propagation.dot(v_rel, v_rel)
        if v2 <= 1e-12:
            break
        dt = -propagation.dot(r_rel, v_rel) / v2
        dt = max(-params.refine_max_step_seconds, min(params.refine_max_step_seconds, dt))
        if abs(dt) < 0.1:
            break
        t_next = t + timedelta(seconds=dt)
        if t_next < t_start:
            t_next = t_start
        if t_next > t_end:
            t_next = t_end
        if t_next == t:
            break
        t = t_next

    r_rel, v_rel = _relative_state_at(primary, secondary, t)
    return propagation.utc_naive(t), [float(x) for x in r_rel], [float(x) for x in v_rel]


def compute_close_approach(
    primary: StateEstimate,
    secondary: StateEstimate,
    t_start: datetime,
    t_end: datetime,
    params: ConjunctionParams,
) -> Optional[Encounter]:
    t_start_n = propagation.utc_naive(t_start)
    t_end_n = propagation.utc_naive(t_end)

    anchor_step = timedelta(hours=int(params.anchor_step_hours))
    anchors = []
    anchor = t_start_n
    while anchor <= t_end_n:
        anchors.append(anchor)
        anchor += anchor_step

    half_seg_seconds = (float(params.anchor_step_hours) * 3600.0) / 2.0
    guesses: list[tuple[datetime, float]] = []
    for a in anchors:
        try:
            r_rel, v_rel = _relative_state_at(primary, secondary, a)
        except Exception:
            continue
        v2_mag = propagation.dot(v_rel, v_rel)
        dt = 0.0 if v2_mag <= 1e-12 else -propagation.dot(r_rel, v_rel) / v2_mag
        dt = max(-half_seg_seconds, min(half_seg_seconds, dt))
        guess = a + timedelta(seconds=dt)
        if guess < t_start_n:
            guess = t_start_n
        if guess > t_end_n:
            guess = t_end_n
        r_pred = [r_rel[i] + v_rel[i] * dt for i in range(3)]
        predicted_miss = propagation.norm(r_pred)
        guesses.append((guess, float(predicted_miss)))

    if not guesses:
        return None
    guesses.sort(key=lambda item: item[1])
    guesses = guesses[: max(1, int(params.refine_candidates))]
    if guesses[0][1] > float(params.predicted_miss_prefilter_km):
        return None

    best: Optional[tuple[datetime, float, float, list[float], list[float]]] = None
    for guess, _predicted in guesses:
        try:
            tca, r_rel, v_rel = _refine_tca(primary, secondary, guess, t_start_n, t_end_n, params)
        except Exception:
            continue
        miss_km = propagation.norm(r_rel)
        rel_v = propagation.norm(v_rel)
        if best is None or miss_km < best[1]:
            best = (tca, float(miss_km), float(rel_v), r_rel, v_rel)

    if best is None:
        return None
    tca, miss_km, rel_v, r_rel, v_rel = best
    if miss_km > float(params.screening_volume_km):
        return None

    return Encounter(
        tca=tca,
        miss_distance_km=float(miss_km),
        relative_velocity_km_s=float(rel_v),
        r_rel_eci_km=[float(x) for x in r_rel],
        v_rel_eci_km_s=[float(x) for x in v_rel],
    )


def rtn_basis_from_primary_state(
    r_eci_km: Sequence[float], v_eci_km_s: Sequence[float]
) -> tuple[list[float], list[float], list[float]]:
    r_hat = propagation.safe_unit(r_eci_km)
    h_vec = propagation.cross(r_eci_km, v_eci_km_s)
    n_hat = propagation.safe_unit(h_vec, fallback=(0.0, 0.0, 1.0))
    t_hat = propagation.cross(n_hat, r_hat)
    t_hat = propagation.safe_unit(t_hat, fallback=(0.0, 1.0, 0.0))
    # Ensure orthonormal-ish basis.
    n_hat = propagation.safe_unit(propagation.cross(r_hat, t_hat), fallback=n_hat)
    return [float(x) for x in r_hat], [float(x) for x in t_hat], [float(x) for x in n_hat]


def project_to_rtn(vec_eci: Sequence[float], basis: tuple[Sequence[float], Sequence[float], Sequence[float]]) -> list[float]:
    r_hat, t_hat, n_hat = basis
    return [
        float(propagation.dot(vec_eci, r_hat)),
        float(propagation.dot(vec_eci, t_hat)),
        float(propagation.dot(vec_eci, n_hat)),
    ]
