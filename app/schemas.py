from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class SourceCreate(BaseModel):
    name: str
    type: str
    provenance_uri: Optional[str] = None
    license_terms: Optional[str] = None


class SourceOut(SourceCreate):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class SpaceObjectOut(BaseModel):
    id: int
    norad_cat_id: Optional[int] = None
    name: str
    object_type: Optional[str] = None
    international_designator: Optional[str] = None
    is_operator_asset: bool
    created_at: datetime

    class Config:
        from_attributes = True


class SatelliteCreate(BaseModel):
    operator_id: Optional[str] = None
    name: str
    catalog_id: Optional[str] = None
    orbit_regime: str = "LEO"
    status: str = "active"


class SatelliteOut(SatelliteCreate):
    id: int
    space_object_id: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class OrbitStateCreate(BaseModel):
    epoch: datetime
    state_vector: List[float] = Field(..., min_length=6, max_length=6)
    covariance: Optional[List[List[float]]] = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    source: SourceCreate
    satellite_id: Optional[int] = None
    satellite: Optional[SatelliteCreate] = None


class OrbitStateOut(BaseModel):
    id: int
    satellite_id: Optional[int] = None
    space_object_id: Optional[int] = None
    epoch: datetime
    frame: str
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    state_vector: List[float]
    covariance: Optional[List[List[float]]]
    provenance_json: Optional[dict] = None
    source_id: int
    confidence: float
    created_at: datetime

    class Config:
        from_attributes = True


class CdmAttachRequest(BaseModel):
    tca: datetime
    relative_position_km: List[float] = Field(..., min_length=3, max_length=3)
    relative_velocity_km_s: List[float] = Field(..., min_length=3, max_length=3)
    combined_pos_covariance_km2: List[List[float]] = Field(..., min_length=3, max_length=3)
    hard_body_radius_m: Optional[float] = None
    source: SourceCreate
    secondary_norad_cat_id: Optional[int] = None
    secondary_name: Optional[str] = None
    override_secondary: bool = False


class CdmAttachOut(BaseModel):
    event_id: int
    update_id: int


class ConjunctionEventOut(BaseModel):
    id: int
    satellite_id: int
    object_id: Optional[int] = None
    space_object_id: Optional[int] = None
    tca: datetime
    miss_distance: float
    relative_velocity: float
    screening_volume: float
    risk_tier: str
    risk_score: float
    confidence_score: float
    confidence_label: str
    current_update_id: Optional[int] = None
    last_seen_at: Optional[datetime] = None
    is_active: bool
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class ConjunctionEventUpdateOut(BaseModel):
    id: int
    event_id: int
    computed_at: datetime

    tca: datetime
    miss_distance_km: float
    relative_velocity_km_s: float
    screening_volume_km: float

    r_rel_eci_km: Optional[list[float]] = None
    v_rel_eci_km_s: Optional[list[float]] = None
    r_rel_rtn_km: Optional[list[float]] = None
    v_rel_rtn_km_s: Optional[list[float]] = None

    risk_tier: str
    risk_score: float
    confidence_score: float
    confidence_label: str

    drivers_json: Optional[list[str]] = None
    details_json: Optional[dict] = None

    class Config:
        from_attributes = True


class CdmRecordOut(BaseModel):
    id: int
    event_id: int
    source_id: Optional[int] = None
    tca: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class DecisionCreate(BaseModel):
    action: str
    approved_by: str
    approved_at: datetime
    rationale_text: Optional[str] = None
    decision_driver: Optional[str] = None
    assumption_notes: Optional[str] = None
    override_reason: Optional[str] = None
    checklist_json: Optional[list[str]] = None
    status_after: Optional[str] = None


class DecisionOut(DecisionCreate):
    id: int
    event_id: int
    status_after: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class EventListItem(BaseModel):
    event: ConjunctionEventOut
    time_to_tca_hours: Optional[float] = None


class EventDetailOut(BaseModel):
    event: ConjunctionEventOut
    current_update: Optional[ConjunctionEventUpdateOut] = None
    updates: List[ConjunctionEventUpdateOut] = []
    decision: Optional[DecisionOut]
    cdm_records: List[CdmRecordOut] = []




class RunbookCreate(BaseModel):
    risk_band: str
    template_name: str
    steps: list[str]


class RunbookOut(RunbookCreate):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class WebhookCreate(BaseModel):
    url: str
    event_type: str = "event.created"
    secret: Optional[str] = None


class WebhookOut(WebhookCreate):
    id: int
    active: bool
    created_at: datetime

    class Config:
        from_attributes = True
