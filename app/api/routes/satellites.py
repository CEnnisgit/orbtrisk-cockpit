from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import auth
from app import models, schemas
from app.database import get_db

router = APIRouter()



@router.post("/satellites", response_model=schemas.SatelliteOut)
def create_satellite(request: Request, payload: schemas.SatelliteCreate, db: Session = Depends(get_db)):
    auth.require_business(request)
    data = payload.model_dump()

    space_object = None
    norad_id = None
    if data.get("catalog_id") and str(data["catalog_id"]).isdigit():
        norad_id = int(str(data["catalog_id"]))
        space_object = db.query(models.SpaceObject).filter(models.SpaceObject.norad_cat_id == norad_id).first()
    if space_object is None:
        space_object = db.query(models.SpaceObject).filter(models.SpaceObject.name == data["name"]).first()
    if space_object is None:
        space_object = models.SpaceObject(
            norad_cat_id=norad_id,
            name=data["name"],
            object_type="PAYLOAD",
            international_designator=None,
            source_id=None,
            is_operator_asset=True,
        )
        db.add(space_object)
        db.flush()
    else:
        if not space_object.is_operator_asset:
            space_object.is_operator_asset = True
        if norad_id is not None and space_object.norad_cat_id is None:
            space_object.norad_cat_id = norad_id

    satellite = models.Satellite(**data, space_object_id=space_object.id)
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
