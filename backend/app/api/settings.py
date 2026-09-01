from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/attachments")
async def get_attachment_settings():
    """Return attachment configuration for this instance."""
    settings = get_settings()
    allowed = [ext.strip().lower() for ext in settings.storage_allowed_extensions.split(",") if ext.strip()]
    return {
        "allowed_extensions": allowed,
        "max_file_size_mb": settings.storage_max_file_size_mb,
        "max_attachments_per_transaction": settings.storage_max_attachments_per_transaction,
        "max_attachments_per_invoice": settings.storage_max_attachments_per_invoice,
    }
