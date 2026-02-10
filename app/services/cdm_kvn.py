from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple


class CdmKvnError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("Invalid CCSDS CDM KVN")
        self.errors = errors


_KVN_RE = re.compile(r"^\s*(?P<key>[A-Za-z0-9_]+)\s*=\s*(?P<value>.*?)\s*$")
_UNIT_RE = re.compile(r"^(?P<value>.*?)(?:\s*\[(?P<unit>[^\]]+)\])?$")

_FORCE_GLOBAL_KEYS = {
    # Header-ish / relative metadata keys that should not be captured inside OBJECT blocks.
    "CCSDS_CDM_VERS",
    "CREATION_DATE",
    "ORIGINATOR",
    "TCA",
    "REF_FRAME",
    "MISS_DISTANCE",
    "RELATIVE_SPEED",
    # Minimal RTN position covariance (symmetric 3x3) for combined/relative position.
    "CR_R",
    "CT_R",
    "CT_T",
    "CN_R",
    "CN_T",
    "CN_N",
}


@dataclass(frozen=True)
class ParsedObject:
    norad_cat_id: Optional[int]
    name: Optional[str]
    state_km: list[float]


@dataclass(frozen=True)
class ParsedCdm:
    version: str
    creation_date: datetime
    originator: str
    tca: datetime
    miss_distance_km: float
    relative_speed_km_s: Optional[float]
    ref_frame: str
    object1: ParsedObject
    object2: ParsedObject
    covariance_rtn_km2: Optional[list[list[float]]]
    kvn: Dict[str, Any]


def _parse_datetime(value: str, *, field: str, errors: list[str]) -> Optional[datetime]:
    raw = (value or "").strip()
    if not raw:
        errors.append(f"Missing {field}")
        return None
    raw = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        errors.append(f"Invalid {field}: {value!r}")
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _split_value_and_unit(raw: str) -> Tuple[str, Optional[str]]:
    match = _UNIT_RE.match(raw.strip())
    if not match:
        return raw.strip(), None
    val = (match.group("value") or "").strip()
    unit = (match.group("unit") or "").strip() or None
    return val, unit


def _parse_float(raw: str, *, field: str, errors: list[str]) -> Optional[float]:
    text = (raw or "").strip()
    if not text:
        errors.append(f"Missing {field}")
        return None
    # Some CDMs use Fortran-style exponents.
    text = text.replace("D", "E").replace("d", "e")
    try:
        return float(text)
    except ValueError:
        errors.append(f"Invalid {field}: {raw!r}")
        return None


def _require_unit(unit: Optional[str], *, field: str, errors: list[str]) -> Optional[str]:
    if not unit:
        errors.append(f"Missing units for {field} (expected bracket units like [km] or [m])")
        return None
    return unit


def _to_km(value: float, unit: str, *, field: str, errors: list[str]) -> Optional[float]:
    u = unit.strip().lower().replace(" ", "")
    if "km" in u:
        return float(value)
    if u in {"m"} or u.endswith("m"):
        return float(value) / 1000.0
    errors.append(f"Unsupported units for {field}: {unit!r}")
    return None


def _to_km_s(value: float, unit: str, *, field: str, errors: list[str]) -> Optional[float]:
    u = unit.strip().lower().replace(" ", "")
    if "km/s" in u or ("km" in u and "/s" in u):
        return float(value)
    if "m/s" in u or (u.endswith("m/s")):
        return float(value) / 1000.0
    errors.append(f"Unsupported units for {field}: {unit!r}")
    return None


def _to_km2(value: float, unit: str, *, field: str, errors: list[str]) -> Optional[float]:
    u = unit.strip().lower().replace(" ", "").replace("**", "^")
    if "km" in u and ("^2" in u or "2" in u):
        return float(value)
    if "m" in u and ("^2" in u or "2" in u):
        return float(value) / 1_000_000.0
    errors.append(f"Unsupported units for {field}: {unit!r}")
    return None


def _parse_object_state(obj: Dict[str, str], *, name: str, errors: list[str]) -> Optional[list[float]]:
    keys = ("X", "Y", "Z", "X_DOT", "Y_DOT", "Z_DOT")
    missing = [k for k in keys if k not in obj]
    if missing:
        errors.append(f"{name} missing state fields: {', '.join(missing)}")
        return None

    out: list[float] = []
    for k in ("X", "Y", "Z"):
        val_raw, unit = _split_value_and_unit(obj[k])
        unit = _require_unit(unit, field=f"{name}.{k}", errors=errors)
        val = _parse_float(val_raw, field=f"{name}.{k}", errors=errors)
        if unit is None or val is None:
            continue
        km = _to_km(val, unit, field=f"{name}.{k}", errors=errors)
        if km is not None:
            out.append(float(km))

    for k in ("X_DOT", "Y_DOT", "Z_DOT"):
        val_raw, unit = _split_value_and_unit(obj[k])
        unit = _require_unit(unit, field=f"{name}.{k}", errors=errors)
        val = _parse_float(val_raw, field=f"{name}.{k}", errors=errors)
        if unit is None or val is None:
            continue
        km_s = _to_km_s(val, unit, field=f"{name}.{k}", errors=errors)
        if km_s is not None:
            out.append(float(km_s))

    return out if len(out) == 6 else None


def parse_cdm_kvn(raw_text: str) -> ParsedCdm:
    if not raw_text or not raw_text.strip():
        raise CdmKvnError(["Empty CDM text"])

    global_kv: Dict[str, str] = {}
    objects: Dict[str, Dict[str, str]] = {"OBJECT1": {}, "OBJECT2": {}}
    current_object: Optional[str] = None

    for line_no, line in enumerate(raw_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("//"):
            continue

        match = _KVN_RE.match(stripped)
        if not match:
            continue

        key = (match.group("key") or "").strip().upper()
        value = (match.group("value") or "").strip()

        if key == "COMMENT":
            continue

        if key == "OBJECT":
            obj = value.strip().upper()
            current_object = obj if obj in objects else None
            continue

        if key in _FORCE_GLOBAL_KEYS:
            global_kv[key] = value
            continue

        target = objects[current_object] if current_object in objects else global_kv
        target[key] = value

    errors: list[str] = []

    version = (global_kv.get("CCSDS_CDM_VERS") or "").strip()
    if not version:
        errors.append("Missing CCSDS_CDM_VERS")

    creation_date = _parse_datetime(global_kv.get("CREATION_DATE", ""), field="CREATION_DATE", errors=errors)
    originator = (global_kv.get("ORIGINATOR") or "").strip()
    if not originator:
        errors.append("Missing ORIGINATOR")

    tca = _parse_datetime(global_kv.get("TCA", ""), field="TCA", errors=errors)

    miss_raw, miss_unit = _split_value_and_unit(global_kv.get("MISS_DISTANCE", ""))
    miss_unit = _require_unit(miss_unit, field="MISS_DISTANCE", errors=errors)
    miss_val = _parse_float(miss_raw, field="MISS_DISTANCE", errors=errors)
    miss_distance_km: Optional[float] = None
    if miss_unit and miss_val is not None:
        miss_distance_km = _to_km(miss_val, miss_unit, field="MISS_DISTANCE", errors=errors)

    rel_speed_km_s: Optional[float] = None
    if "RELATIVE_SPEED" in global_kv:
        sp_raw, sp_unit = _split_value_and_unit(global_kv.get("RELATIVE_SPEED", ""))
        sp_unit = _require_unit(sp_unit, field="RELATIVE_SPEED", errors=errors)
        sp_val = _parse_float(sp_raw, field="RELATIVE_SPEED", errors=errors)
        if sp_unit and sp_val is not None:
            rel_speed_km_s = _to_km_s(sp_val, sp_unit, field="RELATIVE_SPEED", errors=errors)

    ref_frame_raw = global_kv.get("REF_FRAME") or objects["OBJECT1"].get("REF_FRAME") or objects["OBJECT2"].get("REF_FRAME")
    ref_frame = (ref_frame_raw or "").strip().upper()
    if not ref_frame:
        errors.append("Missing REF_FRAME")

    obj1_state = _parse_object_state(objects["OBJECT1"], name="OBJECT1", errors=errors)
    obj2_state = _parse_object_state(objects["OBJECT2"], name="OBJECT2", errors=errors)

    def _parse_int(value: Optional[str], field: str) -> Optional[int]:
        raw = (value or "").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            errors.append(f"Invalid {field}: {value!r}")
            return None

    obj1_norad = _parse_int(objects["OBJECT1"].get("NORAD_CAT_ID"), "OBJECT1.NORAD_CAT_ID")
    obj2_norad = _parse_int(objects["OBJECT2"].get("NORAD_CAT_ID"), "OBJECT2.NORAD_CAT_ID")
    obj1_name = (objects["OBJECT1"].get("OBJECT_NAME") or objects["OBJECT1"].get("OBJECT") or "").strip() or None
    obj2_name = (objects["OBJECT2"].get("OBJECT_NAME") or objects["OBJECT2"].get("OBJECT") or "").strip() or None

    cov_keys = ("CR_R", "CT_R", "CT_T", "CN_R", "CN_T", "CN_N")
    cov_present = any(k in global_kv for k in cov_keys)
    covariance_rtn_km2: Optional[list[list[float]]] = None
    if cov_present:
        missing = [k for k in cov_keys if k not in global_kv]
        if missing:
            errors.append(f"Covariance missing fields: {', '.join(missing)}")
        else:
            cov_vals: dict[str, float] = {}
            cov_unit: Optional[str] = None
            for k in cov_keys:
                raw, unit = _split_value_and_unit(global_kv.get(k, ""))
                unit = _require_unit(unit, field=k, errors=errors)
                val = _parse_float(raw, field=k, errors=errors)
                if unit and val is not None:
                    cov_unit = cov_unit or unit
                    km2 = _to_km2(val, unit, field=k, errors=errors)
                    if km2 is not None:
                        cov_vals[k] = float(km2)
            if len(cov_vals) == 6:
                covariance_rtn_km2 = [
                    [cov_vals["CR_R"], cov_vals["CT_R"], cov_vals["CN_R"]],
                    [cov_vals["CT_R"], cov_vals["CT_T"], cov_vals["CN_T"]],
                    [cov_vals["CN_R"], cov_vals["CN_T"], cov_vals["CN_N"]],
                ]

    if errors:
        raise CdmKvnError(errors)

    assert creation_date is not None
    assert tca is not None
    assert miss_distance_km is not None
    assert obj1_state is not None
    assert obj2_state is not None

    return ParsedCdm(
        version=version,
        creation_date=creation_date,
        originator=originator,
        tca=tca,
        miss_distance_km=float(miss_distance_km),
        relative_speed_km_s=float(rel_speed_km_s) if rel_speed_km_s is not None else None,
        ref_frame=ref_frame,
        object1=ParsedObject(norad_cat_id=obj1_norad, name=obj1_name, state_km=obj1_state),
        object2=ParsedObject(norad_cat_id=obj2_norad, name=obj2_name, state_km=obj2_state),
        covariance_rtn_km2=covariance_rtn_km2,
        kvn={"global": global_kv, "object1": objects["OBJECT1"], "object2": objects["OBJECT2"]},
    )
