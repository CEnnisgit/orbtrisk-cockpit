import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import init_db  # noqa: E402
from app.main import app  # noqa: E402


client = TestClient(app)


def setup_module():
    init_db()


def test_unauthenticated_events_forbidden():
    resp = client.get("/events")
    assert resp.status_code == 403


def test_unauthenticated_dashboard_redirect():
    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code in (303, 307)
    assert resp.headers.get("location", "").startswith("/auth/login")

