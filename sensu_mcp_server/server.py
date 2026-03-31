import argparse
import logging
import sys
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from sensu_mcp_server.config import Settings, configure_logging
from sensu_mcp_server.filters import SensuFilter
from sensu_mcp_server.sensu_client import SensuRestClient


def parse_cli_args() -> dict[str, Any]:
    """
    Parse command-line arguments for configuration overrides.

    Returns:
        dict of configuration overrides (only includes explicitly set values)
    """
    parser = argparse.ArgumentParser(
        description="Sensu MCP Server - Model Context Protocol server for Sensu",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--sensu-url",
        type=str,
        help="Base URL of the Sensu backend (e.g., http://sensu.example.com:8080/)",
    )
    parser.add_argument(
        "--sensu-api-key",
        type=str,
        help="API key for Sensu authentication",
    )
    parser.add_argument(
        "--sensu-namespace",
        type=str,
        help="Sensu namespace to query (default: default)",
    )
    parser.add_argument(
        "--transport",
        type=str,
        choices=["stdio", "http"],
        help="MCP transport protocol (default: stdio)",
    )
    parser.add_argument("--host", type=str, help="Host address for HTTP server (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, help="Port for HTTP server (default: 8000)")

    ssl_group = parser.add_mutually_exclusive_group()
    ssl_group.add_argument(
        "--verify-ssl",
        action="store_true",
        dest="verify_ssl",
        default=None,
        help="Verify SSL certificates (default)",
    )
    ssl_group.add_argument(
        "--no-verify-ssl",
        action="store_false",
        dest="verify_ssl",
        help="Disable SSL certificate verification (not recommended)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging verbosity level (default: INFO)",
    )

    args: argparse.Namespace = parser.parse_args()

    overlay: dict[str, Any] = {}
    if args.sensu_url is not None:
        overlay["sensu_url"] = args.sensu_url
    if args.sensu_api_key is not None:
        overlay["sensu_api_key"] = args.sensu_api_key
    if args.sensu_namespace is not None:
        overlay["sensu_namespace"] = args.sensu_namespace
    if args.transport is not None:
        overlay["transport"] = args.transport
    if args.host is not None:
        overlay["host"] = args.host
    if args.port is not None:
        overlay["port"] = args.port
    if args.verify_ssl is not None:
        overlay["verify_ssl"] = args.verify_ssl
    if args.log_level is not None:
        overlay["log_level"] = args.log_level

    return overlay


mcp = FastMCP("Sensu")
sensu: SensuRestClient | None = None


@mcp.tool(
    description="""
    Returns the list of Sensu entities (agents and proxies) in the configured namespace.

    An entity is anything that reports check results — typically a server, VM, network device,
    or cloud instance running the Sensu agent.

    Args:
        limit: Maximum number of entities to return per page (default 50, max 1000).
               Sensu uses cursor-based pagination, not offset.
               Start with the default; increase only if you need more results at once.

        continue_token: Pagination cursor returned in the previous response's 'continue' field.
                        Pass this to retrieve the next page of results.
                        Leave empty (or None) to start from the first page.

                        Pagination pattern:
                          page1 = sensu_get_entities(limit=50)
                          # if page1['continue'] is set, there are more results:
                          page2 = sensu_get_entities(limit=50, continue_token=page1['continue'])

        field_selector: Server-side field filter expression (Sensu field selector syntax).
                        Filters on entity metadata fields. Only exact-match comparisons supported.

                        Examples:
                          "entity.name == server1"
                          "entity.entity_class == agent"

        label_selector: Server-side label filter expression (Sensu label selector syntax).
                        Filters entities by their metadata.labels.

                        Examples:
                          "region == us-east-1"
                          "environment in (production, staging)"

    Returns:
        A list of entity objects. Each entity contains:
        - metadata.name: The entity's unique name (hostname / identifier)
        - metadata.namespace: The namespace this entity belongs to
        - metadata.labels: Key-value labels attached to the entity
        - entity_class: "agent" (Sensu agent installed) or "proxy" (synthetic)
        - system.hostname: Reported hostname
        - system.os / system.platform / system.platform_version: OS info
        - system.arch: CPU architecture
        - system.network.interfaces: Network interface list with MAC and IP addresses
        - subscriptions: List of subscriptions this entity is enrolled in
        - last_seen: Unix timestamp of when the entity last reported in
        - sensu_agent_version: Version of the Sensu agent (agent entities only)

        The response also includes a top-level 'continue' key. If non-empty, more pages exist.
        Pass it as continue_token to retrieve the next page.

        ALWAYS CHECK THE 'continue' FIELD BEFORE TELLING THE USER YOU HAVE ALL RESULTS.

    Common queries:
        # List all entities
        sensu_get_entities()

        # Filter to agent-class entities only
        sensu_get_entities(field_selector="entity.entity_class == agent")

        # Find a specific host
        sensu_get_entities(field_selector="entity.name == server1")

        # Filter by label
        sensu_get_entities(label_selector="environment == production")

    NOTE: fieldSelector and labelSelector are evaluated client-side (the server-side
    filtering is a commercial Sensu feature). When either selector is provided, ALL
    pages are fetched automatically and the filter is applied to the combined result,
    so you do not need to paginate manually in that case.
    """
)
def sensu_get_entities(
    limit: Annotated[int, Field(default=50, ge=1, le=1000)] = 50,
    continue_token: str | None = None,
    field_selector: str | None = None,
    label_selector: str | None = None,
) -> list[dict]:
    """Returns the list of entities in the configured Sensu namespace."""
    f = SensuFilter(field_selector=field_selector, label_selector=label_selector)

    if not f.is_empty:
        # Fetch all pages then filter client-side
        all_entities: list[dict] = []
        token: str | None = continue_token
        while True:
            params: dict[str, Any] = {"limit": limit}
            if token:
                params["continue"] = token
            page = sensu.get("entities", params=params)
            entities = page if isinstance(page, list) else page.get("items", [])
            all_entities.extend(entities)
            token = page.get("continue") if isinstance(page, dict) else None
            if not token:
                break
        return f.apply(all_entities)

    # No filter — single page, honour continue_token for manual pagination
    params = {"limit": limit}
    if continue_token:
        params["continue"] = continue_token
    return sensu.get("entities", params=params)


@mcp.tool(
    description="""
    Get a single Sensu entity by exact name.

    Use this instead of sensu_get_entities() when you know the entity name.
    This is a direct API lookup — if the entity does not exist the call returns
    a 404 error, so it also serves as an existence check.

    Args:
        entity_name: The exact name of the entity (hostname / identifier).
                     Example: "ctr-navi4x-aj53-ws10", "db-prod-01"

    Returns:
        A single entity object with the same fields as sensu_get_entities().
        Raises an HTTPError with status 404 if the entity does not exist.
    """
)
def sensu_get_entity(entity_name: str) -> dict:
    """Returns the entity with the given name, or raises 404 if not found."""
    return sensu.get(f"entities/{entity_name}")


@mcp.tool(
    description="""
    Returns all events for a specific Sensu entity.

    An event represents the result of a check execution for an entity. Each entity may have
    multiple events — one per check configured for it (e.g., keepalive, check_cpu, check_disk).

    Use this tool to see the health status of all checks running on a given host.

    Args:
        entity_name: The name of the entity to query events for.
                     This is the entity's unique identifier — typically the hostname.
                     Example: "server1", "sensu-centos", "db-prod-01"

                     To find entity names, use sensu_get_entities() first.

        limit: Maximum number of events to return per page (default 50, max 1000).

        continue_token: Pagination cursor from the previous response's 'continue' field.
                        Leave empty to start from the first page.

    Returns:
        A list of event objects for the specified entity. Each event contains:

        - id: Unique event identifier (UUID)
        - timestamp: Unix timestamp of when the event was processed
        - sequence: Monotonically increasing sequence number for this entity+check pair

        - check: The check result that generated this event:
            - metadata.name: Check name (e.g., "check_cpu", "keepalive")
            - command: The command that was executed
            - status: Exit code — 0=OK, 1=WARNING, 2=CRITICAL, 3=UNKNOWN
            - state: Human-readable state — "passing", "failing", "flapping"
            - output: The check's stdout output
            - issued / executed: Unix timestamps for when the check was scheduled and run
            - interval: How often this check runs (seconds)
            - occurrences: How many consecutive times this result has occurred
            - occurrences_watermark: Peak consecutive occurrences
            - last_ok: Unix timestamp of the last passing result
            - history: Recent status history (last N executions)
            - is_silenced: Whether alerts for this check are currently silenced

        - entity: Snapshot of the entity at the time of the event (same fields as sensu_get_entities)
        - pipelines: Pipelines this event was routed through (e.g., alert handlers)

        ALWAYS CHECK THE 'continue' FIELD BEFORE TELLING THE USER YOU HAVE ALL RESULTS.

    Common queries:
        # Get all check results for a host
        sensu_get_entity_events("server1")

        # See if any checks are failing on a host
        sensu_get_entity_events("db-prod-01")
        # → look for events where check.status != 0

        # Only warning checks (status 1)
        sensu_get_entity_events("db-prod-01", field_selector="event.check.status == 1")

        # Only failing/critical checks (status 2)
        sensu_get_entity_events("db-prod-01", field_selector="event.check.status == 2")

        # Checks that are currently in a failing state
        sensu_get_entity_events("db-prod-01", field_selector='event.check.state == "failing"')

    NOTE: field_selector and label_selector are evaluated client-side. When provided,
    all pages are fetched automatically before filtering.

    Available event field paths:
        event.check.status          (0=OK, 1=WARNING, 2=CRITICAL, 3=UNKNOWN)
        event.check.state           ("passing", "failing", "flapping")
        event.check.name
        event.check.handlers
        event.check.subscriptions
        event.check.is_silenced
        event.check.publish
        event.check.round_robin
        event.check.runtime_assets
        event.entity.name
        event.entity.entity_class
        event.entity.subscriptions
        event.entity.deregister
        event.is_silenced

    Error codes:
        404: Entity does not exist in this namespace
        500: Internal Sensu backend error
    """
)
def sensu_get_entity_events(
    entity_name: str,
    limit: Annotated[int, Field(default=50, ge=1, le=1000)] = 50,
    continue_token: str | None = None,
    field_selector: str | None = None,
    label_selector: str | None = None,
) -> list[dict]:
    """Returns all events for the specified Sensu entity, with optional client-side filtering."""
    f = SensuFilter(field_selector=field_selector, label_selector=label_selector)

    if not f.is_empty:
        all_events: list[dict] = []
        token: str | None = continue_token
        while True:
            params: dict[str, Any] = {"limit": limit}
            if token:
                params["continue"] = token
            page = sensu.get(f"events/{entity_name}", params=params)
            events = page if isinstance(page, list) else page.get("items", [])
            all_events.extend(events)
            token = page.get("continue") if isinstance(page, dict) else None
            if not token:
                break
        return f.apply(all_events)

    params = {"limit": limit}
    if continue_token:
        params["continue"] = continue_token
    return sensu.get(f"events/{entity_name}", params=params)


@mcp.tool(
    description="""
    Returns the event for a specific entity and check combination.

    Use this tool when you want the current status of ONE specific check on ONE specific host.
    This is more targeted than sensu_get_entity_events() and returns a single event object
    rather than a list.

    Args:
        entity_name: The name of the entity (host) to query.
                     Example: "server1", "db-prod-01"

                     To find entity names, use sensu_get_entities() first.

        check_name: The name of the check to query.
                    Example: "check_cpu", "check_disk", "keepalive"

                    To find check names for an entity, use sensu_get_entity_events(entity_name)
                    and look at event.check.metadata.name in the results.

    Returns:
        A single event object (dict) containing:

        - id: Unique event identifier (UUID)
        - timestamp: Unix timestamp of when the event was processed
        - sequence: Monotonically increasing counter for this entity+check pair

        - check: The check result:
            - metadata.name: Check name
            - command: The command that was executed
            - status: Exit code — 0=OK, 1=WARNING, 2=CRITICAL, 3=UNKNOWN
            - state: "passing", "failing", or "flapping"
            - output: The check's stdout/stderr output
            - issued / executed: Timestamps for scheduling and execution
            - duration: How long the check took to run (seconds)
            - interval: Check interval (seconds)
            - occurrences: Consecutive identical results
            - occurrences_watermark: Peak consecutive identical results
            - last_ok: Unix timestamp of last OK result
            - history: Recent per-execution status history
            - is_silenced: Whether this check's alerts are currently silenced
            - processed_by: Which Sensu backend processed this event
            - scheduler: Scheduling backend used ("memory", "etcd", etc.)

        - entity: Entity snapshot at event time
        - pipelines: Pipelines this event was routed through

    Common queries:
        # Is the CPU check passing on server1?
        sensu_get_entity_check_event("server1", "check_cpu")
        # → check check.status (0=OK) and check.output

        # When did the keepalive last succeed?
        sensu_get_entity_check_event("server1", "keepalive")
        # → check check.last_ok (Unix timestamp)

        # How long has a check been failing?
        sensu_get_entity_check_event("db-prod-01", "check_disk")
        # → check check.occurrences (consecutive failures)

    Error codes:
        404: Entity or check does not exist, or no event recorded for this pair
        500: Internal Sensu backend error
    """
)
def sensu_get_entity_check_event(
    entity_name: str,
    check_name: str,
) -> dict:
    """Returns the event for the specified entity and check."""
    return sensu.get(f"events/{entity_name}/{check_name}")


def main() -> None:
    """Main entry point for the MCP server."""
    global sensu

    cli_overlay: dict[str, Any] = parse_cli_args()

    try:
        settings = Settings(**cli_overlay)
    except Exception as e:
        print(f"Configuration error: {e}", file=sys.stderr)  # noqa: T201
        sys.exit(1)

    configure_logging(settings.log_level)
    logger = logging.getLogger(__name__)

    logger.info("Starting Sensu MCP Server")
    logger.info(f"Effective configuration: {settings.get_effective_config_summary()}")

    if not settings.verify_ssl:
        logger.warning(
            "SSL certificate verification is DISABLED. "
            "This is insecure and should only be used for testing."
        )

    try:
        sensu = SensuRestClient(
            url=str(settings.sensu_url),
            api_key=settings.sensu_api_key.get_secret_value(),
            namespace=settings.sensu_namespace,
            verify_ssl=settings.verify_ssl,
        )
        logger.debug("Sensu client initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize Sensu client: {e}")
        sys.exit(1)

    try:
        if settings.transport == "stdio":
            logger.info("Starting stdio transport")
            mcp.run(transport="stdio")
        elif settings.transport == "http":
            logger.info(f"Starting HTTP transport on {settings.host}:{settings.port}")
            mcp.run(transport="http", host=settings.host, port=settings.port)
    except Exception as e:
        logger.error(f"Failed to start MCP server: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
