from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app import auth, models
from app.database import get_db
from app.services import catalog_sync

router = APIRouter()


@router.post("/catalog/sync")
def sync_catalog(request: Request, db: Session = Depends(get_db)):
    auth.require_business(request)
    return catalog_sync.sync_catalog(db, manual=True)

@router.post("/catalog/sync-if-due")
def sync_catalog_if_due(request: Request, db: Session = Depends(get_db)):
    auth.require_business(request)
    result = catalog_sync.sync_if_due(db)
    if result is None:
        return {"synced": False}
    return {"synced": True, **result}


@router.get("/catalog/status")
def catalog_status(request: Request, db: Session = Depends(get_db)):
    return catalog_sync.catalog_status(db)


@router.get("/catalog/objects")
def catalog_objects(
    request: Request,
    db: Session = Depends(get_db),
    q: Optional[str] = Query(default=None),
    limit: Optional[int] = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    show: str = Query(default="catalog"),
):
    is_business = auth.is_business(request)

    if limit is None:
        data = catalog_sync.catalog_objects(db)
        if is_business:
            return data

        items = [item for item in data.get("items", []) if not bool(item.get("is_operator_asset"))]
        total = (
            db.query(models.SpaceObject)
            .filter(models.SpaceObject.is_operator_asset.is_(False))
            .count()
        )
        return {
            "items": items,
            "total": total,
            "missing_tle": max(0, int(total) - len(items)),
        }

    is_operator_asset = None
    if show == "catalog":
        is_operator_asset = False
    elif show == "operator":
        if not is_business:
            raise HTTPException(status_code=403, detail="Business access required")
        is_operator_asset = True
    elif show == "all":
        if not is_business:
            raise HTTPException(status_code=403, detail="Business access required")
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
def catalog_object_detail(request: Request, object_id: int, db: Session = Depends(get_db)):
    auth.require_business(request)
    detail = catalog_sync.catalog_object_detail(db, object_id)
    if not detail:
        raise HTTPException(status_code=404, detail="SpaceObject not found")
    return detail
