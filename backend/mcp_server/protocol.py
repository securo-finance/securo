"""Model Context Protocol wire rules for Securo's built-in server.

The server is dual-era, as the 2026-07-28 revision describes:

- **Modern** (`2026-07-28`): stateless. There is no `initialize` handshake;
  every request carries its protocol version and client capabilities in
  `params._meta`, mirrored into HTTP headers that the server must check
  against the body.
- **Legacy** (`2025-11-25`, `2025-06-18`, `2025-03-26`): the `initialize`
  handshake era. Kept because every MCP client that predates 2026-07-28
  speaks it, and because a dual-era client falls back to it when a modern
  request is refused.

How a request is classified is decided once, in `main.py`; this module holds
the constants and the checks both paths share.

Spec: https://modelcontextprotocol.io/specification/2026-07-28/
"""
from __future__ import annotations

import base64
import binascii
import re
from typing import Any, Optional

# Newest first. A modern client picks from `supportedVersions`; a legacy
# client negotiates within LEGACY_VERSIONS through `initialize`.
MODERN_VERSIONS: tuple[str, ...] = ("2026-07-28",)
LEGACY_VERSIONS: tuple[str, ...] = ("2025-11-25", "2025-06-18", "2025-03-26")
SUPPORTED_VERSIONS: tuple[str, ...] = MODERN_VERSIONS + LEGACY_VERSIONS

# Clients from before 2025-06-18 did not send `MCP-Protocol-Version`. The
# spec lets a server that supports them treat a header-less request as
# 2025-03-26, and Securo's own in-app agent sent no header until this
# change — so header-less requests stay on the legacy path.
HEADERLESS_LEGACY_VERSION = "2025-03-26"
LATEST_LEGACY_VERSION = LEGACY_VERSIONS[0]

MODERN_METHODS = frozenset({"server/discover", "tools/list", "tools/call"})
LEGACY_METHODS = frozenset({"initialize", "ping", "tools/list", "tools/call"})

# `_meta` keys reserved by the spec.
META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

# Securo's own per-tool extras. `_meta` keys need a reverse-DNS prefix, and
# a prefix whose second label is `modelcontextprotocol`/`mcp` is reserved.
TOOL_META_KEY = "com.usesecuro/tool"

# JSON-RPC 2.0.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
# Allocated by the MCP spec in its reserved -32020..-32099 sub-range.
HEADER_MISMATCH = -32020
UNSUPPORTED_PROTOCOL_VERSION = -32022

# Caching hints required on `server/discover` and `tools/list`. The tool set
# is fixed per deploy and identical for every caller, so it is `public`; five
# minutes keeps a client from re-listing on every turn without pinning a
# stale list across a release for long.
LIST_TTL_MS = 300_000
LIST_CACHE_SCOPE = "public"

_BASE64_SENTINEL = re.compile(r"^=\?base64\?(?P<body>.*)\?=$", re.DOTALL)
# RFC 9110 field-value characters: visible ASCII, space, horizontal tab.
_HEADER_SAFE = re.compile(r"^[\x20-\x7e\t]*$")


class ProtocolError(Exception):
    """A request the transport must refuse with a specific status and code."""

    def __init__(
        self,
        status: int,
        code: int,
        message: str,
        *,
        data: Any = None,
        request_id: Any = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.data = data
        self.request_id = request_id

    def body(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            error["data"] = self.data
        out: dict[str, Any] = {"jsonrpc": "2.0", "error": error}
        # A JSON-RPC error with no id is allowed when the id could not be read.
        if self.request_id is not None:
            out["id"] = self.request_id
        return out


def unsupported_version(requested: str, request_id: Any) -> ProtocolError:
    return ProtocolError(
        400,
        UNSUPPORTED_PROTOCOL_VERSION,
        "Unsupported protocol version",
        data={"supported": list(SUPPORTED_VERSIONS), "requested": requested},
        request_id=request_id,
    )


def header_mismatch(message: str, request_id: Any) -> ProtocolError:
    return ProtocolError(400, HEADER_MISMATCH, f"Header mismatch: {message}", request_id=request_id)


def decode_header_value(value: str) -> Optional[str]:
    """Undo the spec's `=?base64?…?=` sentinel. None means undecodable.

    `Mcp-Name` is carried base64-encoded when the name cannot travel as a
    plain header value; the server must decode before comparing to the body.
    """
    match = _BASE64_SENTINEL.match(value)
    if match is None:
        return value if _HEADER_SAFE.match(value) else None
    try:
        return base64.b64decode(match.group("body"), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None


def encode_header_value(value: str) -> str:
    """The client side of `decode_header_value`."""
    needs_encoding = (
        not _HEADER_SAFE.match(value)
        or value != value.strip()
        or _BASE64_SENTINEL.match(value) is not None
    )
    if not needs_encoding:
        return value
    return "=?base64?" + base64.b64encode(value.encode("utf-8")).decode("ascii") + "?="


def parse_allowed_origins(raw: str) -> frozenset[str]:
    return frozenset(o.strip().rstrip("/") for o in (raw or "").split(",") if o.strip())


def origin_allowed(origin: Optional[str], allowed: frozenset[str]) -> bool:
    """The spec's DNS-rebinding guard.

    Servers MUST validate `Origin` and answer 403 when it is present and
    invalid. Every legitimate caller of this server is another process —
    the backend's agent runtime, or an MCP gateway in the cluster — and none
    of them send `Origin`. A browser always does. So an absent header passes
    and a present one must be explicitly allowed.
    """
    if origin is None:
        return True
    return origin.rstrip("/") in allowed


# Proposals whose apply step only inserts a new row and touches nothing that
# already exists. Listed by name rather than matched by prefix:
# `propose_create_rule` can apply the new rule to existing transactions and
# overwrite their categories.
ADDITIVE_PROPOSALS = frozenset(
    {
        "propose_create_budget",
        "propose_create_category",
        "propose_create_goal",
        "propose_create_payee_rule",
        "propose_create_recurring_transaction",
        "propose_create_transaction",
    }
)


def tool_annotations(name: str, *, is_proposal: bool) -> dict[str, Any]:
    """Behaviour hints for one tool, derived from what it can do.

    Clients treat these as untrusted hints, but a gateway uses them to decide
    what to put behind an approval prompt, so they err towards caution:

    - Every non-proposal tool only reads.
    - A proposal can write when an external caller applies it, so it is never
      read-only. The proposals in `ADDITIVE_PROPOSALS` only add rows, so they
      are marked non-destructive. Every other proposal omits
      `destructiveHint`, which the spec defaults to true —
      `propose_categorize` overwrites categories in bulk and
      `propose_cancel_recurring_transaction` can hard-delete, and a tool added
      later inherits the cautious default rather than a guess.
    - Nothing here reaches outside Securo's own ledger.
    """
    if not is_proposal:
        return {"readOnlyHint": True, "openWorldHint": False}
    hints: dict[str, Any] = {"readOnlyHint": False, "openWorldHint": False}
    if name in ADDITIVE_PROPOSALS:
        hints["destructiveHint"] = False
    return hints
