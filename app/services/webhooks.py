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


async def post_webhook(
    *,
    url: str,
    event_type: str,
    subscription_id: int,
    secret: Optional[str],
    payload: Dict,
    timeout_seconds: float,
) -> None:
    headers = {
        "Content-Type": "application/json",
        "X-Event-Type": str(event_type),
        "X-Webhook-Id": str(subscription_id),
    }
    signature = sign_payload(secret, payload)
    if signature:
        headers["X-Signature"] = signature

    body = _json_body(payload)
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        await client.post(url, content=body, headers=headers)


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
        for sub in subscriptions:
            try:
                await post_webhook(
                    url=str(sub.url),
                    event_type=str(event_type),
                    subscription_id=int(sub.id),
                    secret=str(sub.secret) if sub.secret else None,
                    payload=payload,
                    timeout_seconds=float(timeout),
                )
            except httpx.HTTPError:
                continue
    finally:
        db.close()
