# tesla-mcp

A comprehensive MCP server for the [Tesla Fleet API](https://developer.tesla.com/docs/fleet-api). Control and monitor any Tesla vehicle (Model S/3/X/Y, Cybertruck, Semi) via Claude, Claude Code, Cursor, LLM Apps, or any MCP-compatible client.

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

## Two Auth Modes

This server ships with two entry points for different client types:

| File | Auth | Port | Best For |
|------|------|------|----------|
| `tesla_mcp.py` | OAuth 2.1 (client ID + secret) | 8752 | Claude.ai (web + mobile), Claude Desktop, Claude Code |
| `tesla_mcp_apikey.py` | API key (Bearer token) | 8753 | LLM Apps, simple MCP clients, direct API access |

Both files share the same 96 tools and Tesla credentials. Run one or both depending on your needs.

## Quick Start

### Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- Tesla account with a vehicle
- Developer app at [developer.tesla.com](https://developer.tesla.com)

### Install

```bash
git clone https://github.com/YOUR_USERNAME/tesla-mcp.git
cd tesla-mcp
uv sync
```

### Configure

```bash
cp .env.example .env
# Edit .env with your Tesla credentials
```

### Run

```bash
# OAuth mode (Claude.ai)
uv run python tesla_mcp.py

# API key mode (LLM Apps)
uv run python tesla_mcp_apikey.py

# stdio mode (Claude Desktop local, no auth needed)
TESLA_MCP_TRANSPORT=stdio uv run python tesla_mcp.py
```

## Connecting Clients

### Claude.ai (web + mobile) -- OAuth mode

1. Run `tesla_mcp.py` on your server
2. Go to Claude.ai → Settings → Connectors → Add custom connector
3. URL: `https://your-domain.com/tesla/mcp`
4. Click Advanced Settings
5. Enter your `MCP_CLIENT_ID` and `MCP_CLIENT_SECRET`
6. Click Add

Once connected on web, it syncs to Claude mobile automatically.

### LLM Apps -- API key mode

1. Run `tesla_mcp_apikey.py` on your server
2. In LLM Apps → Settings → Connections → Add Integration → Create
3. Name: `Tesla`
4. URL: `https://your-domain.com/tesla-api/mcp`
5. API Key: contents of `.api_key` file on your server

### Claude Desktop (local stdio, no auth)

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

### Claude Code

```bash
claude mcp add tesla -- env TESLA_MCP_TRANSPORT=stdio uv run --directory /path/to/tesla-mcp python tesla_mcp.py
```

## Full Setup Guide

### 1. Register a Developer App

Go to [developer.tesla.com](https://developer.tesla.com), create an account, and submit an application request. Once approved, you'll get a Client ID and Client Secret.

Set your **Allowed Origin** to your domain and **Allowed Redirect URI** to a callback path on your domain.

### 2. Generate EC Key Pair

```bash
openssl ecparam -name prime256v1 -genkey -noout -out private-key.pem
openssl ec -in private-key.pem -pubout -out public-key.pem
chmod 600 private-key.pem
```

**Never commit or host `private-key.pem` publicly.**

### 3. Host Your Public Key

Must be accessible at:

```
https://your-domain.com/.well-known/appspecific/com.tesla.3p.public-key.pem
```

nginx example:

```nginx
location /.well-known/appspecific/com.tesla.3p.public-key.pem {
    alias /path/to/tesla-mcp/public-key.pem;
}
```

### 4. Register with Tesla

```bash
# Using MCP tools:
# 1. tesla_register_partner(domain="your-domain.com")
# 2. tesla_oauth_url() -> visit URL, log in, copy code
# 3. tesla_oauth_exchange(code="the_code")
```

### 5. Pair Virtual Key to Vehicle

Open on your phone with the Tesla app:

```
https://tesla.com/_ak/your-domain.com?vin=YOUR_VIN
```

Accept the prompt. One time only. Verify with `tesla_fleet_status`.

### 6. Vehicle Command Proxy

Post-2021 vehicles require signed commands. Build and run Tesla's [Vehicle Command Proxy](https://github.com/teslamotors/vehicle-command):

```bash
git clone https://github.com/teslamotors/vehicle-command.git
cd vehicle-command
go build ./cmd/tesla-http-proxy

# Generate TLS cert
mkdir -p config
openssl req -x509 -nodes -newkey ec \
    -pkeyopt ec_paramgen_curve:secp384r1 \
    -pkeyopt ec_param_enc:named_curve \
    -subj '/CN=localhost' \
    -keyout config/tls-key.pem -out config/tls-cert.pem -sha256 -days 3650 \
    -addext "extendedKeyUsage = serverAuth" \
    -addext "keyUsage = digitalSignature, keyCertSign, keyAgreement"

# Run
./tesla-http-proxy \
    -tls-key config/tls-key.pem \
    -cert config/tls-cert.pem \
    -key-file /path/to/private-key.pem \
    -port 4443
```

Add to `.env`:

```env
TESLA_PROXY_URL=https://localhost:4443
TESLA_PROXY_VERIFY_SSL=false
```

Read-only endpoints work without the proxy. The proxy is only needed for write commands.

## Production Deployment

### systemd Services

**OAuth server (Claude.ai):**

```ini
[Unit]
Description=Tesla MCP Server (OAuth)
After=network.target

[Service]
User=your-user
Group=your-user
WorkingDirectory=/path/to/tesla-mcp
EnvironmentFile=/path/to/tesla-mcp/.env
ExecStart=/path/to/uv run python tesla_mcp.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**API key server (LLM Apps):**

```ini
[Unit]
Description=Tesla MCP Server (API Key)
After=network.target

[Service]
User=your-user
Group=your-user
WorkingDirectory=/path/to/tesla-mcp
EnvironmentFile=/path/to/tesla-mcp/.env
ExecStart=/path/to/uv run python tesla_mcp_apikey.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**Vehicle Command Proxy:**

```ini
[Unit]
Description=Tesla Vehicle Command Proxy
After=network.target

[Service]
User=your-user
Group=your-user
WorkingDirectory=/path/to/vehicle-command
ExecStart=/path/to/vehicle-command/tesla-http-proxy \
    -tls-key /path/to/vehicle-command/config/tls-key.pem \
    -cert /path/to/vehicle-command/config/tls-cert.pem \
    -key-file /path/to/tesla-mcp/private-key.pem \
    -port 4443
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### nginx

```nginx
# OAuth endpoints for Claude.ai
location /authorize {
    proxy_pass http://127.0.0.1:8752/authorize;
    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location /token {
    proxy_pass http://127.0.0.1:8752/token;
    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location /register {
    proxy_pass http://127.0.0.1:8752/register;
    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location /.well-known/oauth-authorization-server {
    proxy_pass http://127.0.0.1:8752/.well-known/oauth-authorization-server;
    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location /.well-known/oauth-protected-resource {
    proxy_pass http://127.0.0.1:8752/.well-known/oauth-protected-resource;
    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

# Tesla public key
location /.well-known/appspecific/com.tesla.3p.public-key.pem {
    alias /path/to/tesla-mcp/public-key.pem;
}

# OAuth MCP server (Claude.ai)
location /tesla/ {
    proxy_pass http://127.0.0.1:8752/;
    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade    $http_upgrade;
    proxy_set_header Connection "upgrade";
}

# API key MCP server (LLM Apps)
location /tesla-api/ {
    proxy_pass http://127.0.0.1:8753/;
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
| `TESLA_REGION` | `na` | API region: `na`, `eu`, `cn` |
| `TESLA_REFRESH_TOKEN` | | OAuth refresh token |
| `TESLA_TOKEN_FILE` | `~/.tesla_tokens.json` | Token storage path |
| `TESLA_PROXY_URL` | | Vehicle Command Proxy URL |
| `TESLA_PROXY_VERIFY_SSL` | `false` | Verify proxy TLS cert |
| `TESLA_MCP_TRANSPORT` | `streamable-http` | `streamable-http` or `stdio` |
| `TESLA_MCP_HOST` | `0.0.0.0` | Bind address |
| `TESLA_MCP_PORT` | `8752` | OAuth server port |
| `MCP_BASE_URL` | | Public URL for OAuth discovery |
| `MCP_CLIENT_ID` | | OAuth client ID for MCP auth |
| `MCP_CLIENT_SECRET` | | OAuth client secret for MCP auth |
| `MCP_API_KEY` | auto-generated | API key for API key mode |
| `TESLA_MCP_PORT_APIKEY` | `8753` | API key server port |

## API Regions

| Region | Base URL |
|--------|----------|
| North America / Asia-Pacific | `https://fleet-api.prd.na.vn.cloud.tesla.com` |
| Europe / Middle East / Africa | `https://fleet-api.prd.eu.vn.cloud.tesla.com` |
| China | `https://fleet-api.prd.cn.vn.cloud.tesla.cn` |

## Pricing

Tesla provides a **$10/month free credit** per developer account.

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

**OAuth mode (`tesla_mcp.py`):**
- Static client ID + secret required. Only clients with matching credentials can authorize.
- Credentials checked at authorize, token exchange, and token refresh (defense in depth).
- DCR enabled for protocol compliance but unauthorized clients are rejected at authorization.
- Tokens persist to `.oauth-state/` (survives restarts).

**API key mode (`tesla_mcp_apikey.py`):**
- Bearer token auth via FastMCP's `StaticTokenVerifier`.
- Auto-generated key saved to `.api_key` (chmod 600) or set via `MCP_API_KEY`.

**Both modes:**
- VINs validated against ISO 3779 format.
- All URL path segments sanitized to prevent traversal.
- PINs validated as exactly 4 digits, no defaults.
- Numeric inputs range-checked.
- No secrets in tool output.
- Tesla OAuth tokens stored at `~/.tesla_tokens.json` (chmod 600).
- Destructive actions (unlock, remote_start, honk) warn AI assistants to confirm with user.

### Files to Never Commit

All in `.gitignore`:
- `.env`
- `*.pem` / `private-key.*`
- `.api_key`
- `.tesla_tokens.json`
- `.oauth-state/`

## Important Notes

- **Vehicle must be awake** before commands. Use `tesla_wait_for_wake`.
- **Virtual key must be paired** before commands work. Use `tesla_fleet_status` to check.
- **Don't poll `vehicle_data`** regularly. Use Fleet Telemetry. Each call wakes the car and costs money.
- **Some commands can't be undone**: `honk_horn`, `media_next_track`, etc.
- **Refresh tokens expire after 3 months**. The server auto-refreshes access tokens.
- **Vehicle Command Proxy** required for write commands on post-2021 vehicles.

## Contributing

PRs welcome. Validate all inputs with `_vin()`, `_validate_id()`, `_validate_domain()`, `_validate_pin()`, `_clamp()`.

## License

MIT
