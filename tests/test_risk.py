from datetime import datetime, timedelta

from app.services import risk
from app.services.conjunction import Encounter


def make_encounter(miss_km: float, rel_speed: float = 10.0, tca_hours: float = 10.0) -> Encounter:
    now = datetime.utcnow()
    tca = now + timedelta(hours=tca_hours)
    return Encounter(
        tca=tca,
        miss_distance_km=miss_km,
        relative_velocity_km_s=rel_speed,
        r_rel_eci_km=[miss_km, 0.0, 0.0],
        v_rel_eci_km_s=[0.0, rel_speed, 0.0],
    )


def test_risk_tier_thresholds():
    now = datetime.utcnow()

    high = risk.assess_encounter(
        make_encounter(0.5),
        now=now,
        dt_hours=10.0,
        primary_conf=0.8,
        secondary_conf=0.8,
        primary_source_type="public",
        secondary_source_type="public",
        primary_age_hours=1.0,
        secondary_age_hours=1.0,
    )
    assert high.risk_tier == "high"

    watch = risk.assess_encounter(
        make_encounter(3.0),
        now=now,
        dt_hours=10.0,
        primary_conf=0.8,
        secondary_conf=0.8,
        primary_source_type="public",
        secondary_source_type="public",
        primary_age_hours=1.0,
        secondary_age_hours=1.0,
    )
    assert watch.risk_tier in {"watch", "high"}

    low = risk.assess_encounter(
        make_encounter(9.0),
        now=now,
        dt_hours=200.0,
        primary_conf=0.8,
        secondary_conf=0.8,
        primary_source_type="public",
        secondary_source_type="public",
        primary_age_hours=1.0,
        secondary_age_hours=1.0,
    )
    assert low.risk_tier == "low"


def test_confidence_degrades_with_age_for_public_sources():
    now = datetime.utcnow()
    fresh = risk.assess_encounter(
        make_encounter(5.0),
        now=now,
        dt_hours=10.0,
        primary_conf=0.8,
        secondary_conf=0.8,
        primary_source_type="public",
        secondary_source_type="public",
        primary_age_hours=1.0,
        secondary_age_hours=1.0,
    )
    stale = risk.assess_encounter(
        make_encounter(5.0),
        now=now,
        dt_hours=10.0,
        primary_conf=0.8,
        secondary_conf=0.8,
        primary_source_type="public",
        secondary_source_type="public",
        primary_age_hours=200.0,
        secondary_age_hours=200.0,
    )
    assert stale.confidence_score <= fresh.confidence_score

