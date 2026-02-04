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
    state_vector: List[float]
    covariance: Optional[List[List[float]]]
    source_id: int
    confidence: float
    created_at: datetime

    class Config:
        from_attributes = True


class ConjunctionEventOut(BaseModel):
    id: int
    satellite_id: int
    object_id: Optional[int] = None
    space_object_id: Optional[int] = None
    tca: datetime
    miss_distance: float
    relative_velocity: float
    screening_volume: float
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class RiskAssessmentOut(BaseModel):
    id: int
    event_id: int
    poc: float
    risk_score: float
    components_json: dict
    sensitivity_json: dict
    created_at: datetime

    class Config:
        from_attributes = True


class ManeuverOptionOut(BaseModel):
    id: int
    event_id: int
    delta_v: float
    time_window_start: datetime
    time_window_end: datetime
    risk_after: float
    fuel_cost: float
    is_recommended: bool
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
    risk_score: Optional[float] = None


class EventDetailOut(BaseModel):
    event: ConjunctionEventOut
    risk: Optional[RiskAssessmentOut]
    maneuvers: List[ManeuverOptionOut]
    decision: Optional[DecisionOut]




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
