import math
from typing import Dict, Optional, Sequence, Tuple

from app.models import ConjunctionEvent
from app.settings import settings
from app.services import propagation

WEIGHTS = {
    "collision_probability": 0.5,
    "mission_impact": 0.2,
    "fuel_cost": 0.2,
    "regulatory_exposure": 0.1,
}


def _invert_2x2(mat: Sequence[Sequence[float]]) -> Tuple[Optional[list[list[float]]], float]:
    a = float(mat[0][0])
    b = float(mat[0][1])
    c = float(mat[1][0])
    d = float(mat[1][1])
    det = a * d - b * c
    if det == 0.0:
        return None, det
    inv = [[d / det, -b / det], [-c / det, a / det]]
    return inv, det


def _regularize_2x2(mat: Sequence[Sequence[float]], eps: float) -> list[list[float]]:
    # Ensure symmetry and add a small diagonal regularizer.
    a = float(mat[0][0])
    b = 0.5 * (float(mat[0][1]) + float(mat[1][0]))
    d = float(mat[1][1])
    return [[a + eps, b], [b, d + eps]]


def _encounter_plane(mu_rel_km: Sequence[float], v_rel_km_s: Sequence[float]) -> Tuple[list[float], list[float], list[float], list[float]]:
    # Returns: (mu2_km, u_hat, e1, e2)
    u_hat, e1, e2 = propagation.orthonormal_basis_from_u(v_rel_km_s)
    mu2 = [propagation.dot(mu_rel_km, e1), propagation.dot(mu_rel_km, e2)]
    return [float(mu2[0]), float(mu2[1])], u_hat, e1, e2


def _project_covariance_onto_plane(
    cov_pos_km2: Sequence[Sequence[float]], e1: Sequence[float], e2: Sequence[float]
) -> list[list[float]]:
    # cov2 = B * cov3 * B^T where rows(B) = e1,e2
    def cov_dot(u: Sequence[float], v: Sequence[float]) -> float:
        return float(
            sum(u[i] * float(cov_pos_km2[i][j]) * v[j] for i in range(3) for j in range(3))
        )

    return [
        [cov_dot(e1, e1), cov_dot(e1, e2)],
        [cov_dot(e2, e1), cov_dot(e2, e2)],
    ]


def _poc_2d_gaussian_circle(
    mu_km: Sequence[float],
    cov_km2: Sequence[Sequence[float]],
    radius_km: float,
    *,
    num_angles: int,
) -> float:
    """Probability that a 2D Gaussian N(mu, cov) falls within a radius."""
    if radius_km <= 0.0:
        return 0.0
    cov2 = _regularize_2x2(cov_km2, eps=1e-12)
    inv, det = _invert_2x2(cov2)
    if inv is None or det <= 0.0:
        cov2 = _regularize_2x2(cov_km2, eps=1e-6)
        inv, det = _invert_2x2(cov2)
        if inv is None or det <= 0.0:
            return 0.0

    mx = float(mu_km[0])
    my = float(mu_km[1])

    c = mx * (inv[0][0] * mx + inv[0][1] * my) + my * (inv[1][0] * mx + inv[1][1] * my)
    exp_c = math.exp(-0.5 * c) if c < 700 else 0.0  # avoid overflow in exp()
    if exp_c == 0.0:
        return 0.0

    total = 0.0
    n = max(12, int(num_angles))
    for k in range(n):
        theta = 2.0 * math.pi * k / n
        ux = math.cos(theta)
        uy = math.sin(theta)
        a = ux * (inv[0][0] * ux + inv[0][1] * uy) + uy * (inv[1][0] * ux + inv[1][1] * uy)
        if a <= 1e-18:
            continue
        b = ux * (inv[0][0] * mx + inv[0][1] * my) + uy * (inv[1][0] * mx + inv[1][1] * my)

        t0 = -b / a
        t1 = radius_km - b / a

        exp_b = math.exp(0.5 * (b * b) / a) if (b * b) / a < 700 else float("inf")
        if not math.isfinite(exp_b):
            # The combined exponent still has exp(-0.5*c); if this overflows we can treat it as ~0.
            continue

        term1 = (math.exp(-0.5 * a * t0 * t0) - math.exp(-0.5 * a * t1 * t1)) / a
        scale = math.sqrt(a / 2.0)
        term2 = (b / a) * math.sqrt(math.pi / (2.0 * a)) * (math.erf(scale * t1) - math.erf(scale * t0))
        total += exp_c * exp_b * (term1 + term2)

    poc = total / (n * math.sqrt(det))
    if poc < 0.0:
        return 0.0
    if poc > 1.0:
        return 1.0
    return float(poc)


def collision_probability(event: ConjunctionEvent, sigma_km: float) -> Tuple[float, Dict[str, object]]:
    """Compute probability of collision (PoC).

    Prefers a covariance-based encounter-plane method when geometry is available.
    Falls back to a simple isotropic heuristic when it is not.
    """
    hbr_m = getattr(event, "_hard_body_radius_m", None)
    hbr_m = float(hbr_m) if isinstance(hbr_m, (int, float)) else float(settings.default_hbr_m)
    hbr_km = max(0.0, hbr_m) / 1000.0

    r_rel = getattr(event, "_r_rel_km", None)
    v_rel = getattr(event, "_v_rel_km_s", None)
    cov_pos = getattr(event, "_combined_pos_covariance_km2", None)

    if (
        isinstance(r_rel, list)
        and isinstance(v_rel, list)
        and isinstance(cov_pos, list)
        and len(r_rel) == 3
        and len(v_rel) == 3
        and len(cov_pos) >= 3
    ):
        try:
            mu2, _u_hat, e1, e2 = _encounter_plane(r_rel, v_rel)
            cov2 = _project_covariance_onto_plane(cov_pos, e1, e2)
            poc = _poc_2d_gaussian_circle(
                mu2,
                cov2,
                hbr_km,
                num_angles=int(settings.poc_num_angle_steps),
            )
            return float(poc), {
                "poc_method": "encounter_plane_2d_gaussian",
                "hard_body_radius_m": float(hbr_m),
                "encounter_plane_mu_km": [float(mu2[0]), float(mu2[1])],
                "encounter_plane_cov_km2": [[float(cov2[0][0]), float(cov2[0][1])], [float(cov2[1][0]), float(cov2[1][1])]],
            }
        except Exception:
            pass

    # Fallback: isotropic heuristic based only on miss distance and sigma.
    sigma = max(0.1, float(sigma_km))
    poc = math.exp(-((float(event.miss_distance) / sigma) ** 2))
    return float(poc), {"poc_method": "heuristic_isotropic", "sigma_km": sigma}


def risk_components(event: ConjunctionEvent, sigma_km: float) -> Dict[str, object]:
    poc, poc_meta = collision_probability(event, sigma_km)
    # Normalize PoC into a 0-1 score using a commonly-used alert threshold.
    threshold = float(settings.poc_alert_threshold)
    collision_score = poc if threshold <= 0.0 else min(1.0, poc / threshold)
    mission_impact = min(1.0, event.relative_velocity / 15.0)
    fuel_cost = max(0.0, min(1.0, 1.0 - (event.miss_distance / event.screening_volume)))
    regulatory_exposure = min(1.0, collision_score * 2.0)
    components: Dict[str, object] = {
        "poc": float(poc),
        "collision_probability": float(collision_score),
        "mission_impact": mission_impact,
        "fuel_cost": fuel_cost,
        "regulatory_exposure": regulatory_exposure,
    }
    components.update(poc_meta)
    components["poc_alert_threshold"] = threshold
    return components


def weighted_risk_score(components: Dict[str, object]) -> float:
    score = 0.0
    for key, weight in WEIGHTS.items():
        value = components.get(key, 0.0)
        score += float(value) * float(weight)
    return float(score)


def sensitivity_analysis(components: Dict[str, object]) -> Dict[str, object]:
    # Rank by weighted contribution to the final risk_score (ignore metadata fields).
    contributions = []
    for key, weight in WEIGHTS.items():
        contributions.append((key, float(components.get(key, 0.0)) * float(weight)))
    ranked = sorted(contributions, key=lambda item: item[1], reverse=True)
    return {"top_drivers": [name for name, _ in ranked[:3]]}


def assess_event(event: ConjunctionEvent, sigma_km: float) -> Tuple[float, float, Dict, Dict]:
    components = risk_components(event, sigma_km)
    risk_score = weighted_risk_score(components)
    poc = float(components.get("poc", 0.0))
    sensitivity = sensitivity_analysis(components)
    return poc, risk_score, components, sensitivity
