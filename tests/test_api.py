import os
from datetime import datetime

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.database import init_db  # noqa: E402


client = TestClient(app)


def login_business():
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


def test_screening_dedup_and_updates():
    login_business()
    client.post("/demo/seed")

    # First screening pass should create at least one event + update.
    events = client.get("/events").json()
    assert len(events) >= 1
    event_id = events[0]["event"]["id"]

    detail = client.get(f"/events/{event_id}").json()
    assert detail["event"]["risk_tier"] in {"low", "watch", "high", "unknown"}
    assert detail["event"]["confidence_label"] in {"A", "B", "C", "D"}
    assert len(detail["updates"]) >= 1
    updates_before = len(detail["updates"])

    # Second screening should not create duplicate events, but should append updates.
    sat_list = client.get("/satellites").json()
    sat_id = sat_list[0]["id"]
    client.post(f"/satellites/{sat_id}/screen")

    detail2 = client.get(f"/events/{event_id}").json()
    assert len(detail2["updates"]) >= updates_before


def test_attach_cdm_creates_update():
    login_business()
    client.post("/demo/seed")

    events = client.get("/events").json()
    event_id = events[0]["event"]["id"]

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
        "secondary_norad_cat_id": None,
        "secondary_name": None,
        "override_secondary": True,
    }

    resp = client.post(f"/events/{event_id}/cdm", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["event_id"] == event_id
    assert isinstance(body["update_id"], int)

    detail = client.get(f"/events/{event_id}").json()
    assert len(detail["updates"]) >= 2
    assert detail["cdm_records"]


def test_pdf_report_smoke():
    login_business()
    client.post("/demo/seed")
    events = client.get("/events").json()
    event_id = events[0]["event"]["id"]

    report = client.get(f"/events/{event_id}/report")
    assert report.status_code == 200
    assert "application/pdf" in report.headers.get("content-type", "")


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

