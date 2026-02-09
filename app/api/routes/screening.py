from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import auth, models
from app.database import get_db
from app.services import screening

router = APIRouter()


@router.post("/satellites/{satellite_id}/screen")
def screen_satellite(request: Request, satellite_id: int, db: Session = Depends(get_db)):
    auth.require_business(request)
    sat = db.get(models.Satellite, satellite_id)
    if not sat:
        raise HTTPException(status_code=404, detail="Satellite not found")
    result = screening.screen_satellite(db, satellite_id)
    return {
        "satellite_id": satellite_id,
        "screened_at": result.screened_at.isoformat(),
        "events_updated": result.events_updated,
        "events_created": result.events_created,
        "updates_created": result.updates_created,
    }
