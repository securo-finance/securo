"""A `Content-Disposition` value that survives any filename.

Header values go out as latin-1, so a name built from free text (an
invoice number prefix in Greek, a `№`) raises before the response is
sent, and a `"` in it ends the quoted parameter early. RFC 6266 answers
both: an ASCII `filename` for old clients plus `filename*` in UTF-8.
"""
import re
from urllib.parse import quote

_UNSAFE = re.compile(r'[\x00-\x1f\x7f"\\/]')


def content_disposition(disposition: str, filename: str) -> str:
    name = _UNSAFE.sub("-", filename)
    fallback = name.encode("ascii", "replace").decode("ascii").replace("?", "_")
    return (
        f'{disposition}; filename="{fallback}"; '
        f"filename*=UTF-8''{quote(name, safe='')}"
    )
