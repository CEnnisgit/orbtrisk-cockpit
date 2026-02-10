from __future__ import annotations

from datetime import datetime
from typing import Sequence

import astropy.units as u
from astropy.coordinates import CartesianDifferential, CartesianRepresentation, GCRS, ITRS, TEME
from astropy.time import Time
from astropy.utils import iers


# Never download IERS tables at runtime (self-hosted/offline friendly).
iers.conf.auto_download = False


def _norm_frame(name: str) -> str:
    return (name or "").strip().upper().replace("-", "_")


def convert_state_vector_km(state6: Sequence[float], from_frame: str, to_frame: str, t: datetime) -> list[float]:
    """Convert a 6D state vector between frames at time t.

    Canonical internal frame is GCRS. For MVP, ECI/GCRF/EME2000 are treated as
    GCRS-like inertial frames (documented approximation).
    """

    if state6 is None or len(state6) != 6:
        raise ValueError("state6 must be length 6 [x,y,z,vx,vy,vz]")

    f = _norm_frame(from_frame)
    to = _norm_frame(to_frame)
    if to != "GCRS":
        raise ValueError(f"Unsupported to_frame: {to_frame!r}")

    # Treat common inertial aliases as GCRS for MVP.
    if f in {"GCRS", "ECI", "GCRF", "EME2000", "J2000"}:
        return [float(x) for x in state6]

    obstime = Time(t, scale="utc")
    rep = CartesianRepresentation(
        state6[0] * u.km,
        state6[1] * u.km,
        state6[2] * u.km,
        differentials=CartesianDifferential(state6[3] * u.km / u.s, state6[4] * u.km / u.s, state6[5] * u.km / u.s),
    )

    if f == "TEME":
        coord = TEME(rep, obstime=obstime)
    elif f in {"ITRF", "ITRS"}:
        coord = ITRS(rep, obstime=obstime)
    else:
        raise ValueError(f"Unsupported from_frame: {from_frame!r}")

    out = coord.transform_to(GCRS(obstime=obstime))
    pos = out.cartesian.xyz.to(u.km).value
    # Velocity differentials are stored under the default key "s" (per-second).
    vel = out.cartesian.differentials["s"].d_xyz.to(u.km / u.s).value
    return [float(pos[0]), float(pos[1]), float(pos[2]), float(vel[0]), float(vel[1]), float(vel[2])]

