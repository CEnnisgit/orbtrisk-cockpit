import logging
import os
from datetime import datetime
from typing import Dict, List

import httpx
import spiceypy as spice

from app.settings import settings

logger = logging.getLogger(__name__)

KERNEL_URLS = {
    "naif0012.tls": "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/lsk/naif0012.tls",
    "pck00010.tpc": "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/pck00010.tpc",
    "de440s.bsp": "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440s.bsp",
    "jup365.bsp": "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/satellites/jup365.bsp",
    "sat441.bsp": "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/satellites/sat441.bsp",
    "plu058.bsp": "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/satellites/plu058.bsp",
    "ura111.bsp": "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/satellites/ura111.bsp",
    "nep097.bsp": "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/satellites/nep097.bsp",
}

BODY_DEFS = [
    {"name": "Sun", "target": "SUN", "radius_km": 696340, "category": "star"},
    {"name": "Mercury", "target": "MERCURY", "radius_km": 2439.7, "category": "planet"},
    {"name": "Venus", "target": "VENUS", "radius_km": 6051.8, "category": "planet"},
    {"name": "Earth", "target": "EARTH", "radius_km": 6371.0, "category": "planet"},
    {"name": "Moon", "target": "MOON", "radius_km": 1737.4, "category": "moon", "parent": "Earth"},
    {"name": "Mars", "target": "MARS", "radius_km": 3389.5, "category": "planet"},
    {"name": "Jupiter", "target": "JUPITER BARYCENTER", "radius_km": 69911, "category": "planet"},
    {"name": "Io", "target": "IO", "radius_km": 1821.6, "category": "moon", "parent": "Jupiter"},
    {"name": "Europa", "target": "EUROPA", "radius_km": 1560.8, "category": "moon", "parent": "Jupiter"},
    {"name": "Ganymede", "target": "GANYMEDE", "radius_km": 2634.1, "category": "moon", "parent": "Jupiter"},
    {"name": "Callisto", "target": "CALLISTO", "radius_km": 2410.3, "category": "moon", "parent": "Jupiter"},
    {"name": "Saturn", "target": "SATURN BARYCENTER", "radius_km": 58232, "category": "planet"},
    {"name": "Titan", "target": "TITAN", "radius_km": 2574.7, "category": "moon", "parent": "Saturn"},
    {"name": "Enceladus", "target": "ENCELADUS", "radius_km": 252.1, "category": "moon", "parent": "Saturn"},
    {"name": "Uranus", "target": "URANUS BARYCENTER", "radius_km": 25362, "category": "planet"},
    {"name": "Neptune", "target": "NEPTUNE BARYCENTER", "radius_km": 24622, "category": "planet"},
    {"name": "Triton", "target": "TRITON", "radius_km": 1353.4, "category": "moon", "parent": "Neptune"},
    {"name": "Pluto", "target": "PLUTO BARYCENTER", "radius_km": 1188.3, "category": "dwarf_planet"},
]

_kernels_loaded = False


def _download(url: str, dest_path: str) -> None:
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    if os.path.exists(dest_path):
        return
    with httpx.stream("GET", url, timeout=120.0) as response:
        response.raise_for_status()
        with open(dest_path, "wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)


def ensure_kernels() -> List[str]:
    kernel_dir = settings.spice_kernel_dir
    os.makedirs(kernel_dir, exist_ok=True)
    paths = []
    for filename, url in KERNEL_URLS.items():
        path = os.path.join(kernel_dir, filename)
        try:
            _download(url, path)
            paths.append(path)
        except Exception:
            logger.warning("Failed to download SPICE kernel %s — skipping", filename)
    return paths


def load_kernels() -> None:
    global _kernels_loaded
    if _kernels_loaded:
        return
    paths = ensure_kernels()
    for path in paths:
        spice.furnsh(path)
    _kernels_loaded = True


def get_body_positions(epoch_iso: str) -> Dict[str, object]:
    load_kernels()
    et = spice.str2et(epoch_iso)
    bodies = []
    for body in BODY_DEFS:
        if body["name"].lower() == "sun":
            pos = [0.0, 0.0, 0.0]
        else:
            try:
                pos, _ = spice.spkpos(body["target"], et, "J2000", "NONE", "SUN")
            except Exception:
                logger.debug("spkpos failed for %s — skipping", body["name"])
                continue
        entry = {
            "name": body["name"],
            "target": body["target"],
            "radius_km": body["radius_km"],
            "position_km": [float(pos[0]), float(pos[1]), float(pos[2])],
            "category": body.get("category", "planet"),
        }
        if "parent" in body:
            entry["parent"] = body["parent"]
        bodies.append(entry)
    return {"epoch": epoch_iso, "bodies": bodies}
