import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import httpx

from app.settings import settings

SMALL_BODIES = [
    {"name": "1 Ceres", "command": "DES=1", "radius_km": 473},
    {"name": "2 Pallas", "command": "DES=2", "radius_km": 256},
    {"name": "4 Vesta", "command": "DES=4", "radius_km": 263},
    {"name": "10 Hygiea", "command": "DES=10", "radius_km": 215},
    {"name": "16 Psyche", "command": "DES=16", "radius_km": 113},
    {"name": "433 Eros", "command": "DES=433", "radius_km": 8.4},
    {"name": "243 Ida", "command": "DES=243", "radius_km": 15.7},
    {"name": "253 Mathilde", "command": "DES=253", "radius_km": 26.5},
    {"name": "25143 Itokawa", "command": "DES=25143", "radius_km": 0.17},
    {"name": "101955 Bennu", "command": "DES=101955", "radius_km": 0.246},
    {"name": "162173 Ryugu", "command": "DES=162173", "radius_km": 0.435},
    {"name": "67P/Churyumov-Gerasimenko", "command": "DES=67P", "radius_km": 2.0},
    {"name": "1P/Halley", "command": "DES=1P", "radius_km": 5.5},
    # Dwarf planets
    {"name": "Eris", "command": "DES=136199", "radius_km": 1163, "category": "dwarf_planet"},
    {"name": "Makemake", "command": "DES=136472", "radius_km": 715, "category": "dwarf_planet"},
    {"name": "Haumea", "command": "DES=136108", "radius_km": 816, "category": "dwarf_planet"},
    {"name": "Sedna", "command": "DES=90377", "radius_km": 498, "category": "dwarf_planet"},
    # Notable asteroids
    {"name": "99942 Apophis", "command": "DES=99942", "radius_km": 0.185},
    {"name": "3200 Phaethon", "command": "DES=3200", "radius_km": 2.9},
    {"name": "52768 OR2", "command": "DES=52768", "radius_km": 2.0},
]

_cache: Dict[str, object] = {"epoch_key": None, "timestamp": None, "data": None}


def _parse_epoch(epoch_iso: str) -> datetime:
    if epoch_iso.endswith("Z"):
        epoch_iso = epoch_iso.replace("Z", "+00:00")
    return datetime.fromisoformat(epoch_iso).astimezone(timezone.utc)


def _epoch_key(epoch_iso: str) -> str:
    epoch = _parse_epoch(epoch_iso)
    rounded = epoch.replace(minute=0, second=0, microsecond=0)
    return rounded.isoformat()


def _parse_vectors(result_text: str) -> Optional[List[float]]:
    if "$$SOE" not in result_text:
        return None
    chunk = result_text.split("$$SOE")[1].split("$$EOE")[0]
    lines = [line.strip() for line in chunk.splitlines() if line.strip()]
    if not lines:
        return None
    line = lines[0]
    floats = re.findall(r"[-+]?\\d*\\.?\\d+(?:[EeDd][-+]?\\d+)?", line)
    if len(floats) < 6:
        return None
    floats = [float(val.replace("D", "E")) for val in floats]
    return floats[-6:-3]


def _fetch_body_position(command: str, epoch_iso: str) -> Optional[List[float]]:
    start = _parse_epoch(epoch_iso)
    stop = start + timedelta(minutes=1)
    params = {
        "format": "json",
        "COMMAND": f"'{command}'",
        "CENTER": "'@sun'",
        "EPHEM_TYPE": "VECTORS",
        "MAKE_EPHEM": "YES",
        "OBJ_DATA": "NO",
        "START_TIME": f"'{start.strftime('%Y-%m-%d %H:%M')}'",
        "STOP_TIME": f"'{stop.strftime('%Y-%m-%d %H:%M')}'",
        "STEP_SIZE": "'1 m'",
        "CSV_FORMAT": "YES",
        "VEC_TABLE": "1",
        "OUT_UNITS": "KM-S",
    }
    resp = httpx.get(settings.horizons_base_url, params=params, timeout=30.0)
    resp.raise_for_status()
    data = resp.json()
    result_text = data.get("result", "")
    return _parse_vectors(result_text)


def _fetch_one(body: dict, epoch_iso: str) -> Optional[dict]:
    try:
        position = _fetch_body_position(body["command"], epoch_iso)
    except Exception:
        position = None
    if not position:
        return None
    return {
        "name": body["name"],
        "category": body.get("category", "small_body"),
        "radius_km": body["radius_km"],
        "position_km": position,
    }


def get_small_body_positions(epoch_iso: str) -> List[dict]:
    key = _epoch_key(epoch_iso)
    now = datetime.now(timezone.utc)
    if _cache["epoch_key"] == key and _cache["timestamp"]:
        age = now - _cache["timestamp"]
        if age <= timedelta(hours=settings.solar_small_body_cache_hours):
            return _cache["data"] or []

    bodies = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_fetch_one, body, epoch_iso): body for body in SMALL_BODIES}
        for future in as_completed(futures):
            result = future.result()
            if result:
                bodies.append(result)

    _cache["epoch_key"] = key
    _cache["timestamp"] = now
    _cache["data"] = bodies
    return bodies
