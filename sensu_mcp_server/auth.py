"""
Sensu Authentication
====================

Per-request Bearer auth — the client passes its own Sensu API key as the
Bearer token.  The server validates that the token is non-empty and forwards
it directly to the Sensu REST API.

Flow:
  User (or admin) creates a Sensu API key:
    sensuctl api-key grant <username>
  User puts the key in their MCP client config:
    Authorization: Bearer <api-key>
  Server extracts the key, builds a per-request SensuRestClient with it,
  and forwards it to Sensu as:
    Authorization: Key <api-key>

Read-only users hold a key created for a read-only Sensu user.
Admins hold a key with broader permissions — no server-side change needed.
"""

import sensu_mcp_server.config as _config_module
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_http_headers
from sensu_mcp_server.sensu_client import SensuRestClient


class SensuBearerAuthProvider(TokenVerifier):
    """
    Accepts any non-empty Bearer token as a Sensu API key.

    Sensu API keys are opaque — there is nothing to cryptographically
    validate on the MCP server side.  We simply confirm the token is present
    and let Sensu itself reject invalid or insufficient keys.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token:
            return None
        # Use the token itself as the client_id so it's available downstream
        return AccessToken(
            token=token,
            client_id=token,
            scopes=[],
            expires_at=None,
        )


def get_sensu_client() -> SensuRestClient:
    """
    Return a per-request SensuRestClient.

    Both the Sensu URL and API key are resolved per-request, with env fallbacks
    for stdio/dev mode:

      X-Sensu-URL header           → per-user Sensu URL   (falls back to SENSU_URL)
      Authorization: Bearer <key>  → per-user API key     (falls back to SENSU_API_KEY)
    """
    cfg = _config_module.settings
    headers = get_http_headers()

    url = headers.get("x-sensu-url", "") or (str(cfg.sensu_url) if cfg.sensu_url else "")
    if not url:
        raise ToolError(
            "No Sensu URL available. Set SENSU_URL in the environment "
            "or pass X-Sensu-URL in the request header."
        )

    auth_header = headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        api_key = auth_header[7:]
    elif cfg.sensu_api_key:
        api_key = cfg.sensu_api_key.get_secret_value()
    else:
        raise ToolError(
            "No Sensu API key available. Set SENSU_API_KEY in the environment "
            "or supply a Bearer token in the Authorization header."
        )

    return SensuRestClient(
        url=url,
        api_key=api_key,
        namespace=cfg.sensu_namespace,
        verify_ssl=cfg.verify_ssl,
    )
