from fastapi import APIRouter

from app.agents.config import get_agent_settings
from app.agents.providers import list_providers

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("/info")
async def get_agents_info():
    """Capability discovery for the frontend. Always available when the
    agents router is mounted (i.e. the feature is enabled instance-wide)."""
    s = get_agent_settings()
    return {
        "enabled": True,
        "providers": list_providers(),
        "embedding_dim": s.embedding_dim,
        "default_top_n": s.default_top_n,
        "default_similarity_threshold": s.default_similarity_threshold,
        "extra_mcp_servers_configured": bool(s.extra_mcp_servers.strip()),
        "mcp_external_ttl_days": s.mcp_external_ttl_days,
        # Empty string means "let the frontend derive it from window.location".
        "external_mcp_url": s.external_mcp_url.strip(),
    }
