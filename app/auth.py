from typing import Optional

from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse


def session_role(request: Request) -> str:
    # Starlette's Request.session property asserts SessionMiddleware is installed.
    # For robustness (and tests), read from the ASGI scope directly.
    session = request.scope.get("session")
    role = session.get("role") if isinstance(session, dict) else None
    return str(role) if role else "public"


def is_business(request: Request) -> bool:
    return session_role(request) == "business"


def require_business(request: Request) -> None:
    if not is_business(request):
        raise HTTPException(status_code=403, detail="Business access required")


def require_business_ui(request: Request) -> None:
    # Deprecated: prefer returning login_redirect() from route handlers.
    if not is_business(request):
        raise HTTPException(status_code=403, detail="Business access required")


def business_access_configured(access_code: Optional[str]) -> bool:
    return bool(access_code and access_code.strip())


def login_redirect(next_path: str = "/dashboard") -> RedirectResponse:
    params = urlencode({"next": next_path})
    return RedirectResponse(url=f"/auth/login?{params}", status_code=303)
