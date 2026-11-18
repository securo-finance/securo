"""The archive work runs off the event loop.

Reading, decrypting and unzipping an upload — and zipping a whole workspace on
the way out — is CPU and disk work. Done on the event loop it stalls every
other request the same worker is serving, so both directions hand the work to
a thread. These tests hold that in place: inside the archive call there is no
running loop to block, and the code is not on the main thread.
"""

import asyncio
import threading

from app.api import export as export_api


def _observed_calls(calls: list[dict], original):
    """Wrap a synchronous archive call, recording where it ran."""

    def wrapper(*args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            loop_running = False
        else:
            loop_running = True
        calls.append(
            {
                "loop_running": loop_running,
                "main_thread": threading.current_thread() is threading.main_thread(),
            }
        )
        return original(*args, **kwargs)

    return wrapper


async def test_restore_reads_the_archive_off_the_event_loop(
    client, auth_headers, test_account, monkeypatch
):
    calls: list[dict] = []
    monkeypatch.setattr(export_api, "open_archive", _observed_calls(calls, export_api.open_archive))

    exported = await client.get("/api/export/backup", headers=auth_headers)
    restored = await client.post(
        "/api/export/restore",
        headers=auth_headers,
        files={"file": ("backup.zip", exported.content, "application/zip")},
    )

    assert restored.status_code == 201, restored.text
    assert calls == [{"loop_running": False, "main_thread": False}]


async def test_backup_zips_the_workspace_off_the_event_loop(
    client, auth_headers, test_account, monkeypatch
):
    calls: list[dict] = []
    monkeypatch.setattr(
        export_api,
        "build_backup_archive",
        _observed_calls(calls, export_api.build_backup_archive),
    )

    response = await client.get("/api/export/backup", headers=auth_headers)

    assert response.status_code == 200
    assert calls == [{"loop_running": False, "main_thread": False}]


async def test_protected_backup_encrypts_off_the_event_loop(
    client, auth_headers, test_account, monkeypatch
):
    calls: list[dict] = []
    monkeypatch.setattr(
        export_api,
        "build_backup_archive",
        _observed_calls(calls, export_api.build_backup_archive),
    )

    response = await client.post(
        "/api/export/backup",
        json={"password": "correct horse battery"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert calls == [{"loop_running": False, "main_thread": False}]
