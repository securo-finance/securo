from fastapi import HTTPException, status

from app.core.config import get_settings


def local_auth_enabled() -> bool:
    """True when the deployment still accepts local credentials."""
    return get_settings().local_auth_enabled


def require_local_auth_enabled() -> None:
    """Reject local credential operations when OIDC-only mode is active."""
    if not local_auth_enabled():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="LOCAL_AUTH_DISABLED",
        )
