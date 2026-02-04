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
