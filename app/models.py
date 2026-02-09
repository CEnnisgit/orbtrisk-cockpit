from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Boolean,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.types import JSON

from app.database import Base


class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    type = Column(String(64), nullable=False)
    provenance_uri = Column(String(512), nullable=True)
    license_terms = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SpaceObject(Base):
    __tablename__ = "space_objects"

    id = Column(Integer, primary_key=True)
    norad_cat_id = Column(Integer, nullable=True, index=True)
    name = Column(String(256), nullable=False, index=True)
    object_type = Column(String(64), nullable=True)
    international_designator = Column(String(64), nullable=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=True)
    is_operator_asset = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    source = relationship("Source")


class SpaceObjectMetadata(Base):
    __tablename__ = "space_object_metadata"

    space_object_id = Column(Integer, ForeignKey("space_objects.id"), primary_key=True)
    satcat_json = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    space_object = relationship("SpaceObject")


class Satellite(Base):
    __tablename__ = "satellites"

    id = Column(Integer, primary_key=True)
    space_object_id = Column(Integer, ForeignKey("space_objects.id"), nullable=True)
    operator_id = Column(String(64), nullable=True)
    name = Column(String(128), nullable=False)
    catalog_id = Column(String(64), nullable=True)
    orbit_regime = Column(String(32), nullable=False, default="LEO")
    status = Column(String(32), nullable=False, default="active")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    space_object = relationship("SpaceObject")
    orbit_states = relationship("OrbitState", back_populates="satellite")


class OrbitState(Base):
    __tablename__ = "orbit_states"

    id = Column(Integer, primary_key=True)
    satellite_id = Column(Integer, ForeignKey("satellites.id"), nullable=True)
    space_object_id = Column(Integer, ForeignKey("space_objects.id"), nullable=True)
    epoch = Column(DateTime, nullable=False)
    frame = Column(String(32), nullable=False, default="ECI")
    valid_from = Column(DateTime, nullable=True)
    valid_to = Column(DateTime, nullable=True)
    state_vector = Column(JSON, nullable=False)
    covariance = Column(JSON, nullable=True)
    provenance_json = Column(JSON, nullable=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=False)
    confidence = Column(Float, nullable=False, default=0.5)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    satellite = relationship("Satellite", back_populates="orbit_states")
    space_object = relationship("SpaceObject")
    source = relationship("Source")


class TleRecord(Base):
    __tablename__ = "tle_records"

    id = Column(Integer, primary_key=True)
    space_object_id = Column(Integer, ForeignKey("space_objects.id"), nullable=False, index=True)
    line1 = Column(String(256), nullable=False)
    line2 = Column(String(256), nullable=False)
    epoch = Column(DateTime, nullable=False)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=True)
    ingested_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    raw_text = Column(Text, nullable=False)

    space_object = relationship("SpaceObject")
    source = relationship("Source")


class ConjunctionEvent(Base):
    __tablename__ = "conjunction_events"

    id = Column(Integer, primary_key=True)
    satellite_id = Column(Integer, ForeignKey("satellites.id"), nullable=False)
    object_id = Column(Integer, nullable=True)
    space_object_id = Column(Integer, ForeignKey("space_objects.id"), nullable=True)
    tca = Column(DateTime, nullable=False, index=True)
    miss_distance = Column(Float, nullable=False)
    relative_velocity = Column(Float, nullable=False)
    screening_volume = Column(Float, nullable=False)
    risk_tier = Column(String(32), nullable=False, default="unknown")
    risk_score = Column(Float, nullable=False, default=0.0)
    confidence_score = Column(Float, nullable=False, default=0.0)
    confidence_label = Column(String(8), nullable=False, default="D")
    current_update_id = Column(Integer, nullable=True)
    last_seen_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    status = Column(String(32), nullable=False, default="open", index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    satellite = relationship("Satellite")
    space_object = relationship("SpaceObject")


class ConjunctionEventUpdate(Base):
    __tablename__ = "conjunction_event_updates"

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("conjunction_events.id"), nullable=False, index=True)
    computed_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    primary_orbit_state_id = Column(Integer, ForeignKey("orbit_states.id"), nullable=True)
    secondary_orbit_state_id = Column(Integer, ForeignKey("orbit_states.id"), nullable=True)
    primary_tle_record_id = Column(Integer, ForeignKey("tle_records.id"), nullable=True)
    secondary_tle_record_id = Column(Integer, ForeignKey("tle_records.id"), nullable=True)
    cdm_record_id = Column(Integer, ForeignKey("cdm_records.id"), nullable=True)

    tca = Column(DateTime, nullable=False)
    miss_distance_km = Column(Float, nullable=False)
    relative_velocity_km_s = Column(Float, nullable=False)
    screening_volume_km = Column(Float, nullable=False)

    r_rel_eci_km = Column(JSON, nullable=True)
    v_rel_eci_km_s = Column(JSON, nullable=True)
    r_rel_rtn_km = Column(JSON, nullable=True)
    v_rel_rtn_km_s = Column(JSON, nullable=True)

    risk_tier = Column(String(32), nullable=False)
    risk_score = Column(Float, nullable=False)
    confidence_score = Column(Float, nullable=False)
    confidence_label = Column(String(8), nullable=False)

    drivers_json = Column(JSON, nullable=True)
    details_json = Column(JSON, nullable=True)

    event = relationship("ConjunctionEvent", foreign_keys=[event_id])
    primary_orbit_state = relationship("OrbitState", foreign_keys=[primary_orbit_state_id])
    secondary_orbit_state = relationship("OrbitState", foreign_keys=[secondary_orbit_state_id])
    primary_tle_record = relationship("TleRecord", foreign_keys=[primary_tle_record_id])
    secondary_tle_record = relationship("TleRecord", foreign_keys=[secondary_tle_record_id])
    cdm_record = relationship("CdmRecord", foreign_keys=[cdm_record_id])


class CdmRecord(Base):
    __tablename__ = "cdm_records"

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("conjunction_events.id"), nullable=False, index=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=True)
    tca = Column(DateTime, nullable=False)
    message_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    event = relationship("ConjunctionEvent")
    source = relationship("Source")


class Decision(Base):
    __tablename__ = "decisions"

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("conjunction_events.id"), nullable=False)
    action = Column(String(64), nullable=False)
    approved_by = Column(String(128), nullable=False)
    approved_at = Column(DateTime, nullable=False)
    rationale_text = Column(Text, nullable=True)
    decision_driver = Column(String(128), nullable=True)
    assumption_notes = Column(Text, nullable=True)
    override_reason = Column(Text, nullable=True)
    checklist_json = Column(JSON, nullable=True)
    status_after = Column(String(32), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    event = relationship("ConjunctionEvent")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    entity_type = Column(String(64), nullable=False)
    entity_id = Column(Integer, nullable=False)
    hash = Column(String(128), nullable=False)
    prev_hash = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class WebhookSubscription(Base):
    __tablename__ = "webhook_subscriptions"

    id = Column(Integer, primary_key=True)
    url = Column(String(512), nullable=False)
    event_type = Column(String(64), nullable=False, default="event.created")
    secret = Column(String(128), nullable=True)
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Runbook(Base):
    __tablename__ = "runbooks"

    id = Column(Integer, primary_key=True)
    risk_band = Column(String(32), nullable=False)
    template_name = Column(String(128), nullable=False)
    steps_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
