# Sensu MCP Server

A read-only [Model Context Protocol](https://modelcontextprotocol.io/) server for Sensu. It enables you to interact with your Sensu monitoring data directly via LLMs that support MCP.

## Tools

| Tool | Description |
|------|-------------|
| `sensu_get_entities` | Returns the list of entities (agents and proxies) in the configured namespace |
| `sensu_get_entity_events` | Returns all check events for a specific entity |
| `sensu_get_entity_check_event` | Returns the event for a specific entity and check combination |

## Usage

1. Create an API key in Sensu with sufficient permissions to read entities and events.

2. Install dependencies:

    ```bash
    uv sync
    ```

3. Verify the server can run:

    ```bash
    SENSU_URL=http://sensu.example.com:8080/ SENSU_API_KEY=<your-api-key> uv run sensu-mcp
    ```

4. Add the MCP server to your LLM client. See below for examples with Claude.

### Claude Code

#### Stdio Transport (Default)

```bash
claude mcp add --transport stdio sensu \
  --env SENSU_URL=http://sensu.example.com:8080/ \
  --env SENSU_API_KEY=<your-api-key> \
  -- uv --directory /path/to/sensu-mcp-server run sensu-mcp
```

**Important notes:**

- Replace `/path/to/sensu-mcp-server` with the absolute path to this directory
- The `--` separator distinguishes Claude Code flags from the server command
- Use `--scope project` to share configuration via `.mcp.json` in version control
- Use `--scope user` to make it available across all your projects (default is `local`)

After adding, verify with `/mcp` in Claude Code or `claude mcp list` in your terminal.

#### HTTP Transport

Start the server with HTTP transport:

```bash
SENSU_URL=http://sensu.example.com:8080/ \
SENSU_API_KEY=<your-api-key> \
TRANSPORT=http \
uv run sensu-mcp
```

Then add it to Claude Code:

```bash
claude mcp add --transport http sensu http://127.0.0.1:8000/mcp
```

### Claude Desktop

Add the server to your Claude Desktop config file (`~/Library/Application Support/Claude/claude_desktop_config.json` on Mac):

```json
{
    "mcpServers": {
        "sensu": {
            "command": "uv",
            "args": [
                "--directory",
                "/path/to/sensu-mcp-server",
                "run",
                "sensu-mcp"
            ],
            "env": {
                "SENSU_URL": "http://sensu.example.com:8080/",
                "SENSU_API_KEY": "<your-api-key>"
            }
        }
    }
}
```

> On Windows, use full escaped paths, e.g. `C:\\Users\\myuser\\.local\\bin\\uv` and `C:\\Users\\myuser\\sensu-mcp-server`.

### Example queries

```text
> List all entities in Sensu
> Show me all failing checks across all hosts
> What checks are running on server1?
> Is the CPU check passing on db-prod-01?
> Which hosts haven't checked in recently?
> Show me the check history for server1's keepalive
```

## Configuration

Configuration precedence (highest to lowest):

1. **Command-line arguments**
2. **Environment variables**
3. **`.env` file** in the project root
4. **Default values**

### Configuration Reference

| Setting | Type | Default | Required | Description |
|---------|------|---------|----------|-------------|
| `SENSU_URL` | URL | - | Yes | Base URL of your Sensu backend (e.g., `http://sensu.example.com:8080/`) |
| `SENSU_API_KEY` | String | - | Yes | API key for authentication |
| `SENSU_NAMESPACE` | String | `default` | No | Sensu namespace to query |
| `TRANSPORT` | `stdio` \| `http` | `stdio` | No | MCP transport protocol |
| `HOST` | String | `127.0.0.1` | If HTTP | Host address for HTTP server |
| `PORT` | Integer | `8000` | If HTTP | Port for HTTP server |
| `VERIFY_SSL` | Boolean | `true` | No | Whether to verify SSL certificates |
| `LOG_LEVEL` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` \| `CRITICAL` | `INFO` | No | Logging verbosity |

### Example .env file

```env
# Core Sensu Configuration
SENSU_URL=http://sensu.example.com:8080/
SENSU_API_KEY=your-api-key-here
SENSU_NAMESPACE=default

# Transport Configuration (optional, defaults to stdio)
TRANSPORT=stdio

# HTTP Transport Settings (only used if TRANSPORT=http)
# HOST=127.0.0.1
# PORT=8000

# Security (optional, defaults to true)
VERIFY_SSL=true

# Logging (optional, defaults to INFO)
LOG_LEVEL=INFO
```

### CLI Arguments

```bash
uv run sensu-mcp --help

# Common examples:
uv run sensu-mcp --log-level DEBUG --no-verify-ssl        # Development
uv run sensu-mcp --transport http --port 9000              # Custom HTTP port
uv run sensu-mcp --sensu-namespace production              # Different namespace
```

## Docker Usage

Build and run the Sensu MCP server in a container:

```bash
# Build the image
docker build -t sensu-mcp-server:latest .

# Run with HTTP transport
docker run --rm \
  -e SENSU_URL=http://sensu.example.com:8080/ \
  -e SENSU_API_KEY=<your-api-key> \
  -e TRANSPORT=http \
  -e HOST=0.0.0.0 \
  -e PORT=8000 \
  -p 8000:8000 \
  sensu-mcp-server:latest
```

> **Note:** Docker containers require `TRANSPORT=http` since stdio transport doesn't work in containerized environments.

With additional options:

```bash
docker run --rm \
  -e SENSU_URL=http://sensu.example.com:8080/ \
  -e SENSU_API_KEY=<your-api-key> \
  -e SENSU_NAMESPACE=production \
  -e TRANSPORT=http \
  -e HOST=0.0.0.0 \
  -e LOG_LEVEL=DEBUG \
  -e VERIFY_SSL=false \
  -p 8000:8000 \
  sensu-mcp-server:latest
```

The server will be accessible at `http://localhost:8000/mcp`.
