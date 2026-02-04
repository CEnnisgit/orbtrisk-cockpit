from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db

router = APIRouter()


@router.post("/webhooks", response_model=schemas.WebhookOut)
def create_webhook(payload: schemas.WebhookCreate, db: Session = Depends(get_db)):
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
def list_webhooks(db: Session = Depends(get_db)):
    return db.query(models.WebhookSubscription).order_by(models.WebhookSubscription.id.asc()).all()
