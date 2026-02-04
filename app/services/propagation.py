from typing import List, Optional


def default_covariance(source_type: str) -> List[List[float]]:
    if source_type.lower() in {"commercial", "ephemeris"}:
        base = 0.1
    else:
        base = 1.0
    return [[base if i == j else 0.0 for j in range(6)] for i in range(6)]


def covariance_growth(covariance: List[List[float]], hours: float) -> List[List[float]]:
    growth = max(0.0, hours) * 0.05
    return [
        [covariance[i][j] + (growth if i == j else 0.0) for j in range(6)]
        for i in range(6)
    ]


def position_from_state(state_vector: List[float]) -> List[float]:
    return state_vector[:3]


def velocity_from_state(state_vector: List[float]) -> List[float]:
    return state_vector[3:6]


def norm(vec: List[float]) -> float:
    return sum(x * x for x in vec) ** 0.5


def relative_velocity(v1: List[float], v2: List[float]) -> float:
    return norm([v1[i] - v2[i] for i in range(3)])


def miss_distance(p1: List[float], p2: List[float]) -> float:
    return norm([p1[i] - p2[i] for i in range(3)])


def propagate_stub(state_vector: List[float], hours: float) -> List[float]:
    return state_vector


def extract_sigma(covariance: Optional[List[List[float]]]) -> float:
    if not covariance:
        return 1.0
    return max(0.1, sum(covariance[i][i] for i in range(min(3, len(covariance)))) / 3) ** 0.5


def altitude_km(state_vector: List[float]) -> float:
    earth_radius_km = 6371.0
    position = position_from_state(state_vector)
    return max(0.0, norm(position) - earth_radius_km)
