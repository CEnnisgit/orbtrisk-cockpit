import json
import os
import uuid
from datetime import datetime
from typing import Dict

from app.settings import settings


def write_raw_snapshot(payload: Dict) -> str:
    os.makedirs(settings.raw_data_dir, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    filename = f"orbit_state_{timestamp}_{uuid.uuid4().hex}.json"
    path = os.path.join(settings.raw_data_dir, filename)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)
    return path


def write_raw_text_snapshot(prefix: str, raw_text: str) -> str:
    os.makedirs(settings.raw_data_dir, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    safe_prefix = "".join(ch for ch in (prefix or "raw") if ch.isalnum() or ch in {"_", "-"}).strip("_-") or "raw"
    filename = f"{safe_prefix}_{timestamp}_{uuid.uuid4().hex}.txt"
    path = os.path.join(settings.raw_data_dir, filename)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(raw_text)
    return path
