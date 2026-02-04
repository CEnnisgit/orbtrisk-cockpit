import os
from datetime import datetime, timedelta

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.database import init_db  # noqa: E402


client = TestClient(app)


def setup_module():
    init_db()


def test_ingest_and_event_flow():
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
    assert len(events.json()) >= 1


def test_decision_and_audit_export():
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
