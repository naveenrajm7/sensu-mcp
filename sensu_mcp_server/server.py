import argparse
import logging
import sys
from typing import Any

from fastmcp import FastMCP

import sensu_mcp_server.config as _config_module
from sensu_mcp_server.auth import SensuBearerAuthProvider, get_sensu_client
from sensu_mcp_server.config import Settings, configure_logging, settings
from sensu_mcp_server.filters import SensuFilter


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


mcp = FastMCP("Sensu", auth=SensuBearerAuthProvider())


@mcp.tool(
    description="""
    Returns the list of Sensu entities (agents and proxies) in the configured namespace.

    An entity is anything that reports check results — typically a server, VM, network device,
    or cloud instance running the Sensu agent.

    Args:
        field_selector: Filter expression using Sensu field selector syntax.
                        Filters on entity metadata fields. Only exact-match comparisons supported.

                        Examples:
                          "entity.name == server1"
                          "entity.entity_class == agent"

        label_selector: Filter expression using Sensu label selector syntax.
                        Filters entities by their metadata.labels.

                        Examples:
                          "region == us-east-1"
                          "environment in (production, staging)"

    Returns:
        A complete list of all matching entity objects. Each entity contains:
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

    Common queries:
        # List all entities
        sensu_get_entities()

        # Filter to agent-class entities only
        sensu_get_entities(field_selector="entity.entity_class == agent")

        # Find a specific host
        sensu_get_entities(field_selector="entity.name == server1")

        # Filter by label
        sensu_get_entities(label_selector="environment == production")

    NOTE: Filtering is evaluated client-side (server-side filtering is a commercial Sensu
    feature). All pages are always fetched and the filter applied to the combined result.
    """
)
def sensu_get_entities(
    field_selector: str | None = None,
    label_selector: str | None = None,
) -> list[dict]:
    """Returns all entities in the configured Sensu namespace."""
    sensu = get_sensu_client()
    f = SensuFilter(field_selector=field_selector, label_selector=label_selector)
    all_entities: list[dict] = []
    token: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 1000}
        if token:
            params["continue"] = token
        entities, token = sensu.get_page("entities", params=params)
        all_entities.extend(entities)
        if not token:
            break
    return f.apply(all_entities)


@mcp.tool(
    description="""
    Get a single Sensu entity by exact name.

    Use this instead of sensu_get_entities() when you know the entity name.
    This is a direct API lookup — if the entity does not exist the call returns
    a 404 error, so it also serves as an existence check.

    Args:
        entity_name: The exact name of the entity (hostname / identifier).
                     Example: "web-01", "db-prod-01"

    Returns:
        A single entity object with the same fields as sensu_get_entities().
        Raises an HTTPError with status 404 if the entity does not exist.
    """
)
def sensu_get_entity(entity_name: str) -> dict:
    """Returns the entity with the given name, or raises 404 if not found."""
    return get_sensu_client().get(f"entities/{entity_name}")


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

        field_selector: Optional client-side filter expression.
                        Example: 'event.check.state == "failing"'

        label_selector: Optional client-side label filter expression.

    Returns:
        A complete list of all event objects for the entity. Each event contains:

        - check.metadata.name: Check name (e.g., "check_cpu", "keepalive")
        - check.status: Exit code — 0=OK, 1=WARNING, 2=CRITICAL, 3=UNKNOWN
        - check.state: "passing", "failing", or "flapping"
        - check.output: The check's stdout output
        - check.last_ok: Unix timestamp of the last passing result
        - check.occurrences: How many consecutive times this result has occurred
        - entity: Snapshot of the entity at the time of the event
        - timestamp: When the event was processed

    Available field paths for field_selector:
        event.check.status, event.check.state, event.check.name,
        event.check.is_silenced, event.entity.name, event.is_silenced

    Error codes:
        404: Entity does not exist in this namespace
        500: Internal Sensu backend error
    """
)
def sensu_get_entity_events(
    entity_name: str,
    field_selector: str | None = None,
    label_selector: str | None = None,
) -> list[dict]:
    """Returns all events for the specified Sensu entity."""
    sensu = get_sensu_client()
    f = SensuFilter(field_selector=field_selector, label_selector=label_selector)
    all_events: list[dict] = []
    token: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 1000}
        if token:
            params["continue"] = token
        events, token = sensu.get_page(f"events/{entity_name}", params=params)
        all_events.extend(events)
        if not token:
            break
    return f.apply(all_events)


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
    return get_sensu_client().get(f"events/{entity_name}/{check_name}")


@mcp.tool(
    description="""
    List events across all entities, with mandatory filtering to avoid flooding.

    At least one of entity_name, check_name, check_status, or check_state MUST be
    provided. Without a filter the full event list can be enormous, so this tool
    refuses to run unfiltered.

    Args:
        entity_name: Filter to events for a specific entity (exact match).
                     Example: "server1"

        check_name: Filter to events for a specific check (exact match).
                    Example: "check_cpu"

        check_status: Filter by check exit code.
                      0 = OK, 1 = WARNING, 2 = CRITICAL, 3 = UNKNOWN

        check_state: Filter by check state string.
                     One of: "passing", "failing", "flapping"

    Returns:
        A filtered list of projected event objects. Each object contains:
        - entity: Entity name
        - check: Check name
        - status: Exit code (0=OK, 1=WARNING, 2=CRITICAL, 3=UNKNOWN)
        - state: "passing", "failing", or "flapping"
        - total_state_change: Percentage of state changes (0-100)
        - last_ok: Unix timestamp of last OK result
        - occurrences: Consecutive identical results
        - max_occurrences: Peak consecutive occurrences (occurrences_watermark)
        - issued: Unix timestamp when the check was scheduled
        - executed: Unix timestamp when the check ran
        - duration_ms: How long the check took in milliseconds
        - is_silenced: Whether alerts are silenced
        - output: Check output message

    Error codes:
        400: No filters provided (safety guard)
        500: Internal Sensu backend error
    """
)
def sensu_get_events(
    entity_name: str | None = None,
    check_name: str | None = None,
    check_status: int | None = None,
    check_state: str | None = None,
) -> list[dict]:
    """List events with at least one required filter."""
    if entity_name is None and check_name is None and check_status is None and check_state is None:
        raise ValueError(
            "At least one filter (entity_name, check_name, check_status, or check_state) is required."
        )

    clauses: list[str] = []
    if entity_name is not None:
        clauses.append(f'event.entity.name == "{entity_name}"')
    if check_name is not None:
        clauses.append(f'event.check.name == "{check_name}"')
    if check_status is not None:
        clauses.append(f"event.check.status == {check_status}")
    if check_state is not None:
        clauses.append(f'event.check.state == "{check_state}"')

    f = SensuFilter(field_selector=" && ".join(clauses))

    sensu = get_sensu_client()
    all_events: list[dict] = []
    token: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 1000}
        if token:
            params["continue"] = token
        events, token = sensu.get_page("events", params=params)
        all_events.extend(f.apply(events))
        if not token:
            break

    def _project(event: dict) -> dict:
        check = event.get("check", {})
        entity = event.get("entity", {})
        return {
            "entity": entity.get("metadata", {}).get("name"),
            "check": check.get("metadata", {}).get("name"),
            "status": check.get("status"),
            "state": check.get("state"),
            "total_state_change": check.get("total_state_change"),
            "last_ok": check.get("last_ok"),
            "occurrences": check.get("occurrences"),
            "max_occurrences": check.get("occurrences_watermark"),
            "issued": check.get("issued"),
            "executed": check.get("executed"),
            "duration_ms": round(check.get("duration", 0) * 1000),
            "is_silenced": check.get("is_silenced"),
            "output": check.get("output", "").strip(),
        }

    return [_project(e) for e in all_events]


@mcp.tool(
    description="""
    Execute a Sensu check on demand for a specific entity.

    This is a **write** operation — it triggers an immediate check execution
    outside the normal schedule.  Use it when self-healing has not triggered
    or when you need a fresh result right now.

    Requires a Sensu API key with sufficient permissions (typically an admin or
    operator key).  Read-only keys will receive a 403 from Sensu.

    Args:
        entity_names: One or more entity names (hosts) to run the check on.
                      Each name is mapped to an entity:<name> subscription.
                      Example: ["server1"] or ["db-prod-01", "db-prod-02"]

        check_name: Name of the check to execute.
                    Example: "check_cpu", "check_disk", "remediate_service"

                    The check must already be defined in Sensu and the entity
                    must be subscribed to its subscription.

    Returns:
        A dict with an "issued" timestamp (Unix epoch) confirming the check
        was queued for execution.  The check result will appear via
        sensu_get_entity_check_event() once the agent reports back.

    Error codes:
        403: API key lacks permission to execute checks
        404: Entity or check does not exist in this namespace
        500: Internal Sensu backend error
    """
)
def sensu_execute_check(entity_names: list[str], check_name: str) -> dict:
    """Trigger an on-demand check execution for one or more entities."""
    subscriptions = [f"entity:{name}" for name in entity_names]
    return get_sensu_client().post(
        f"checks/{check_name}/execute",
        body={"check": check_name, "subscriptions": subscriptions},
    )


def main() -> None:
    """Main entry point for the MCP server."""
    cli_overlay: dict[str, Any] = parse_cli_args()

    # Apply CLI overrides on top of env-var-loaded settings.
    # auth.py and tools import the module-level settings singleton, so we
    # replace it in-place so they pick up any CLI-supplied values.
    if cli_overlay:
        try:
            merged = settings.model_dump()
            merged.update(cli_overlay)
            _config_module.settings = Settings(**merged)
        except Exception as e:
            print(f"Configuration error: {e}", file=sys.stderr)  # noqa: T201
            sys.exit(1)

    effective = _config_module.settings
    configure_logging(effective.log_level)
    logger = logging.getLogger(__name__)

    logger.info("Starting Sensu MCP Server")
    logger.info(f"Effective configuration: {effective.get_effective_config_summary()}")

    if not effective.verify_ssl:
        logger.warning(
            "SSL certificate verification is DISABLED. "
            "This is insecure and should only be used for testing."
        )

    try:
        if effective.transport == "stdio":
            logger.info("Starting stdio transport")
            mcp.run(transport="stdio")
        elif effective.transport == "http":
            logger.info(f"Starting HTTP transport on {effective.host}:{effective.port}")
            mcp.run(transport="http", host=effective.host, port=effective.port)
    except Exception as e:
        logger.error(f"Failed to start MCP server: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
