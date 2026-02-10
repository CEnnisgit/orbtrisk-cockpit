from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app import auth
from app import models, schemas
from app.database import get_db

router = APIRouter()


@router.post("/webhooks", response_model=schemas.WebhookOut)
def create_webhook(request: Request, payload: schemas.WebhookCreate, db: Session = Depends(get_db)):
    auth.require_business(request)
    webhook = models.WebhookSubscription(
        url=payload.url,
        event_type=payload.event_type,
        secret=payload.secret,
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)
    return webhook


@router.get("/webhooks", response_model=list[schemas.WebhookOut])
def list_webhooks(request: Request, db: Session = Depends(get_db)):
    auth.require_business(request)
    return db.query(models.WebhookSubscription).order_by(models.WebhookSubscription.id.asc()).all()
