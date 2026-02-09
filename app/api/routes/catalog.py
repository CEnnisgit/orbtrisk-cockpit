from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import catalog_sync

router = APIRouter()


@router.post("/catalog/sync")
def sync_catalog(db: Session = Depends(get_db)):
    return catalog_sync.sync_catalog(db, manual=True)

@router.post("/catalog/sync-if-due")
def sync_catalog_if_due(db: Session = Depends(get_db)):
    result = catalog_sync.sync_if_due(db)
    if result is None:
        return {"synced": False}
    return {"synced": True, **result}


@router.get("/catalog/status")
def catalog_status(db: Session = Depends(get_db)):
    return catalog_sync.catalog_status(db)


@router.get("/catalog/objects")
def catalog_objects(
    db: Session = Depends(get_db),
    q: Optional[str] = Query(default=None),
    limit: Optional[int] = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    show: str = Query(default="catalog"),
):
    if limit is None:
        return catalog_sync.catalog_objects(db)

    is_operator_asset = None
    if show == "catalog":
        is_operator_asset = False
    elif show == "operator":
        is_operator_asset = True
    elif show == "all":
        is_operator_asset = None
    else:
        raise HTTPException(status_code=400, detail="show must be catalog, operator, or all")

    return catalog_sync.catalog_objects_page(
        db,
        q=q,
        is_operator_asset=is_operator_asset,
        limit=limit,
        offset=offset,
    )


@router.get("/catalog/objects/{object_id}")
def catalog_object_detail(object_id: int, db: Session = Depends(get_db)):
    detail = catalog_sync.catalog_object_detail(db, object_id)
    if not detail:
        raise HTTPException(status_code=404, detail="SpaceObject not found")
    return detail
