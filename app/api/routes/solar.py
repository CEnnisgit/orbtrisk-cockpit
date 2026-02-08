import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.services import spice_service, solar_small_bodies

router = APIRouter()

# In-memory cache for solar positions (60-second TTL)
_solar_cache: dict = {}
_SOLAR_CACHE_TTL = 60


def _cache_key(epoch: str, include_small_bodies: bool, category: Optional[str]) -> str:
    # Round epoch to nearest 10 seconds for cache hit rate
    try:
        dt = datetime.fromisoformat(epoch.replace("Z", "+00:00"))
        rounded_ts = int(dt.timestamp() / 10) * 10
    except (ValueError, AttributeError):
        rounded_ts = epoch
    return f"{rounded_ts}:{include_small_bodies}:{category or ''}"


@router.get("/solar/positions")
def solar_positions(
    epoch: Optional[str] = None,
    include_small_bodies: bool = True,
    category: Optional[str] = None,
):
    if not epoch:
        epoch = datetime.now(timezone.utc).isoformat()

    key = _cache_key(epoch, include_small_bodies, category)
    now = time.monotonic()
    cached = _solar_cache.get(key)
    if cached and (now - cached[0]) < _SOLAR_CACHE_TTL:
        return cached[1]

    try:
        data = spice_service.get_body_positions(epoch)
        if include_small_bodies:
            small_bodies = solar_small_bodies.get_small_body_positions(epoch)
            data["bodies"] = data.get("bodies", []) + small_bodies
        if category:
            allowed = {c.strip() for c in category.split(",")}
            data["bodies"] = [b for b in data["bodies"] if b.get("category") in allowed]

        # Evict stale entries periodically (every 10 inserts worth)
        if len(_solar_cache) > 100:
            stale_keys = [k for k, v in _solar_cache.items() if (now - v[0]) >= _SOLAR_CACHE_TTL]
            for k in stale_keys:
                del _solar_cache[k]

        _solar_cache[key] = (now, data)
        return data
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to compute solar positions") from exc
