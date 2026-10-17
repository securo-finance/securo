"""Cache-Control for the frontend served in-process by the API.

Only relevant to the all-in-one image, where FastAPI serves the built SPA
instead of nginx (see `frontend_dist_path` and `app.frontend()` in
`app.main`). `app.frontend()` emits ETag and Last-Modified but no
Cache-Control, and the nginx config it replaces sets one deliberately.

Pure ASGI rather than `BaseHTTPMiddleware`: that base class buffers the
whole response before passing it on, which would break the agents chat
stream.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: `frontend/vite.config.ts` pins `build.assetsDir` to `static`, so every
#: content-hashed JS/CSS file lives here and can be cached forever. Anything
#: else the frontend route serves — `index.html` above all — must not be,
#: because a cached shell keeps asking for asset hashes that a later deploy
#: has already replaced.
_IMMUTABLE_PREFIX = "/static/"

_IMMUTABLE = b"public, max-age=31536000, immutable"
_NO_STORE = b"no-store"


class FrontendCacheHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path", "").startswith("/api"):
            await self.app(scope, receive, send)
            return

        immutable = scope["path"].startswith(_IMMUTABLE_PREFIX)

        async def send_with_cache_control(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                # Never override a route that has already decided for itself.
                if not any(key.lower() == b"cache-control" for key, _ in headers):
                    headers.append(
                        (b"cache-control", _IMMUTABLE if immutable else _NO_STORE)
                    )
            await send(message)

        await self.app(scope, receive, send_with_cache_control)
