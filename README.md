# tesla-fleet-mcp

A comprehensive MCP server for the [Tesla Fleet API](https://developer.tesla.com/docs/fleet-api). Control and monitor any Tesla vehicle (Model S/3/X/Y, Cybertruck, Semi) via Claude, Claude Code, Cursor, or any MCP-compatible AI assistant.

**97 tools** covering every Fleet API endpoint.

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
# Clone
git clone https://github.com/ysrdevs/tesla-mcp
cd tesla-fleet-mcp

# Install with uv
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
# With uv
uv run tesla-fleet-mcp

# Or directly
uv run python tesla_mcp.py
```

## Full Setup Guide

### 1. Register a Developer App

Go to [developer.tesla.com](https://developer.tesla.com), create an account, and submit an application request. Select the scopes your app needs. Once approved, you'll get a Client ID and Client Secret.

### 2. Generate EC Key Pair

Tesla requires an EC key pair for command signing and Fleet Telemetry.

```bash
openssl ecparam -name prime256v1 -genkey -noout -out private-key.pem
openssl ec -in private-key.pem -pubout -out public-key.pem
```

**Keep `private-key.pem` secret.** Never commit it, never host it publicly.

### 3. Host Your Public Key

The public key must be accessible at:

```
https://your-domain.com/.well-known/appspecific/com.tesla.3p.public-key.pem
```

With nginx, this is a static file serve. The key must remain accessible -- Tesla re-validates it periodically.

### 4. Register with Tesla

Use the MCP tools (or curl) to register your domain:

```bash
# Using the MCP server interactively via Claude:
# 1. tesla_register_partner(domain="your-domain.com")
# 2. tesla_oauth_url() -> visit the URL, log in, copy the code
# 3. tesla_oauth_exchange(code="the_code_from_redirect")
```

Or via curl:

```bash
# Get partner token
curl -X POST https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token \
  -d "grant_type=client_credentials&client_id=$CLIENT_ID&client_secret=$CLIENT_SECRET&scope=openid vehicle_device_data vehicle_cmds vehicle_charging_cmds&audience=https://fleet-api.prd.na.vn.cloud.tesla.com"

# Register domain
curl -X POST https://fleet-api.prd.na.vn.cloud.tesla.com/api/1/partner_accounts \
  -H "Authorization: Bearer $PARTNER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"domain":"your-domain.com"}'
```

### 5. Pair Virtual Key to Vehicle

The vehicle owner must add the virtual key. Open this URL on a phone with the Tesla app:

```
https://tesla.com/_ak/your-domain.com
```

Or with a specific VIN:

```
https://tesla.com/_ak/your-domain.com?vin=YOUR_VIN
```

### 6. Vehicle Command Proxy (Required for Commands)

Post-2021 vehicles require commands to be cryptographically signed. Run Tesla's [Vehicle Command HTTP Proxy](https://github.com/teslamotors/vehicle-command):

```bash
git clone https://github.com/teslamotors/vehicle-command.git
cd vehicle-command
go build ./cmd/tesla-http-proxy
./tesla-http-proxy -key-file /path/to/private-key.pem -port 4443
```

> **Note**: Read-only endpoints (vehicle data, list vehicles, etc.) work without the proxy. The proxy is only needed for write commands (lock, unlock, climate, charging, etc.).

## Usage

### Claude Code

```bash
claude mcp add tesla-fleet -- uv run --directory /path/to/tesla-fleet-mcp tesla-fleet-mcp
```

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "tesla": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/tesla-fleet-mcp", "tesla-fleet-mcp"],
      "env": {
        "TESLA_CLIENT_ID": "your_client_id",
        "TESLA_CLIENT_SECRET": "your_client_secret",
        "TESLA_VIN": "your_vin",
        "TESLA_REGION": "na"
      }
    }
  }
}
```

### Install from GitHub with uvx

```bash
uvx --from "git+https://github.com/YOUR_USERNAME/tesla-fleet-mcp" tesla-fleet-mcp
```

## API Regions

| Region | Base URL |
|--------|----------|
| North America / Asia-Pacific | `https://fleet-api.prd.na.vn.cloud.tesla.com` |
| Europe / Middle East / Africa | `https://fleet-api.prd.eu.vn.cloud.tesla.com` |
| China | `https://fleet-api.prd.cn.vn.cloud.tesla.cn` |

Set `TESLA_REGION` to `na`, `eu`, or `cn`.

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

This server handles sensitive credentials. The following measures are built in:

- **Input validation on all parameters**: VINs are validated against the 17-character ISO 3779 format. Energy site IDs, invite IDs, and domains are sanitized to prevent path traversal. PINs are validated as exactly 4 digits. Numeric inputs are range-checked.
- **No secrets in tool output**: Partner tokens are used internally and never returned to the MCP client. Error responses are sanitized to avoid leaking raw HTTP bodies or auth headers.
- **Token storage**: Tokens are saved to `~/.tesla_tokens.json` with `chmod 600` (owner-read-only). The file path is not exposed in tool output.
- **No default PINs**: PIN parameters are required (no default values like "1234" in function signatures).
- **Destructive action warnings**: Tools like `unlock`, `remote_start`, and `honk` include docstring warnings prompting AI assistants to confirm with the user first.

### Before You Push to GitHub

```bash
# Verify no secrets are committed
grep -rn "TESLA_CLIENT" .env          # should NOT exist in repo
grep -rn "private-key" *.pem          # should NOT exist in repo
cat .gitignore                        # verify .env, *.pem, .tesla_tokens.json are listed
```

### Best Practices

- Never commit `.env`, `*.pem`, or `.tesla_tokens.json`
- Use a secrets manager or env vars in production
- Run the Vehicle Command Proxy with the private key -- don't embed the key in this server
- Set a billing limit on your Tesla developer account to prevent surprise charges
- Refresh tokens expire after 3 months -- the server auto-refreshes access tokens but monitor for failures

## Important Notes

- **Vehicle must be awake** before sending commands. Use `tesla_wait_for_wake`.
- **Virtual key must be paired** before commands work. Use `tesla_fleet_status` to check.
- **Don't poll `vehicle_data`** regularly. Use Fleet Telemetry instead. Each call wakes the car and costs money.
- **Some commands can't be undone**: `honk_horn`, `media_next_track`, etc. The server warns about these.
- **Refresh tokens rotate**: When you refresh, save the new refresh token. The server handles this automatically.

## Tool Count by Category

| Category | Tools | Examples |
|----------|-------|---------|
| OAuth / Setup | 4 | `tesla_oauth_url`, `tesla_oauth_exchange`, `tesla_register_partner`, `tesla_token_status` |
| Vehicle Data | 14 | `tesla_vehicles`, `tesla_vehicle_data`, `tesla_fleet_status`, `tesla_nearby_charging` |
| Climate | 12 | `tesla_climate_on`, `tesla_set_temps`, `tesla_seat_heater`, `tesla_defrost` |
| Charging | 10 | `tesla_charge_start`, `tesla_set_charge_limit`, `tesla_set_charging_amps` |
| Locks / Trunk | 4 | `tesla_lock`, `tesla_unlock`, `tesla_open_frunk`, `tesla_open_trunk` |
| Horn / Lights | 3 | `tesla_honk`, `tesla_flash_lights`, `tesla_boombox` |
| Windows | 3 | `tesla_vent_windows`, `tesla_close_windows`, `tesla_sunroof` |
| Navigation | 3 | `tesla_navigate`, `tesla_navigate_address`, `tesla_navigate_supercharger` |
| Media | 7 | `tesla_media_toggle`, `tesla_media_next`, `tesla_adjust_volume` |
| Security | 7 | `tesla_sentry_mode`, `tesla_valet_mode`, `tesla_speed_limit_set` |
| Energy | 6 | `tesla_energy_live_status`, `tesla_energy_backup_reserve` |
| Sharing | 3 | `tesla_create_share_invite`, `tesla_revoke_share_invite` |
| Telemetry | 3 | `tesla_fleet_telemetry_config_get`, `tesla_fleet_telemetry_errors` |
| Utility | 3 | `tesla_wait_for_wake`, `tesla_key_pairing_url`, `tesla_user_region` |
| **Total** | **82** | |

## Contributing

PRs welcome. If you're adding tools, follow the existing pattern: validate all inputs, use `_vin()` / `_validate_id()` / `_validate_domain()` for any user-provided path segments, and add range checks on numeric inputs.

## License

MIT
