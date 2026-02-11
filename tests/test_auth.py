import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import init_db  # noqa: E402
from app.main import app  # noqa: E402


client = TestClient(app)


def setup_module():
    init_db()


def login_business(next_path: str = "/dashboard"):
    os.environ["BUSINESS_ACCESS_CODE"] = "test-code"
    from app.settings import settings as app_settings  # noqa: E402

    app_settings.business_access_code = "test-code"
    return client.post(
        "/auth/login",
        data={"access_code": "test-code", "next": next_path},
        follow_redirects=False,
    )


def test_unauthenticated_events_forbidden():
    resp = client.get("/events")
    assert resp.status_code == 403


def test_unauthenticated_dashboard_redirect():
    resp = client.get("/dashboard", follow_redirects=False)
    assert resp.status_code in (303, 307)
    assert resp.headers.get("location", "").startswith("/auth/login")


def test_login_next_path_is_sanitized():
    resp = login_business("https://evil.example/phish")
    assert resp.status_code in (303, 307)
    assert resp.headers.get("location") == "/dashboard"


def test_cross_site_post_blocked_for_business_session():
    login_business()
    resp = client.post("/demo/seed", headers={"origin": "https://evil.example"})
    assert resp.status_code == 403
    assert resp.json().get("detail") == "Cross-site request blocked"
