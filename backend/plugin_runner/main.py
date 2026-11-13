"""The runner's internal API, used by the backend only.

    GET    /health                         no token
    GET    /plugins                        what is installed
    PUT    /plugins/{owner}/{name}         install or update (multipart: metadata + archive)
    POST   /plugins/{owner}/{name}/call    run one method
    DELETE /plugins/{owner}/{name}         remove

Every route but /health needs `Authorization: Bearer $PLUGINS_RUNNER_TOKEN`.
The runner refuses to start without that token or the egress secret.

Run with `uvicorn --factory plugin_runner.main:create_app --host 0.0.0.0 --port 8090`.
"""
from __future__ import annotations

import hmac
import json
import logging
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, ValidationError

from plugin_runner import sandbox
from plugin_runner.config import RunnerSettings
from plugin_runner.store import InstallRequest, NotInstalled, PluginFailed, Store, StoreError

logger = logging.getLogger("plugin_runner")


class InstallMetadata(BaseModel):
    commit: str
    sha256: str
    entrypoint: str
    lockfile: str | None = None
    hosts: list[str] = []


class CallRequest(BaseModel):
    method: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    params: dict[str, Any] = {}
    secrets: dict[str, Any] = {}
    config: dict[str, Any] = {}
    securo: dict[str, Any] = {}
    commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    """The commit the caller agreed to; 404 if that commit isn't installed. Latest when omitted."""
    timeout: int | None = Field(default=None, ge=1)


def create_app(settings: RunnerSettings | None = None) -> FastAPI:
    if settings is None:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
        settings = RunnerSettings()
    if not settings.token or not settings.egress_secret:
        raise RuntimeError("PLUGINS_RUNNER_TOKEN and PLUGINS_EGRESS_SECRET must both be set")
    store = Store(settings)
    if not sandbox.isolated():
        logger.warning("not running as root: plugins share the runner's user (fine for development only)")

    def authorized(authorization: Annotated[str, Header()] = "") -> None:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode(), settings.token.encode()):
            raise HTTPException(401, "unauthorized")

    app = FastAPI(title="Securo plugin runner", openapi_url=None, docs_url=None, redoc_url=None)
    guarded = [Depends(authorized)]

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "isolated": sandbox.isolated()}

    @app.get("/plugins", dependencies=guarded)
    async def list_plugins() -> list[dict[str, Any]]:
        return [
            {"id": p.plugin_id, "commit": p.commit, "hosts": p.hosts, "installed_at": p.installed_at}
            for p in store.installed()
        ]

    @app.put("/plugins/{owner}/{name}", dependencies=guarded)
    async def install(
        owner: str, name: str, metadata: Annotated[str, Form()], archive: Annotated[UploadFile, File()]
    ) -> dict[str, Any]:
        try:
            meta = InstallMetadata.model_validate(json.loads(metadata))
        except (ValueError, ValidationError) as exc:
            raise HTTPException(422, "bad metadata") from exc
        limit = settings.archive_limit_mb * 1024 * 1024
        data = await archive.read(limit + 1)
        try:
            installed = await store.install(
                InstallRequest(f"{owner}/{name}", meta.commit, meta.sha256, meta.entrypoint, meta.lockfile, meta.hosts),
                data,
            )
        except StoreError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": installed.plugin_id, "commit": installed.commit, "hosts": installed.hosts}

    @app.post("/plugins/{owner}/{name}/call", dependencies=guarded)
    async def call(owner: str, name: str, body: CallRequest) -> dict[str, Any]:
        try:
            result = await store.call(
                f"{owner}/{name}", body.method, body.params,
                secrets=body.secrets, config=body.config, securo=body.securo,
                commit=body.commit, timeout=body.timeout,
            )
        except NotInstalled as exc:
            raise HTTPException(404, str(exc)) from exc
        except StoreError as exc:
            raise HTTPException(422, str(exc)) from exc
        except PluginFailed as exc:
            return {"ok": False, "error": {"type": exc.kind, "message": exc.message}}
        return {"ok": True, "result": result}

    @app.delete("/plugins/{owner}/{name}", dependencies=guarded, status_code=204)
    async def remove(
        owner: str, name: str, commit: Annotated[str | None, Query(pattern=r"^[0-9a-f]{40}$")] = None
    ) -> None:
        try:
            await store.remove(f"{owner}/{name}", commit)
        except NotInstalled as exc:
            raise HTTPException(404, str(exc)) from exc

    return app

