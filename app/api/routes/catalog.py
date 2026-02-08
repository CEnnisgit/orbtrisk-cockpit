from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import catalog_sync

router = APIRouter()


@router.post("/catalog/sync")
def sync_catalog(db: Session = Depends(get_db)):
    return catalog_sync.sync_catalog(db, manual=True)


@router.get("/catalog/status")
def catalog_status(db: Session = Depends(get_db)):
    return catalog_sync.catalog_status(db)


@router.get("/catalog/objects")
def catalog_objects(db: Session = Depends(get_db)):
    return catalog_sync.catalog_objects(db)


@router.get("/catalog/objects/{object_id}")
def catalog_object_detail(object_id: int, db: Session = Depends(get_db)):
    detail = catalog_sync.catalog_object_detail(db, object_id)
    if not detail:
        raise HTTPException(status_code=404, detail="SpaceObject not found")
    return detail
