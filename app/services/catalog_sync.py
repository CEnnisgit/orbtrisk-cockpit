import csv
import io
import os
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import httpx
from sgp4.api import Satrec
from sgp4.conveniences import sat_epoch_datetime
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.database import SessionLocal
from app.settings import settings
from app.services import propagation, conjunction, risk, maneuver, space_track_sync

_scheduler_started = False


def _write_raw_text_snapshot(prefix: str, raw_text: str) -> str:
    os.makedirs(settings.raw_data_dir, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    filename = f"{prefix}_{timestamp}_{uuid.uuid4().hex}.txt"
    path = os.path.join(settings.raw_data_dir, filename)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(raw_text)
    return path


def _fetch_text(url: str) -> str:
    resp = httpx.get(url, timeout=30.0)
    resp.raise_for_status()
    return resp.text


def _parse_tle_lines(raw_text: str) -> List[dict]:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    parsed: List[dict] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if line.startswith("1 ") and idx + 1 < len(lines) and lines[idx + 1].startswith("2 "):
            name = f"OBJECT-{line[2:7].strip()}"
            line1 = line
            line2 = lines[idx + 1]
            idx += 2
        else:
            if idx + 2 >= len(lines):
                break
            name = line
            line1 = lines[idx + 1]
            line2 = lines[idx + 2]
            idx += 3
        parsed.append({"name": name.strip(), "line1": line1, "line2": line2, "raw": "\n".join([name, line1, line2])})
    return parsed


def _parse_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_satcat_csv(raw_text: str) -> Dict[int, dict]:
    metadata: Dict[int, dict] = {}
    reader = csv.DictReader(io.StringIO(raw_text))
    for row in reader:
        norad_raw = _get_row_value(row, "NORAD_CAT_ID", "NORAD_CAT_ID", "NORAD_CAT_ID")
        if not norad_raw:
            continue
        try:
            norad_id = int(norad_raw)
        except ValueError:
            continue
        apogee = _parse_float(_get_row_value(row, "APOGEE", "APOGEE"))
        perigee = _parse_float(_get_row_value(row, "PERIGEE", "PERIGEE"))
        inclination = _parse_float(_get_row_value(row, "INCLINATION", "INCLINATION"))
        period = _parse_float(_get_row_value(row, "PERIOD", "PERIOD"))
        metadata[norad_id] = {
            "name": _get_row_value(row, "OBJECT_NAME", "SATNAME", "OBJECT_NAME"),
            "object_type": _get_row_value(row, "OBJECT_TYPE", "OBJECT_TYPE", "OBJ_TYPE"),
            "int_des": _get_row_value(row, "INTL_DES", "INTL_DESIGNATOR", "INT_DES"),
            "owner": _get_row_value(row, "OWNER", "OWNER"),
            "ops_status_code": _get_row_value(row, "OPS_STATUS_CODE", "OPS_STATUS_CODE"),
            "launch_date": _get_row_value(row, "LAUNCH_DATE", "LAUNCH_DATE"),
            "decay_date": _get_row_value(row, "DECAY_DATE", "DECAY_DATE"),
            "apogee_km": apogee,
            "perigee_km": perigee,
            "inclination_deg": inclination,
            "period_min": period,
            "rcs_size": _get_row_value(row, "RCS_SIZE", "RCS", "RCS_SIZE"),
            "orbit_center": _get_row_value(row, "ORBIT_CENTER", "ORBIT_CENTER"),
            "orbit_type": _get_row_value(row, "ORBIT_TYPE", "ORBIT_TYPE"),
        }
    return metadata


def _get_row_value(row: dict, *keys: str) -> Optional[str]:
    for key in keys:
        if key in row:
            return row[key]
        if key.lower() in row:
            return row[key.lower()]
        if key.upper() in row:
            return row[key.upper()]
    return None


def _get_or_create_source(db: Session, name: str, source_type: str, provenance_uri: Optional[str]) -> models.Source:
    source = db.query(models.Source).filter(models.Source.name == name).first()
    if source:
        if provenance_uri:
            source.provenance_uri = provenance_uri
        if source_type:
            source.type = source_type
        return source
    source = models.Source(name=name, type=source_type, provenance_uri=provenance_uri)
    db.add(source)
    db.flush()
    return source


def _upsert_metadata(db: Session, space_object_id: int, meta: dict) -> None:
    if not meta:
        return
    record = db.get(models.SpaceObjectMetadata, space_object_id)
    if record:
        record.satcat_json = meta
    else:
        db.add(models.SpaceObjectMetadata(space_object_id=space_object_id, satcat_json=meta))


def _fetch_celestrak_texts() -> Tuple[str, str, str]:
    group = settings.celestrak_group
    gp_url = f"{settings.celestrak_gp_url}?GROUP={group}&FORMAT=tle"
    satcat_group = group.upper()
    satcat_url = f"{settings.celestrak_satcat_url}?GROUP={satcat_group}&FORMAT=CSV"

    tle_text = _fetch_text(gp_url)
    satcat_text = _fetch_text(satcat_url)
    return group, tle_text, satcat_text


def _fetch_best_tle_text(db: Session, manual: bool) -> Tuple[str, str, str, str]:
    group, celestrak_tle, satcat_text = _fetch_celestrak_texts()
    if not space_track_sync.has_credentials():
        return group, celestrak_tle, satcat_text, f"celestrak-{group}"
    if not manual and not space_track_sync.is_due(db):
        return group, celestrak_tle, satcat_text, f"celestrak-{group}"
    try:
        jitter = None if manual else 720
        space_tle = space_track_sync.fetch_tle_text(jitter_seconds=jitter)
        return group, space_tle, satcat_text, space_track_sync.SPACE_TRACK_SOURCE
    except Exception:
        return group, celestrak_tle, satcat_text, f"celestrak-{group}"


def sync_catalog(db: Session, manual: bool = False) -> dict:
    group, tle_text, satcat_text, source_name = _fetch_best_tle_text(db, manual=manual)

    raw_prefix = "space_track_tle" if source_name == space_track_sync.SPACE_TRACK_SOURCE else "celestrak_tle"
    _write_raw_text_snapshot(raw_prefix, tle_text)
    _write_raw_text_snapshot("celestrak_satcat", satcat_text)

    satcat_meta = _parse_satcat_csv(satcat_text)
    tles = _parse_tle_lines(tle_text)

    if source_name == space_track_sync.SPACE_TRACK_SOURCE:
        source = _get_or_create_source(db, source_name, "restricted", "space-track.org")
    else:
        source = _get_or_create_source(db, f"celestrak-{group}", "public", "celestrak.org")

    ingested = 0
    skipped = 0
    errors = 0

    max_objects = settings.catalog_max_objects

    for entry in tles:
        if max_objects and ingested >= max_objects:
            break
        line1 = entry["line1"]
        line2 = entry["line2"]
        norad_raw = line1[2:7].strip()
        if not norad_raw.isdigit():
            skipped += 1
            continue
        norad_id = int(norad_raw)
        if norad_id > 99999:
            skipped += 1
            continue

        try:
            satrec = Satrec.twoline2rv(line1, line2)
            epoch = sat_epoch_datetime(satrec)
            error_code, position, velocity = satrec.sgp4(satrec.jdsatepoch, satrec.jdsatepochF)
            if error_code != 0:
                errors += 1
                continue
        except Exception:
            errors += 1
            continue

        meta = satcat_meta.get(norad_id, {})
        name = meta.get("name") or entry["name"]
        object_type = meta.get("object_type") or "PAYLOAD"
        int_des = meta.get("int_des")

        space_object = (
            db.query(models.SpaceObject)
            .filter(models.SpaceObject.norad_cat_id == norad_id)
            .filter(models.SpaceObject.is_operator_asset.is_(False))
            .first()
        )
        if not space_object:
            space_object = models.SpaceObject(
                norad_cat_id=norad_id,
                name=name,
                object_type=object_type,
                international_designator=int_des,
                source_id=source.id,
                is_operator_asset=False,
            )
            db.add(space_object)
            db.flush()
        else:
            space_object.name = name or space_object.name
            space_object.object_type = object_type or space_object.object_type
            space_object.international_designator = int_des or space_object.international_designator
            space_object.source_id = source.id

        tle_record = models.TleRecord(
            space_object_id=space_object.id,
            line1=line1,
            line2=line2,
            epoch=epoch,
            source_id=source.id,
            raw_text=entry["raw"],
        )
        db.add(tle_record)
        _upsert_metadata(db, space_object.id, meta)

        db.query(models.OrbitState).filter(models.OrbitState.space_object_id == space_object.id).delete(
            synchronize_session=False
        )
        orbit_state = models.OrbitState(
            satellite_id=None,
            space_object_id=space_object.id,
            epoch=epoch,
            state_vector=[*position, *velocity],
            covariance=propagation.default_covariance("public"),
            source_id=source.id,
            confidence=0.4,
        )
        db.add(orbit_state)
        ingested += 1

    db.commit()

    _generate_operator_events(db)
    return {
        "group": group,
        "source": source.name,
        "ingested": ingested,
        "skipped": skipped,
        "errors": errors,
    }


def _generate_operator_events(db: Session) -> None:
    states = (
        db.query(models.OrbitState)
        .filter(models.OrbitState.satellite_id.isnot(None))
        .order_by(models.OrbitState.epoch.desc())
        .all()
    )
    latest_by_sat = {}
    for state in states:
        if state.satellite_id not in latest_by_sat:
            latest_by_sat[state.satellite_id] = state

    for state in latest_by_sat.values():
        events = conjunction.detect_events_for_state(db, state)
        for event in events:
            sigma = propagation.extract_sigma(state.covariance)
            poc, risk_score, components, sensitivity = risk.assess_event(event, sigma)
            db.add(
                models.RiskAssessment(
                    event_id=event.id,
                    poc=poc,
                    risk_score=risk_score,
                    components_json=components,
                    sensitivity_json=sensitivity,
                )
            )
            options = maneuver.generate_options(event, risk_score)
            for option in options:
                db.add(
                    models.ManeuverOption(
                        event_id=event.id,
                        delta_v=option["delta_v"],
                        time_window_start=option["time_window_start"],
                        time_window_end=option["time_window_end"],
                        risk_after=option["risk_after"],
                        fuel_cost=option["fuel_cost"],
                        is_recommended=option["is_recommended"],
                    )
                )
    db.commit()


def catalog_status(db: Session) -> dict:
    last_record = db.query(models.TleRecord).order_by(models.TleRecord.ingested_at.desc()).first()
    last_sync = last_record.ingested_at if last_record else None
    last_source = last_record.source.name if last_record and last_record.source else None
    object_count = (
        db.query(models.SpaceObject)
        .filter(models.SpaceObject.is_operator_asset.is_(False))
        .count()
    )
    return {
        "last_sync": last_sync.isoformat() if last_sync else None,
        "object_count": object_count,
        "source": last_source,
    }


def _quality_tier(source_name: Optional[str], tle_epoch: Optional[datetime]) -> Tuple[str, Optional[float]]:
    if not tle_epoch:
        return "D", None
    age_hours = (datetime.utcnow() - tle_epoch).total_seconds() / 3600.0
    if source_name == space_track_sync.SPACE_TRACK_SOURCE and age_hours <= 6:
        return "A", age_hours
    if age_hours <= 24:
        return "B", age_hours
    return "C", age_hours


def catalog_objects(db: Session) -> dict:
    latest_tle = (
        db.query(
            models.TleRecord.space_object_id,
            func.max(models.TleRecord.epoch).label("max_epoch"),
        )
        .group_by(models.TleRecord.space_object_id)
        .subquery()
    )

    rows = (
        db.query(models.SpaceObject, models.TleRecord, models.Source, models.SpaceObjectMetadata)
        .join(latest_tle, latest_tle.c.space_object_id == models.SpaceObject.id)
        .join(
            models.TleRecord,
            (models.TleRecord.space_object_id == models.SpaceObject.id)
            & (models.TleRecord.epoch == latest_tle.c.max_epoch),
        )
        .join(models.Source, models.TleRecord.source_id == models.Source.id)
        .outerjoin(models.SpaceObjectMetadata, models.SpaceObjectMetadata.space_object_id == models.SpaceObject.id)
        .all()
    )

    items = []
    for space_object, tle, source, metadata in rows:
        meta = metadata.satcat_json if metadata else {}
        tier, age_hours = _quality_tier(source.name if source else None, tle.epoch if tle else None)
        items.append(
            {
                "id": space_object.id,
                "norad_cat_id": space_object.norad_cat_id,
                "name": space_object.name,
                "object_type": space_object.object_type,
                "international_designator": space_object.international_designator,
                "is_operator_asset": space_object.is_operator_asset,
                "tle_line1": tle.line1,
                "tle_line2": tle.line2,
                "tle_epoch": tle.epoch.isoformat(),
                "tle_source": source.name if source else None,
                "tle_age_hours": age_hours,
                "quality_tier": tier,
                "owner": meta.get("owner"),
                "ops_status_code": meta.get("ops_status_code"),
                "launch_date": meta.get("launch_date"),
                "decay_date": meta.get("decay_date"),
                "apogee_km": meta.get("apogee_km"),
                "perigee_km": meta.get("perigee_km"),
                "inclination_deg": meta.get("inclination_deg"),
                "period_min": meta.get("period_min"),
                "rcs_size": meta.get("rcs_size"),
                "orbit_center": meta.get("orbit_center"),
                "orbit_type": meta.get("orbit_type"),
            }
        )

    total = db.query(models.SpaceObject).count()
    return {
        "items": items,
        "total": total,
        "missing_tle": max(0, total - len(items)),
    }


def catalog_object_detail(db: Session, object_id: int) -> Optional[dict]:
    space_object = db.get(models.SpaceObject, object_id)
    if not space_object:
        return None

    tle = (
        db.query(models.TleRecord)
        .filter(models.TleRecord.space_object_id == space_object.id)
        .order_by(models.TleRecord.epoch.desc())
        .first()
    )
    source = db.get(models.Source, tle.source_id) if tle else None
    metadata = db.get(models.SpaceObjectMetadata, space_object.id)
    meta = metadata.satcat_json if metadata else {}
    tier, age_hours = _quality_tier(source.name if source else None, tle.epoch if tle else None)

    return {
        "id": space_object.id,
        "norad_cat_id": space_object.norad_cat_id,
        "name": space_object.name,
        "object_type": space_object.object_type,
        "international_designator": space_object.international_designator,
        "is_operator_asset": space_object.is_operator_asset,
        "tle_line1": tle.line1 if tle else None,
        "tle_line2": tle.line2 if tle else None,
        "tle_epoch": tle.epoch.isoformat() if tle else None,
        "has_tle": bool(tle),
        "tle_source": source.name if source else None,
        "tle_age_hours": age_hours,
        "quality_tier": tier,
        "owner": meta.get("owner"),
        "ops_status_code": meta.get("ops_status_code"),
        "launch_date": meta.get("launch_date"),
        "decay_date": meta.get("decay_date"),
        "apogee_km": meta.get("apogee_km"),
        "perigee_km": meta.get("perigee_km"),
        "inclination_deg": meta.get("inclination_deg"),
        "period_min": meta.get("period_min"),
        "rcs_size": meta.get("rcs_size"),
        "orbit_center": meta.get("orbit_center"),
        "orbit_type": meta.get("orbit_type"),
    }


def sync_if_due(db: Session) -> Optional[dict]:
    if space_track_sync.has_credentials() and space_track_sync.is_due(db):
        return sync_catalog(db, manual=False)
    last_sync = (
        db.query(func.max(models.TleRecord.ingested_at))
        .join(models.Source, models.TleRecord.source_id == models.Source.id)
        .filter(models.Source.name.like("celestrak-%"))
        .scalar()
    )
    if last_sync is None:
        return sync_catalog(db, manual=False)
    if datetime.utcnow() - last_sync >= timedelta(hours=settings.catalog_sync_hours):
        return sync_catalog(db, manual=False)
    return None


def start_scheduler():
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def loop():
        while True:
            db = SessionLocal()
            try:
                try:
                    sync_if_due(db)
                except Exception:
                    pass
            finally:
                db.close()
            min_hours = min(settings.catalog_sync_hours, settings.space_track_sync_hours)
            time.sleep(max(3600, min_hours * 3600))

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
