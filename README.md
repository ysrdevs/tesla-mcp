# tesla-mcp

A comprehensive MCP server for the [Tesla Fleet API](https://developer.tesla.com/docs/fleet-api). Control and monitor any Tesla vehicle (Model S/3/X/Y, Cybertruck, Semi) via Claude, Claude Code, Cursor, Poke, or any MCP-compatible client.

**96 tools** covering every Fleet API endpoint.

## What You Can Do

**Vehicle Control**: Lock/unlock, open frunk/trunk, remote start, honk, flash lights, boombox, vent/close windows, trigger HomeLink (garage door)

**Climate**: Start/stop HVAC, set temps, seat heaters/coolers, steering wheel heater, defrost, dog mode, camp mode, bioweapon defense mode, cabin overheat protection

**Charging**: Start/stop charging, set charge limit %, set amps, open/close charge port, charge schedules, max range mode

**Navigation**: Send GPS coordinates, addresses, or Supercharger routes to the vehicle

**Media**: Play/pause, next/prev track, favorites, volume control

**Security**: Sentry mode, valet mode with PIN, speed limit mode, PIN to drive, guest mode

**Data**: Live vehicle data, battery state, nearby chargers, firmware release notes, alerts, service status, driver list, warranty info, subscriptions, upgrades

**Energy (Powerwall/Solar)**: Live status, backup reserve, storm watch, operation mode, energy history

**Fleet Management**: Share invites, Fleet Telemetry config, fleet status

**Account**: OAuth setup, token management, partner registration, virtual key pairing

## Quick Start

### Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Tesla account with a vehicle
- Developer app registered at [developer.tesla.com](https://developer.tesla.com)

### Install

```bash
git clone https://github.com/ysrdevs/tesla-mcp.git
cd tesla-mcp
uv sync
```

### Configure

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
TESLA_CLIENT_ID=your_client_id
TESLA_CLIENT_SECRET=your_client_secret
TESLA_VIN=your_vin
TESLA_REGION=na
```

### Run

```bash
# HTTP server (default) -- starts on port 8752
uv run python tesla_mcp.py

# stdio mode (for Claude Desktop local)
TESLA_MCP_TRANSPORT=stdio uv run python tesla_mcp.py
```

On first run, an API key is auto-generated and saved to `.api_key` (chmod 600). You can also set `MCP_API_KEY` in your environment to use your own.

## Authentication

The MCP server uses API key auth via FastMCP's `StaticTokenVerifier`. Clients must pass the API key as a Bearer token.

On first run, a key is auto-generated at `.api_key` in the project directory. To use your own, set `MCP_API_KEY` in `.env` or environment.

### Connecting Clients

**Poke / any MCP client:**
- **Server URL**: `https://your-domain.com/path/mcp`
- **API Key**: contents of `.api_key`

**Claude Desktop (remote HTTP):**

```json
{
  "mcpServers": {
    "tesla": {
      "type": "streamable-http",
      "url": "https://your-domain.com/path/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_API_KEY"
      }
    }
  }
}
```

**Claude Desktop (local stdio, no auth needed):**

```json
{
  "mcpServers": {
    "tesla": {
      "command": "/path/to/uv",
      "args": ["run", "--directory", "/path/to/tesla-mcp", "python", "tesla_mcp.py"],
      "env": {
        "TESLA_MCP_TRANSPORT": "stdio",
        "TESLA_CLIENT_ID": "your_client_id",
        "TESLA_CLIENT_SECRET": "your_client_secret",
        "TESLA_VIN": "your_vin",
        "TESLA_REGION": "na"
      }
    }
  }
}
```

**Claude Code:**

```bash
claude mcp add tesla -- env TESLA_MCP_TRANSPORT=stdio uv run --directory /path/to/tesla-mcp python tesla_mcp.py
```

## Full Setup Guide

### 1. Register a Developer App

Go to [developer.tesla.com](https://developer.tesla.com), create an account, and submit an application request. Select the scopes your app needs. Once approved, you'll get a Client ID and Client Secret.

Set your **Allowed Origin** to your domain (e.g. `https://your-domain.com`) and **Allowed Redirect URI** to a callback path on your domain (e.g. `https://your-domain.com/morpheus/callback`).

### 2. Generate EC Key Pair

Tesla requires an EC key pair for command signing and Fleet Telemetry.

```bash
openssl ecparam -name prime256v1 -genkey -noout -out private-key.pem
openssl ec -in private-key.pem -pubout -out public-key.pem
chmod 600 private-key.pem
```

**Keep `private-key.pem` secret.** Never commit it, never host it publicly.

### 3. Host Your Public Key

The public key must be accessible at:

```
https://your-domain.com/.well-known/appspecific/com.tesla.3p.public-key.pem
```

Example nginx config:

```nginx
location /.well-known/appspecific/com.tesla.3p.public-key.pem {
    alias /path/to/tesla-mcp/public-key.pem;
}
```

Tesla re-validates this periodically -- it must remain accessible.

### 4. Register with Tesla

Use the MCP tool or curl to register your domain:

```bash
# Using MCP tools interactively:
# 1. tesla_register_partner(domain="your-domain.com")
# 2. tesla_oauth_url() -> visit URL, log in, copy code from redirect
# 3. tesla_oauth_exchange(code="the_code")
```

Or via curl:

```bash
# Get partner token
curl -s -X POST https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token \
  -d "grant_type=client_credentials&client_id=$CLIENT_ID&client_secret=$CLIENT_SECRET" \
  -d "scope=openid vehicle_device_data vehicle_cmds vehicle_charging_cmds" \
  -d "audience=https://fleet-api.prd.na.vn.cloud.tesla.com"

# Register domain
curl -X POST https://fleet-api.prd.na.vn.cloud.tesla.com/api/1/partner_accounts \
  -H "Authorization: Bearer $PARTNER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"domain":"your-domain.com"}'
```

### 5. Pair Virtual Key to Vehicle

Open this URL **on your phone** with the Tesla app installed:

```
https://tesla.com/_ak/your-domain.com?vin=YOUR_VIN
```

The Tesla app will prompt you to add the virtual key. Accept it. One time only.

Without the virtual key paired, read-only endpoints work but write commands (lock, unlock, climate, etc.) will be rejected.

Verify the key is paired:

```
tesla_fleet_status
```

### 6. Vehicle Command Proxy (Required for Commands)

Post-2021 vehicles require commands to be cryptographically signed. Run Tesla's [Vehicle Command HTTP Proxy](https://github.com/teslamotors/vehicle-command):

```bash
git clone https://github.com/teslamotors/vehicle-command.git
cd vehicle-command
go build ./cmd/tesla-http-proxy
./tesla-http-proxy -key-file /path/to/private-key.pem -port 4443
```

> **Note**: Read-only endpoints work without the proxy. The proxy is only needed for write commands on vehicles that require the Vehicle Command Protocol.

## Production Deployment

### systemd Service

```ini
[Unit]
Description=Tesla Fleet API MCP Server
After=network.target

[Service]
User=forge
Group=forge
WorkingDirectory=/home/forge/tesla-mcp
EnvironmentFile=/home/forge/tesla-mcp/.env
ExecStart=/home/forge/.local/bin/uv run python tesla_mcp.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo cp tesla-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable tesla-mcp
sudo systemctl start tesla-mcp
```

### nginx Reverse Proxy

```nginx
location /morpheus/ {
    proxy_pass http://127.0.0.1:8752/;
    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade    $http_upgrade;
    proxy_set_header Connection "upgrade";
}
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TESLA_CLIENT_ID` | | Tesla developer app Client ID |
| `TESLA_CLIENT_SECRET` | | Tesla developer app Client Secret |
| `TESLA_VIN` | | Default vehicle VIN |
| `TESLA_REGION` | `na` | API region: `na`, `eu`, or `cn` |
| `TESLA_REFRESH_TOKEN` | | OAuth refresh token (set via OAuth flow) |
| `TESLA_TOKEN_FILE` | `~/.tesla_tokens.json` | Path to token storage |
| `TESLA_MCP_TRANSPORT` | `streamable-http` | Transport: `streamable-http` or `stdio` |
| `TESLA_MCP_HOST` | `0.0.0.0` | Bind address for HTTP transport |
| `TESLA_MCP_PORT` | `8752` | Port for HTTP transport |
| `MCP_API_KEY` | auto-generated | API key for client authentication |

## API Regions

| Region | Base URL |
|--------|----------|
| North America / Asia-Pacific | `https://fleet-api.prd.na.vn.cloud.tesla.com` |
| Europe / Middle East / Africa | `https://fleet-api.prd.eu.vn.cloud.tesla.com` |
| China | `https://fleet-api.prd.cn.vn.cloud.tesla.cn` |

## Pricing

Tesla provides a **$10/month free credit** per developer account. For personal use this covers roughly 100 commands + 2 wakes per day for 2 vehicles.

| Category | Rate |
|----------|------|
| Commands | $1 / 1,000 requests |
| Data | $1 / 500 requests |
| Wakes | $1 / 50 requests |
| Streaming Signals | $1 / 150,000 signals |

## Rate Limits

Per device, per account:

| Type | Limit |
|------|-------|
| Data | 60 req/min |
| Wakes | 3 req/min |
| Commands | 30 req/min |

## Security

- **API key auth**: MCP server requires a Bearer token. Auto-generated on first run (saved to `.api_key` with chmod 600) or set via `MCP_API_KEY` env var.
- **Input validation**: VINs validated against ISO 3779 format. IDs, domains, and PINs sanitized. Numeric inputs range-checked. All URL path segments validated to prevent traversal.
- **No secrets in output**: Partner tokens used internally only, never returned to clients. Error responses sanitized.
- **Token storage**: OAuth tokens saved to `~/.tesla_tokens.json` with chmod 600.
- **No default PINs**: PIN parameters are required, no defaults in function signatures.
- **Destructive action warnings**: Tools like `unlock`, `remote_start`, and `honk` prompt AI assistants to confirm with the user.

### Files to Never Commit

- `.env` -- credentials
- `private-key.pem` -- Tesla EC private key
- `.api_key` -- MCP auth key
- `.tesla_tokens.json` -- OAuth tokens

These are all in `.gitignore`.

## Important Notes

- **Vehicle must be awake** before sending commands. Use `tesla_wait_for_wake`.
- **Virtual key must be paired** before commands work. Use `tesla_fleet_status` to check.
- **Don't poll `vehicle_data`** regularly. Use Fleet Telemetry instead. Each call wakes the car and costs money.
- **Some commands can't be undone**: `honk_horn`, `media_next_track`, etc.
- **Refresh tokens expire after 3 months**. The server auto-refreshes access tokens.

## Contributing

PRs welcome. Validate all inputs using `_vin()` / `_validate_id()` / `_validate_domain()` for path segments, `_validate_pin()` for PINs, and `_clamp()` for numeric ranges.

## License

MIT
