import os
from datetime import datetime, timedelta

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.database import init_db  # noqa: E402


client = TestClient(app)


def login_business():
    # Tests run in-process; configure and use a deterministic access code.
    os.environ["BUSINESS_ACCESS_CODE"] = "test-code"
    from app.settings import settings as app_settings  # noqa: E402

    app_settings.business_access_code = "test-code"
    resp = client.post(
        "/auth/login",
        data={"access_code": "test-code", "next": "/dashboard"},
        follow_redirects=False,
    )
    assert resp.status_code in (303, 307)


def setup_module():
    init_db()


def test_ingest_and_event_flow():
    login_business()
    # Seed a minimal catalog object so conjunction detection has something to screen against.
    client.post("/demo/seed")

    payload_a = {
        "epoch": datetime.utcnow().isoformat(),
        "state_vector": [7000, 0, 0, 0, 7.5, 0],
        "confidence": 0.7,
        "source": {"name": "public-tle", "type": "public"},
        "satellite": {"name": "Alpha", "orbit_regime": "LEO", "status": "active"},
    }
    payload_b = {
        "epoch": datetime.utcnow().isoformat(),
        "state_vector": [7000.005, 0.002, 0.001, 0, 7.49, 0.01],
        "confidence": 0.6,
        "source": {"name": "public-tle", "type": "public"},
        "satellite": {"name": "Beta", "orbit_regime": "LEO", "status": "active"},
    }

    resp_a = client.post("/ingest/orbit-state", json=payload_a)
    assert resp_a.status_code == 200

    resp_b = client.post("/ingest/orbit-state", json=payload_b)
    assert resp_b.status_code == 200

    events = client.get("/events")
    assert events.status_code == 200
    data = events.json()
    assert len(data) >= 1

    # Basic PDF report endpoint smoke test.
    event_id = data[0]["event"]["id"]
    report = client.get(f"/events/{event_id}/report")
    assert report.status_code == 200
    assert "application/pdf" in report.headers.get("content-type", "")


def test_decision_and_audit_export():
    login_business()
    # Ensure at least one event exists for decision/audit flows.
    client.post("/demo/seed")

    events = client.get("/events").json()
    event_id = events[0]["event"]["id"]

    decision_payload = {
        "action": "maneuver",
        "approved_by": "ops@example.com",
        "approved_at": datetime.utcnow().isoformat(),
        "rationale_text": "Risk score exceeded threshold.",
    }
    decision_resp = client.post(f"/events/{event_id}/decisions", json=decision_payload)
    assert decision_resp.status_code == 200

    export_resp = client.get("/audit/export?format=csv")
    assert export_resp.status_code == 200
    assert "text/csv" in export_resp.headers.get("content-type", "")


def test_ingest_cdm_creates_event_with_geometry():
    login_business()
    payload = {
        "tca": datetime.utcnow().isoformat(),
        "relative_position_km": [0.02, 0.0, 0.0],
        "relative_velocity_km_s": [0.0, 0.01, 0.0],
        "combined_pos_covariance_km2": [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ],
        "hard_body_radius_m": 10.0,
        "source": {"name": "cdm-test", "type": "public"},
        "satellite": {"name": "Gamma", "orbit_regime": "LEO", "status": "active"},
        "secondary_norad_cat_id": 424242,
        "secondary_name": "TEST-OBJECT",
    }
    resp = client.post("/ingest/cdm", json=payload)
    assert resp.status_code == 200
    event_id = resp.json()["event_id"]

    detail = client.get(f"/events/{event_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["geometry"] is not None
    assert body["risk"] is not None


def test_ui_pages_smoke():
    login_business()
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    resp = client.get("/events-ui")
    assert resp.status_code == 200
    resp = client.get("/satellites-ui")
    assert resp.status_code == 200
    resp = client.get("/ingest-ui")
    assert resp.status_code == 200
    resp = client.get("/catalog-ui")
    assert resp.status_code == 200
