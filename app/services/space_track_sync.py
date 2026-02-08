import random
from datetime import datetime, timedelta
from typing import Optional

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.settings import settings

SPACE_TRACK_SOURCE = "space-track"


def has_credentials() -> bool:
    return bool(settings.space_track_user and settings.space_track_password)


def last_sync_at(db: Session) -> Optional[datetime]:
    return (
        db.query(func.max(models.TleRecord.ingested_at))
        .join(models.Source, models.TleRecord.source_id == models.Source.id)
        .filter(models.Source.name == SPACE_TRACK_SOURCE)
        .scalar()
    )


def is_due(db: Session) -> bool:
    last_sync = last_sync_at(db)
    if last_sync is None:
        return True
    return datetime.utcnow() - last_sync >= timedelta(hours=max(1, settings.space_track_sync_hours))


def _login(client: httpx.Client) -> None:
    base_url = settings.space_track_base_url.rstrip("/")
    url = f"{base_url}/ajaxauth/login"
    payload = {"identity": settings.space_track_user, "password": settings.space_track_password}
    resp = client.post(url, data=payload, timeout=20.0)
    resp.raise_for_status()


def _build_gp_tle_query() -> str:
    base_url = settings.space_track_base_url.rstrip("/")
    # Limit to non-decayed objects, recent epochs, and 5-digit NORAD catalog IDs.
    return (
        f"{base_url}/basicspacedata/query/class/gp/"
        "decay_date/null-val/"
        "epoch/%3Enow-30/"
        "NORAD_CAT_ID/%3C100000/"
        "orderby/NORAD_CAT_ID/"
        "format/tle"
    )


def fetch_tle_text(jitter_seconds: Optional[int] = None) -> str:
    if not has_credentials():
        raise RuntimeError("Space-Track credentials not configured")
    if jitter_seconds:
        delay = random.randint(max(0, jitter_seconds - 120), jitter_seconds)
        if delay > 0:
            import time

            time.sleep(delay)
    with httpx.Client(follow_redirects=True) as client:
        _login(client)
        query_url = _build_gp_tle_query()
        resp = client.get(query_url, timeout=60.0)
        resp.raise_for_status()
        return resp.text
