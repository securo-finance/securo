"""Packing a workspace backup into a zip, optionally encrypted.

The archive is the user's own copy of their data, so it stays a plain zip of
plain JSON by default: anything that can open a zip can read it, today and in
ten years. A password swaps the container for an AES-256 one (WinZip AES, the
scheme 7-Zip, Keka and WinRAR all speak) so the file stays openable by ordinary
tools while the contents travel safely through whatever cloud or drive the user
keeps it on.

AES rather than the legacy ZipCrypto that "password-protected zip" often means:
ZipCrypto's key is recoverable from a few known plaintext bytes, and a backup
full of JSON is nothing but known plaintext.
"""
import io
import json
import zipfile

import pyzipper

#: The zip encrypts entry *contents*, never the central directory, so the file
#: names and their sizes stay readable without the password. Nothing sensitive
#: goes in a name — they are the fixed entity names below plus `metadata.json`.
_COMPRESSION = zipfile.ZIP_DEFLATED


def build_backup_archive(files: dict[str, object], password: str | None = None) -> bytes:
    """Serialize `files` (name → JSON-serializable payload) into a zip.

    Returns the archive bytes. With a password the entries are encrypted with
    AES-256; without one the result is byte-for-byte the plain zip Securo has
    always produced.
    """
    buf = io.BytesIO()
    if password:
        archive = pyzipper.AESZipFile(
            buf, "w", compression=_COMPRESSION, encryption=pyzipper.WZ_AES
        )
        archive.setpassword(password.encode("utf-8"))
    else:
        archive = zipfile.ZipFile(buf, "w", _COMPRESSION)

    with archive as zf:
        for name, payload in files.items():
            zf.writestr(name, json.dumps(payload, indent=2, ensure_ascii=False))

    return buf.getvalue()
