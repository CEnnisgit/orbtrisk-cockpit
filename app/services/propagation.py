import math
from datetime import datetime, timezone
from typing import Callable, List, Optional, Sequence, Tuple

try:
    from sgp4.api import Satrec
    from sgp4.conveniences import jday_datetime
except Exception:  # pragma: no cover
    Satrec = None  # type: ignore[assignment]
    jday_datetime = None  # type: ignore[assignment]

# Standard gravitational parameter for Earth.
MU_EARTH_KM3_S2 = 398600.4418


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

def add_covariances(a: Optional[List[List[float]]], b: Optional[List[List[float]]]) -> Optional[List[List[float]]]:
    if a is None and b is None:
        return None
    if a is None:
        return [row[:] for row in b]  # type: ignore[arg-type]
    if b is None:
        return [row[:] for row in a]
    size = max(len(a), len(b))
    out: List[List[float]] = []
    for i in range(size):
        row = []
        for j in range(size):
            va = a[i][j] if i < len(a) and j < len(a[i]) else 0.0
            vb = b[i][j] if i < len(b) and j < len(b[i]) else 0.0
            row.append(float(va) + float(vb))
        out.append(row)
    return out


def position_from_state(state_vector: List[float]) -> List[float]:
    return state_vector[:3]


def velocity_from_state(state_vector: List[float]) -> List[float]:
    return state_vector[3:6]


def norm(vec: Sequence[float]) -> float:
    return sum(x * x for x in vec) ** 0.5


def relative_velocity(v1: List[float], v2: List[float]) -> float:
    return norm([v1[i] - v2[i] for i in range(3)])


def miss_distance(p1: List[float], p2: List[float]) -> float:
    return norm([p1[i] - p2[i] for i in range(3)])


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return float(sum(a[i] * b[i] for i in range(min(len(a), len(b)))))


def cross(a: Sequence[float], b: Sequence[float]) -> List[float]:
    return [
        float(a[1] * b[2] - a[2] * b[1]),
        float(a[2] * b[0] - a[0] * b[2]),
        float(a[0] * b[1] - a[1] * b[0]),
    ]


def _stumpff_c2(z: float) -> float:
    if abs(z) < 1e-8:
        # 1/2 - z/24 + z^2/720 - ...
        return 0.5 - z / 24.0 + (z * z) / 720.0
    if z > 0.0:
        s = math.sqrt(z)
        return (1.0 - math.cos(s)) / z
    s = math.sqrt(-z)
    return (math.cosh(s) - 1.0) / (-z)


def _stumpff_c3(z: float) -> float:
    if abs(z) < 1e-8:
        # 1/6 - z/120 + z^2/5040 - ...
        return 1.0 / 6.0 - z / 120.0 + (z * z) / 5040.0
    if z > 0.0:
        s = math.sqrt(z)
        return (s - math.sin(s)) / (s * s * s)
    s = math.sqrt(-z)
    return (math.sinh(s) - s) / (s * s * s)


def propagate_two_body(state_vector: Sequence[float], dt_seconds: float, mu: float = MU_EARTH_KM3_S2) -> List[float]:
    """Propagate an inertial state vector under a 2-body (Kepler) model.

    Units: km, km/s, seconds.
    """
    r0 = [float(state_vector[0]), float(state_vector[1]), float(state_vector[2])]
    v0 = [float(state_vector[3]), float(state_vector[4]), float(state_vector[5])]

    r0_mag = norm(r0)
    v0_mag = norm(v0)
    if r0_mag <= 0.0:
        return [*r0, *v0]

    sqrt_mu = math.sqrt(mu)
    vr0 = dot(r0, v0) / r0_mag
    alpha = 2.0 / r0_mag - (v0_mag * v0_mag) / mu

    # Reasonable universal variable initial guess across common regimes.
    x = sqrt_mu * abs(alpha) * dt_seconds if abs(alpha) > 1e-8 else sqrt_mu * dt_seconds / r0_mag
    if x == 0.0:
        x = 1e-6

    # Newton solve for x.
    for _ in range(50):
        z = alpha * x * x
        c2 = _stumpff_c2(z)
        c3 = _stumpff_c3(z)
        # Universal Kepler equation (Vallado).
        f = (
            (r0_mag * vr0 / sqrt_mu) * x * x * c2
            + (1.0 - alpha * r0_mag) * x * x * x * c3
            + r0_mag * x
            - sqrt_mu * dt_seconds
        )
        if abs(f) < 1e-8:
            break
        df = (
            (r0_mag * vr0 / sqrt_mu) * x * (1.0 - z * c3)
            + (1.0 - alpha * r0_mag) * x * x * c2
            + r0_mag
        )
        if df == 0.0:
            break
        step = f / df
        x -= step
        if abs(step) < 1e-10:
            break

    z = alpha * x * x
    c2 = _stumpff_c2(z)
    c3 = _stumpff_c3(z)

    f = 1.0 - (x * x / r0_mag) * c2
    g = dt_seconds - (x * x * x / sqrt_mu) * c3
    r = [f * r0[i] + g * v0[i] for i in range(3)]
    r_mag = norm(r)
    if r_mag <= 0.0:
        return [*r0, *v0]

    fdot = (sqrt_mu / (r_mag * r0_mag)) * (alpha * x * x * x * c3 - x)
    gdot = 1.0 - (x * x / r_mag) * c2
    v = [fdot * r0[i] + gdot * v0[i] for i in range(3)]
    return [float(r[0]), float(r[1]), float(r[2]), float(v[0]), float(v[1]), float(v[2])]


def utc_naive(dt: datetime) -> datetime:
    """Treat naive datetimes as UTC; convert aware datetimes to UTC and drop tzinfo.

    This avoids mixing offset-aware and offset-naive datetimes during propagation math.
    """
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def state_at_epoch(epoch: datetime, state_vector: Sequence[float], target_epoch: datetime) -> List[float]:
    epoch_n = utc_naive(epoch)
    target_n = utc_naive(target_epoch)
    dt_seconds = (target_n - epoch_n).total_seconds()
    return propagate_two_body(state_vector, dt_seconds)


def sgp4_state_at_epoch(line1: str, line2: str, target_epoch: datetime) -> List[float]:
    if Satrec is None or jday_datetime is None:  # pragma: no cover
        raise RuntimeError("sgp4 is not available in this environment")
    sat = Satrec.twoline2rv(line1, line2)
    jd, fr = jday_datetime(utc_naive(target_epoch))
    error, position, velocity = sat.sgp4(jd, fr)
    if error != 0:
        raise RuntimeError(f"SGP4 propagation failed (code {error})")
    return [float(position[0]), float(position[1]), float(position[2]), float(velocity[0]), float(velocity[1]), float(velocity[2])]


def make_two_body_propagator(epoch: datetime, state_vector: Sequence[float]) -> Callable[[datetime], List[float]]:
    epoch_n = utc_naive(epoch)

    def _prop(t: datetime) -> List[float]:
        return state_at_epoch(epoch_n, state_vector, t)

    return _prop


def make_sgp4_propagator(line1: str, line2: str) -> Callable[[datetime], List[float]]:
    if Satrec is None or jday_datetime is None:  # pragma: no cover
        raise RuntimeError("sgp4 is not available in this environment")
    sat = Satrec.twoline2rv(line1, line2)

    def _prop(t: datetime) -> List[float]:
        jd, fr = jday_datetime(utc_naive(t))
        error, position, velocity = sat.sgp4(jd, fr)
        if error != 0:
            raise RuntimeError(f"SGP4 propagation failed (code {error})")
        return [
            float(position[0]),
            float(position[1]),
            float(position[2]),
            float(velocity[0]),
            float(velocity[1]),
            float(velocity[2]),
        ]

    return _prop


def extract_sigma(covariance: Optional[List[List[float]]]) -> float:
    if not covariance:
        return 1.0
    return max(0.1, sum(covariance[i][i] for i in range(min(3, len(covariance)))) / 3) ** 0.5


def altitude_km(state_vector: List[float]) -> float:
    earth_radius_km = 6371.0
    position = position_from_state(state_vector)
    return max(0.0, norm(position) - earth_radius_km)


def safe_unit(vec: Sequence[float], fallback: Sequence[float] = (1.0, 0.0, 0.0)) -> List[float]:
    mag = norm(vec)
    if mag <= 0.0:
        return [float(fallback[0]), float(fallback[1]), float(fallback[2])]
    return [float(vec[0] / mag), float(vec[1] / mag), float(vec[2] / mag)]


def orthonormal_basis_from_u(u: Sequence[float]) -> Tuple[List[float], List[float], List[float]]:
    """Return (u_hat, e1, e2) where {e1,e2} span the plane orthogonal to u_hat."""
    u_hat = safe_unit(u)
    # Pick a reference vector that isn't too parallel to u.
    ref = (0.0, 0.0, 1.0) if abs(u_hat[2]) < 0.9 else (1.0, 0.0, 0.0)
    e1 = cross(u_hat, ref)
    e1 = safe_unit(e1, fallback=(1.0, 0.0, 0.0))
    e2 = cross(u_hat, e1)
    e2 = safe_unit(e2, fallback=(0.0, 1.0, 0.0))
    return u_hat, e1, e2
