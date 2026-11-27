"""Securo's built-in MCP (Model Context Protocol) server.

Runs as a separate container (`mcp-server` in docker-compose, gated by the
`agents` profile). Exposes Securo's read APIs and guarded proposal tools over
JSON-RPC 2.0.

The server reuses the backend's Python image — same SQLAlchemy models,
same services — but listens on its own port (8765). The agent runtime
mints a short-lived JWT scoped to (user_id, conversation_id) and passes
it as a Bearer token; the MCP server verifies it and runs each tool with
that user's identity.

Tools are read-only in v1. Mutations are returned as `propose_*` previews
that the agent surfaces to the user; the user confirms in the UI, which
then triggers the existing Securo write endpoint. This keeps MCP safe.
"""
