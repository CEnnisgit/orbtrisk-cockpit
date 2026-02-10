import hmac
import json
from hashlib import sha256
from typing import Dict, Optional

import httpx

from app import models
from app.database import SessionLocal
from app.settings import settings


def _json_body(payload: Dict) -> str:
    # Use a stable JSON encoding for signatures and transport.
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def sign_payload(secret: Optional[str], payload: Dict) -> Optional[str]:
    if not secret:
        return None
    digest = hmac.new(secret.encode("utf-8"), _json_body(payload).encode("utf-8"), sha256).hexdigest()
    return digest


async def dispatch_event(event_type: str, payload: Dict) -> None:
    db = SessionLocal()
    try:
        subscriptions = (
            db.query(models.WebhookSubscription)
            .filter(models.WebhookSubscription.active.is_(True))
            .filter(models.WebhookSubscription.event_type == event_type)
            .all()
        )
        if not subscriptions:
            return

        timeout = settings.webhook_timeout_seconds
        body = _json_body(payload)
        async with httpx.AsyncClient(timeout=timeout) as client:
            for sub in subscriptions:
                headers = {"Content-Type": "application/json"}
                signature = sign_payload(sub.secret, payload)
                if signature:
                    headers["X-Signature"] = signature
                try:
                    await client.post(sub.url, content=body, headers=headers)
                except httpx.HTTPError:
                    continue
    finally:
        db.close()
