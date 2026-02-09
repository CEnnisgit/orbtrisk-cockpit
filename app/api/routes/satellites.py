from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import auth
from app import models, schemas
from app.database import get_db

router = APIRouter()



@router.post("/satellites", response_model=schemas.SatelliteOut)
def create_satellite(request: Request, payload: schemas.SatelliteCreate, db: Session = Depends(get_db)):
    auth.require_business(request)
    satellite = models.Satellite(**payload.model_dump())
    db.add(satellite)
    db.commit()
    db.refresh(satellite)
    return satellite


@router.get("/satellites", response_model=list[schemas.SatelliteOut])
def list_satellites(request: Request, db: Session = Depends(get_db)):
    auth.require_business(request)
    return db.query(models.Satellite).order_by(models.Satellite.id.asc()).all()


@router.get("/satellites/{satellite_id}", response_model=schemas.SatelliteOut)
def get_satellite(request: Request, satellite_id: int, db: Session = Depends(get_db)):
    auth.require_business(request)
    satellite = db.get(models.Satellite, satellite_id)
    if not satellite:
        raise HTTPException(status_code=404, detail="Satellite not found")
    return satellite
