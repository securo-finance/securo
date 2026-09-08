"""The workspace's mark, stored as a file it owns.

Three decisions, each of which removes a class of problem rather than
handling it:

  - **Uploaded, not linked.** A remote URL fetches the logo from a third
    party every time a document is drawn. On a self-hosted install that
    is a request leaving the building for an image the user already has,
    it breaks when that host does, and it lets a document's contents be
    changed by somebody who is not the user.

  - **Normalised to PNG.** Whatever arrives is decoded and re-encoded,
    which fixes the storage key's extension, gives the serving route one
    content type to answer with, and drops the metadata a photo carries
    (a logo exported from a phone can arrive with GPS coordinates in it).

  - **Bounded.** Scaled to fit a box no document needs to exceed. A
    4000px mark makes every PDF heavier for a picture drawn 40px tall.
"""
import io
import uuid
from typing import Optional

from PIL import Image, UnidentifiedImageError
from PIL.Image import Resampling
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import InvoiceSettings
from app.providers import get_storage_provider

#: Roughly twice the largest box any renderer draws it in, so a screen at
#: two device pixels per CSS pixel still has real detail to show.
MAX_EDGE = 600

#: What a logo may cost to decode. Generous for a mark — a 6000 × 4000
#: photograph fits — and far below the point where one request can starve
#: the others.
MAX_PIXELS = 25_000_000

#: The formats a browser will render and Pillow will open. Anything else
#: is refused by name rather than failing later inside a decoder.
ACCEPTED = {"image/png", "image/jpeg", "image/webp", "image/gif"}


def storage_key(workspace_id: uuid.UUID, logo_id: uuid.UUID) -> str:
    """Where a logo lives, derived rather than stored.

    Keeping the key computable from the id is what lets a snapshot freeze
    an id alone and still resolve to a file years later.
    """
    return f"{workspace_id}/invoices/logo/{logo_id}.png"


def normalise(data: bytes, content_type: str) -> bytes:
    """Decode, bound and re-encode as PNG. Raises ValueError if unusable."""
    if content_type not in ACCEPTED:
        raise ValueError(
            f"'{content_type}' is not an image this can use. "
            "Use a PNG, JPEG, WebP or GIF."
        )
    try:
        image = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("That file could not be read as an image.") from exc

    # Checked before `load`, which is what actually decodes. A 10,000 ×
    # 10,000 PNG of flat colour compresses to a few kilobytes and passes
    # every size limit above, then asks for hundreds of megabytes here —
    # and `thumbnail` only shrinks it afterwards, far too late.
    width, height = image.size
    if width * height > MAX_PIXELS:
        raise ValueError(
            f"That image is {width}×{height}. A logo may be at most "
            f"{MAX_PIXELS // 1_000_000} megapixels."
        )

    try:
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("That file could not be read as an image.") from exc

    # RGBA throughout: a logo is normally transparent, and converting a
    # palette image without this flattens the transparency to black.
    if image.mode not in ("RGBA", "RGB"):
        image = image.convert("RGBA")
    image.thumbnail((MAX_EDGE, MAX_EDGE), Resampling.LANCZOS)

    out = io.BytesIO()
    # No EXIF is carried over: `save` writes only what is passed, and
    # nothing here passes the source's metadata.
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


async def store(
    session: AsyncSession,
    settings: InvoiceSettings,
    workspace_id: uuid.UUID,
    data: bytes,
    content_type: str,
) -> uuid.UUID:
    """Put a new logo in place and return its id.

    The previous file is deliberately left where it is: invoices issued
    while it was current froze its id, and deleting it would repaint
    documents their recipients already hold.
    """
    png = normalise(data, content_type)
    logo_id = uuid.uuid4()
    storage = get_storage_provider()
    await storage.upload(storage_key(workspace_id, logo_id), png, "image/png")
    settings.logo_id = logo_id
    await session.flush()
    return logo_id


async def read(workspace_id: uuid.UUID, logo_id: uuid.UUID) -> Optional[bytes]:
    """The bytes of one logo, or None if that id has no file."""
    storage = get_storage_provider()
    try:
        return await storage.download(storage_key(workspace_id, logo_id))
    except Exception:
        # A missing logo is a document without a mark on it, never a
        # failed render.
        return None


async def clear(session: AsyncSession, settings: InvoiceSettings) -> None:
    """Stop using the current logo.

    The file stays for the same reason a replacement leaves it: documents
    issued under it still point at it.
    """
    settings.logo_id = None
    await session.flush()
