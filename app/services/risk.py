from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence

from app.settings import settings
from app.services.conjunction import Encounter


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def stddev(values: Sequence[float]) -> Optional[float]:
    nums = [float(v) for v in values if v is not None]
    if len(nums) < 2:
        return None
    mean = sum(nums) / len(nums)
    var = sum((v - mean) ** 2 for v in nums) / (len(nums) - 1)
    return float(math.sqrt(max(0.0, var)))


@dataclass(frozen=True)
class RiskResult:
    risk_score: float
    risk_tier: str
    confidence_score: float
    confidence_label: str
    drivers: list[str]
    details: Dict[str, Any]


def _confidence_age_factor(source_type: str, age_hours: float) -> float:
    st = (source_type or "").lower()
    if st in {"commercial", "ephemeris"}:
        return 1.0
    max_age = float(settings.tle_max_age_hours_for_confidence)
    if max_age <= 0:
        return 0.2
    return clamp(1.0 - (float(age_hours) / max_age), 0.2, 1.0)


def _confidence_label(score: float) -> str:
    if score >= 0.80:
        return "A"
    if score >= 0.60:
        return "B"
    if score >= 0.40:
        return "C"
    return "D"


def assess_encounter(
    encounter: Encounter,
    *,
    now: Optional[object] = None,
    dt_hours: float,
    primary_conf: float,
    secondary_conf: float,
    primary_source_type: str,
    secondary_source_type: str,
    primary_age_hours: float,
    secondary_age_hours: float,
    stability_std_km: Optional[float] = None,
) -> RiskResult:
    miss_km = float(encounter.miss_distance_km)
    rel_speed = float(encounter.relative_velocity_km_s)

    sep = clamp(1.0 - (miss_km / float(settings.screening_volume_km)), 0.0, 1.0)
    time = clamp((float(settings.time_critical_hours) - float(dt_hours)) / float(settings.time_critical_hours), 0.0, 1.0)
    speed = clamp(rel_speed / 15.0, 0.0, 1.0)

    risk_score = float(0.60 * sep + 0.25 * time + 0.15 * speed)

    if miss_km <= float(settings.risk_high_miss_km) or risk_score >= float(settings.risk_high_score):
        tier = "high"
    elif miss_km <= float(settings.risk_watch_miss_km) or risk_score >= float(settings.risk_watch_score):
        tier = "watch"
    else:
        tier = "low"

    p_age_factor = _confidence_age_factor(primary_source_type, primary_age_hours)
    s_age_factor = _confidence_age_factor(secondary_source_type, secondary_age_hours)
    p_adj = clamp(float(primary_conf) * p_age_factor, 0.0, 1.0)
    s_adj = clamp(float(secondary_conf) * s_age_factor, 0.0, 1.0)

    stability_factor = 1.0
    if stability_std_km is not None:
        stability_factor = clamp(1.0 - (float(stability_std_km) / 5.0), 0.3, 1.0)

    confidence_score = clamp(min(p_adj, s_adj) * stability_factor, 0.0, 1.0)
    label = _confidence_label(confidence_score)

    components = {
        "min_separation": sep,
        "time_to_tca": time,
        "relative_speed": speed,
        "data_age": clamp(1.0 - max(primary_age_hours, secondary_age_hours) / float(settings.tle_max_age_hours_for_confidence), 0.0, 1.0),
        "stability": stability_factor,
    }
    drivers = sorted(components.items(), key=lambda kv: kv[1], reverse=True)
    top_drivers = [name for name, _ in drivers[:3]]

    details: Dict[str, Any] = {
        "miss_distance_km": miss_km,
        "relative_velocity_km_s": rel_speed,
        "dt_hours": float(dt_hours),
        "components": components,
        "thresholds": {
            "screening_volume_km": float(settings.screening_volume_km),
            "time_critical_hours": float(settings.time_critical_hours),
            "risk_high_score": float(settings.risk_high_score),
            "risk_watch_score": float(settings.risk_watch_score),
            "risk_high_miss_km": float(settings.risk_high_miss_km),
            "risk_watch_miss_km": float(settings.risk_watch_miss_km),
            "tle_max_age_hours_for_confidence": float(settings.tle_max_age_hours_for_confidence),
        },
        "confidence_inputs": {
            "primary": {
                "base": float(primary_conf),
                "age_hours": float(primary_age_hours),
                "source_type": primary_source_type,
                "age_factor": float(p_age_factor),
                "adjusted": float(p_adj),
            },
            "secondary": {
                "base": float(secondary_conf),
                "age_hours": float(secondary_age_hours),
                "source_type": secondary_source_type,
                "age_factor": float(s_age_factor),
                "adjusted": float(s_adj),
            },
            "stability_std_km": float(stability_std_km) if stability_std_km is not None else None,
            "stability_factor": float(stability_factor),
        },
    }

    return RiskResult(
        risk_score=risk_score,
        risk_tier=tier,
        confidence_score=confidence_score,
        confidence_label=label,
        drivers=top_drivers,
        details=details,
    )

