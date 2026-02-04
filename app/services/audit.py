import hashlib
from typing import Optional

from sqlalchemy.orm import Session

from app import models


def _hash_payload(entity_type: str, entity_id: int, prev_hash: Optional[str]) -> str:
    payload = f"{entity_type}:{entity_id}:{prev_hash or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def append_audit_log(db: Session, entity_type: str, entity_id: int) -> models.AuditLog:
    last = db.query(models.AuditLog).order_by(models.AuditLog.id.desc()).first()
    prev_hash = last.hash if last else None
    new_hash = _hash_payload(entity_type, entity_id, prev_hash)
    entry = models.AuditLog(
        entity_type=entity_type,
        entity_id=entity_id,
        hash=new_hash,
        prev_hash=prev_hash,
    )
    db.add(entry)
    return entry
