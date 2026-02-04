import math
from typing import Dict, Tuple

from app.models import ConjunctionEvent
from app.services import propagation


def collision_probability(miss_distance_km: float, sigma_km: float) -> float:
    sigma = max(0.1, sigma_km)
    return math.exp(-((miss_distance_km / sigma) ** 2))


def risk_components(event: ConjunctionEvent, sigma_km: float) -> Dict[str, float]:
    poc = collision_probability(event.miss_distance, sigma_km)
    mission_impact = min(1.0, event.relative_velocity / 15.0)
    fuel_cost = min(1.0, 1.0 - (event.miss_distance / event.screening_volume))
    regulatory_exposure = min(1.0, poc * 2.0)
    return {
        "collision_probability": poc,
        "mission_impact": mission_impact,
        "fuel_cost": fuel_cost,
        "regulatory_exposure": regulatory_exposure,
    }


def weighted_risk_score(components: Dict[str, float]) -> float:
    weights = {
        "collision_probability": 0.5,
        "mission_impact": 0.2,
        "fuel_cost": 0.2,
        "regulatory_exposure": 0.1,
    }
    return sum(components[key] * weights[key] for key in weights)


def sensitivity_analysis(components: Dict[str, float]) -> Dict[str, float]:
    ranked = sorted(components.items(), key=lambda item: item[1], reverse=True)
    return {"top_drivers": [name for name, _ in ranked[:3]]}


def assess_event(event: ConjunctionEvent, sigma_km: float) -> Tuple[float, float, Dict, Dict]:
    components = risk_components(event, sigma_km)
    risk_score = weighted_risk_score(components)
    poc = components["collision_probability"]
    sensitivity = sensitivity_analysis(components)
    return poc, risk_score, components, sensitivity
