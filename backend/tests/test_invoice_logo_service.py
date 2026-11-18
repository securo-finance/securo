"""What happens when a logo cannot be read.

A logo is decoration: no document should fail because the bytes are gone.
But "gone" and "storage is down" are different events, and only the first
one is normal — the second must leave a trace, or an S3 outage looks like
every workspace simply removing its mark.
"""
import logging
import uuid

from app.services import invoice_logo_service


class Boom(Exception):
    """Stands in for an S3 error that is not a missing key."""


class FakeStorage:
    def __init__(self, error: Exception):
        self.error = error

    async def download(self, storage_key: str) -> bytes:
        raise self.error


def _use(monkeypatch, error: Exception) -> None:
    monkeypatch.setattr(
        invoice_logo_service, "get_storage_provider", lambda: FakeStorage(error)
    )


async def test_missing_file_is_silent(monkeypatch, caplog):
    _use(monkeypatch, FileNotFoundError("File not found: logo"))
    with caplog.at_level(logging.WARNING):
        assert await invoice_logo_service.read(uuid.uuid4(), uuid.uuid4()) is None
    assert caplog.records == []


async def test_other_failures_are_logged(monkeypatch, caplog):
    _use(monkeypatch, Boom("bucket unavailable"))
    with caplog.at_level(logging.WARNING):
        assert await invoice_logo_service.read(uuid.uuid4(), uuid.uuid4()) is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].exc_info is not None
