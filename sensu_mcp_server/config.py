"""Configuration management for Sensu MCP Server."""

import logging
import logging.config
from typing import Any, Literal

from pydantic import AnyUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Centralized configuration for Sensu MCP Server.

    Configuration precedence: CLI > Environment > .env file > Defaults

    Environment variables should match field names (e.g., SENSU_URL, SENSU_API_KEY).
    """

    # ===== Core Sensu Settings =====
    sensu_url: AnyUrl | None = None
    """Base URL of the Sensu backend. Optional when X-Sensu-URL header is supplied per-request."""

    sensu_api_key: SecretStr | None = None
    """
    Fallback API key for Sensu authentication (treated as secret).

    When the server runs with Bearer auth enabled, each MCP client supplies
    its own Sensu API key as the Bearer token, so this field is not required.
    Set it only if you are running without Bearer auth (e.g. stdio transport
    where there is no HTTP Authorization header).
    """

    sensu_namespace: str = "default"
    """Sensu namespace to query (default: 'default')"""

    # ===== Transport Settings =====
    transport: Literal["stdio", "http"] = "stdio"
    """MCP transport protocol to use (stdio for Claude Desktop, http for web clients)"""

    host: str = "127.0.0.1"
    """Host address to bind HTTP server (only used when transport='http')"""

    port: int = 8000
    """Port to bind HTTP server (only used when transport='http')"""

    # ===== Security Settings =====
    verify_ssl: bool = True
    """Whether to verify SSL certificates when connecting to Sensu"""

    # ===== Observability Settings =====
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    """Logging verbosity level"""

    # ===== Pydantic Configuration =====
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",  # No prefix, use field names directly
        extra="ignore",  # Ignore unknown environment variables
        case_sensitive=False,  # Environment variables are case-insensitive
    )

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        """Ensure port is in valid range."""
        if not (0 < v < 65536):
            raise ValueError(f"Port must be between 1 and 65535, got {v}")
        return v

    @field_validator("sensu_url")
    @classmethod
    def validate_sensu_url(cls, v: AnyUrl | None) -> AnyUrl | None:
        """Ensure Sensu URL has a scheme and host when provided."""
        if v is None:
            return v
        if not v.scheme or not v.host:
            raise ValueError(
                "SENSU_URL must include scheme and host (e.g., http://sensu.example.com:8080/)"
            )
        return v

    def get_effective_config_summary(self) -> dict:
        """Return a non-secret summary of effective configuration for logging."""
        return {
            "sensu_url": str(self.sensu_url),
            "sensu_api_key": "***REDACTED***",
            "sensu_namespace": self.sensu_namespace,
            "transport": self.transport,
            "host": self.host if self.transport == "http" else "N/A",
            "port": self.port if self.transport == "http" else "N/A",
            "verify_ssl": self.verify_ssl,
            "log_level": self.log_level,
        }


def configure_logging(
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
) -> None:
    """
    Configure structured logging using dictConfig.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    config: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "console": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "console",
                "stream": "ext://sys.stderr",
            },
        },
        "loggers": {
            "urllib3": {
                "level": "WARNING" if log_level != "DEBUG" else "DEBUG",
            },
            "httpx": {
                "level": "WARNING" if log_level != "DEBUG" else "DEBUG",
            },
            "requests": {
                "level": "WARNING" if log_level != "DEBUG" else "DEBUG",
            },
        },
        "root": {
            "level": log_level,
            "handlers": ["console"],
        },
    }

    logging.config.dictConfig(config)


# Module-level singleton — imported by auth.py and server.py.
# Requires SENSU_URL to be set in the environment (or a .env file).
# server.main() may call settings._apply_cli_overrides() to layer in CLI args.
settings = Settings()
