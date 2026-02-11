from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app import auth
from app import security
from app import models, schemas
from app.database import get_db

router = APIRouter()


def _to_webhook_out(webhook: models.WebhookSubscription) -> schemas.WebhookOut:
    return schemas.WebhookOut(
        id=int(webhook.id),
        url=str(webhook.url),
        event_type=str(webhook.event_type),
        active=bool(webhook.active),
        has_secret=bool(webhook.secret),
        created_at=webhook.created_at,
    )


@router.post("/webhooks", response_model=schemas.WebhookOut)
def create_webhook(request: Request, payload: schemas.WebhookCreate, db: Session = Depends(get_db)):
    auth.require_business(request)
    target = security.validate_webhook_target(str(payload.url))
    webhook = models.WebhookSubscription(
        url=target,
        event_type=payload.event_type,
        secret=payload.secret,
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)
    return _to_webhook_out(webhook)


@router.get("/webhooks", response_model=list[schemas.WebhookOut])
def list_webhooks(request: Request, db: Session = Depends(get_db)):
    auth.require_business(request)
    webhooks = db.query(models.WebhookSubscription).order_by(models.WebhookSubscription.id.asc()).all()
    return [_to_webhook_out(hook) for hook in webhooks]
