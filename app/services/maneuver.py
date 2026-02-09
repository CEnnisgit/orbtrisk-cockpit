from datetime import timedelta
from typing import List, Optional

from app.models import ConjunctionEvent
from app.services import propagation, risk


def generate_options(event: ConjunctionEvent, risk_score: float) -> List[dict]:
    base_time = event.tca

    # Prefer "real-ish" avoidance windows leading up to TCA.
    lead_hours = [6.0, 3.0, 1.0]
    window_minutes = 30
    windows = []
    for hours in lead_hours:
        start = base_time - timedelta(hours=hours)
        end = start + timedelta(minutes=window_minutes)
        windows.append((start, end))

    r_rel = getattr(event, "_r_rel_km", None)
    v_rel = getattr(event, "_v_rel_km_s", None)
    cov_pos = getattr(event, "_combined_pos_covariance_km2", None)

    # If we don't have geometry/covariance context, keep the old stub behavior.
    if not (
        isinstance(r_rel, list)
        and isinstance(v_rel, list)
        and isinstance(cov_pos, list)
        and len(r_rel) == 3
        and len(v_rel) == 3
        and len(cov_pos) >= 3
    ):
        options = []
        for idx, window in enumerate(windows):
            delta_v = 0.05 + idx * 0.02
            risk_after = max(0.0, risk_score - (0.15 + idx * 0.05))
            fuel_cost = delta_v * 10
            options.append(
                {
                    "delta_v": delta_v,
                    "time_window_start": window[0],
                    "time_window_end": window[1],
                    "risk_after": risk_after,
                    "fuel_cost": fuel_cost,
                    "is_recommended": idx == 0,
                }
            )
        return options

    # Simple ballistic approximation: delta-v on the primary shifts the miss vector at TCA by ~delta_v * lead_time.
    # We choose delta-v direction to increase encounter-plane separation (projection orthogonal to v_rel).
    u_hat, _e1, _e2 = propagation.orthonormal_basis_from_u(v_rel)
    along = propagation.dot(r_rel, u_hat)
    r_plane = [float(r_rel[i] - along * u_hat[i]) for i in range(3)]
    dir_hat = propagation.safe_unit(r_plane, fallback=propagation.safe_unit(r_rel))

    # Default magnitudes are in km/s (2, 3, 5 cm/s).
    dv_schedule_km_s = [0.00002, 0.00003, 0.00005]

    cov = cov_pos
    sigma_km = max(0.1, ((float(cov[0][0]) + float(cov[1][1]) + float(cov[2][2])) / 3.0) ** 0.5)
    hbr_override = getattr(event, "_hard_body_radius_m", None)

    options: List[dict] = []
    for idx, window in enumerate(windows):
        dv_km_s = dv_schedule_km_s[min(idx, len(dv_schedule_km_s) - 1)]
        t_mid = window[0] + (window[1] - window[0]) / 2
        lead_s = (base_time - t_mid).total_seconds()
        if lead_s <= 0:
            lead_s = 0.0

        # Apply dv on primary: r_new = r_rel - dv_vec * lead_time.
        dv_vec = [-dir_hat[i] * dv_km_s for i in range(3)]
        r_new = [float(r_rel[i] - dv_vec[i] * lead_s) for i in range(3)]
        miss_new = propagation.norm(r_new)

        temp = ConjunctionEvent(
            satellite_id=event.satellite_id,
            object_id=event.object_id,
            space_object_id=event.space_object_id,
            tca=event.tca,
            miss_distance=float(miss_new),
            relative_velocity=float(event.relative_velocity),
            screening_volume=float(event.screening_volume),
            status=event.status,
        )
        temp._r_rel_km = r_new  # type: ignore[attr-defined]
        temp._v_rel_km_s = v_rel  # type: ignore[attr-defined]
        temp._combined_pos_covariance_km2 = cov_pos  # type: ignore[attr-defined]
        if hbr_override is not None:
            temp._hard_body_radius_m = hbr_override  # type: ignore[attr-defined]

        _poc_after, risk_after, _components_after, _sensitivity_after = risk.assess_event(temp, sigma_km)

        options.append(
            {
                "delta_v": float(dv_km_s),
                "time_window_start": window[0],
                "time_window_end": window[1],
                "risk_after": float(risk_after),
                # A crude proxy: treat delta-v magnitude (m/s) as "fuel cost" for display.
                "fuel_cost": float(dv_km_s * 1000.0),
                "is_recommended": False,
            }
        )

    # Recommend the option with the lowest predicted risk_after; tie-break on lower delta-v.
    best_idx: Optional[int] = None
    for i, opt in enumerate(options):
        if best_idx is None:
            best_idx = i
            continue
        if opt["risk_after"] < options[best_idx]["risk_after"]:
            best_idx = i
            continue
        if opt["risk_after"] == options[best_idx]["risk_after"] and opt["fuel_cost"] < options[best_idx]["fuel_cost"]:
            best_idx = i

    if best_idx is not None:
        options[best_idx]["is_recommended"] = True

    return options
