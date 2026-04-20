"""Sensu REST API client."""

from typing import Any

import requests


class SensuRestClient:
    """
    Sensu backend client using the REST API.

    Handles authentication via API key header and constructs namespaced
    endpoint URLs under /api/core/v2/namespaces/{namespace}/.

    Each instance is scoped to one request's API key — instantiated per-request
    via auth.get_sensu_client().
    """

    def __init__(self, url: str, api_key: str, namespace: str = "default", verify_ssl: bool = True):
        """
        Initialize the Sensu REST API client.

        Args:
            url: Base URL of the Sensu backend (e.g., 'http://sensu.example.com:8080')
            api_key: API key for Sensu authentication
            namespace: Sensu namespace to query (default: 'default')
            verify_ssl: Whether to verify SSL certificates
        """
        self.base_url = url.rstrip("/")
        self.namespace = namespace
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Key {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    def _namespace_url(self, path: str) -> str:
        """Build a namespaced API URL."""
        path = path.strip("/")
        return f"{self.base_url}/api/core/v2/namespaces/{self.namespace}/{path}"

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """
        Perform a GET request against the Sensu API.

        Args:
            path: Resource path relative to the namespace root (e.g., 'entities', 'events/server1')
            params: Optional query parameters (e.g., limit, continue)

        Returns:
            Parsed JSON response (list or dict depending on endpoint)

        Raises:
            requests.HTTPError: If the request fails
        """
        url = self._namespace_url(path)
        response = self.session.get(url, params=params, verify=self.verify_ssl)
        response.raise_for_status()
        return response.json()

    def get_page(self, path: str, params: dict[str, Any] | None = None) -> tuple[Any, str | None]:
        """Like get(), but also returns the Sensu-Continue pagination token from the response header."""
        url = self._namespace_url(path)
        response = self.session.get(url, params=params, verify=self.verify_ssl)
        response.raise_for_status()
        continue_token = response.headers.get("Sensu-Continue") or None
        return response.json(), continue_token

    def post(self, path: str, body: dict[str, Any]) -> Any:
        """
        Perform a POST request against the Sensu API.

        Args:
            path: Resource path relative to the namespace root (e.g., 'checks/check_cpu/execute')
            body: JSON request body

        Returns:
            Parsed JSON response, or an empty dict for 202 Accepted with no body.

        Raises:
            requests.HTTPError: If the request fails (including 403 for insufficient permissions)
        """
        url = self._namespace_url(path)
        response = self.session.post(url, json=body, verify=self.verify_ssl)
        response.raise_for_status()
        return response.json() if response.content else {}
