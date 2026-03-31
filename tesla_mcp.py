#!/usr/bin/env python3
"""
Tesla Fleet API MCP Server

A comprehensive MCP server for controlling and monitoring Tesla vehicles
via the official Fleet API. 97 tools covering vehicle commands, data
retrieval, charging, energy, fleet telemetry, and OAuth token management.

https://github.com/uvtesla/tesla-mcp
"""

import os
import re
import sys
import json
import time
import asyncio
import logging
import secrets
from pathlib import Path
from typing import Optional, Any
from datetime import datetime, timezone

import httpx
import jwt
from dotenv import load_dotenv

try:
    from fastmcp import FastMCP
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
except ImportError:
    print("FastMCP not installed. Run: uv add fastmcp")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv()

TESLA_CLIENT_ID = os.getenv("TESLA_CLIENT_ID", "")
TESLA_CLIENT_SECRET = os.getenv("TESLA_CLIENT_SECRET", "")
TESLA_REFRESH_TOKEN = os.getenv("TESLA_REFRESH_TOKEN", "")
TESLA_VIN = os.getenv("TESLA_VIN", "")
TESLA_REGION = os.getenv("TESLA_REGION", "na")  # na, eu, cn

TOKEN_FILE = os.getenv("TESLA_TOKEN_FILE", str(Path.home() / ".tesla_tokens.json"))

REGION_URLS = {
    "na": "https://fleet-api.prd.na.vn.cloud.tesla.com",
    "eu": "https://fleet-api.prd.eu.vn.cloud.tesla.com",
    "cn": "https://fleet-api.prd.cn.vn.cloud.tesla.cn",
}
AUTH_URL = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"
AUTH_AUTHORIZE_URL = "https://auth.tesla.com/oauth2/v3/authorize"

BASE_URL = REGION_URLS.get(TESLA_REGION, REGION_URLS["na"])

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("tesla-mcp")

# ---------------------------------------------------------------------------
# Input Validation
# ---------------------------------------------------------------------------
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$", re.IGNORECASE)
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.-]{1,253}[a-zA-Z0-9]$")


def _validate_vin(vin: str) -> str:
    """Validate and return a VIN. Prevents path traversal."""
    vin = vin.strip().upper()
    if not _VIN_RE.match(vin):
        raise ValueError(f"Invalid VIN format: must be 17 alphanumeric characters (no I, O, Q). Got: {vin!r}")
    return vin


def _validate_id(value: str, label: str = "ID") -> str:
    """Validate a generic ID parameter (energy_site_id, invite_id, etc.)."""
    value = str(value).strip()
    if not _SAFE_ID_RE.match(value):
        raise ValueError(f"Invalid {label}: must be 1-64 alphanumeric/dash/underscore characters. Got: {value!r}")
    return value


def _validate_domain(domain: str) -> str:
    """Validate a domain name."""
    domain = domain.strip().lower()
    if not _DOMAIN_RE.match(domain) or ".." in domain:
        raise ValueError(f"Invalid domain format: {domain!r}")
    return domain


def _validate_pin(pin: str) -> str:
    """Validate a 4-digit PIN."""
    pin = str(pin).strip()
    if not re.match(r"^\d{4}$", pin):
        raise ValueError("PIN must be exactly 4 digits.")
    return pin


def _clamp(value: float, lo: float, hi: float, label: str) -> float:
    """Clamp a numeric value to a valid range."""
    if value < lo or value > hi:
        raise ValueError(f"{label} must be between {lo} and {hi}. Got: {value}")
    return value


# ---------------------------------------------------------------------------
# Token Management
# ---------------------------------------------------------------------------
_token_cache: dict[str, Any] = {}


def _load_tokens() -> dict[str, Any]:
    global _token_cache
    try:
        token_path = Path(TOKEN_FILE)
        if token_path.exists():
            with open(token_path) as f:
                _token_cache = json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load tokens: {e}")
    return _token_cache


def _save_tokens(data: dict[str, Any]):
    global _token_cache
    _token_cache = data
    try:
        token_path = Path(TOKEN_FILE)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(json.dumps(data, indent=2))
        os.chmod(TOKEN_FILE, 0o600)
    except Exception as e:
        logger.warning(f"Failed to save tokens: {e}")


def _token_expired(token: str, buffer_seconds: int = 60) -> bool:
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        exp = payload.get("exp", 0)
        return time.time() > (exp - buffer_seconds)
    except Exception:
        return True


async def _refresh_access_token() -> str:
    tokens = _load_tokens()
    refresh_token = tokens.get("refresh_token", TESLA_REFRESH_TOKEN)
    if not refresh_token:
        raise ValueError(
            "No refresh token available. Run the OAuth setup first: "
            "use the tesla_oauth_url tool to get the auth URL, then "
            "tesla_oauth_exchange to exchange the code."
        )
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(AUTH_URL, data={
            "grant_type": "refresh_token",
            "client_id": TESLA_CLIENT_ID,
            "refresh_token": refresh_token,
        })
        resp.raise_for_status()
        data = resp.json()
    _save_tokens({
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", refresh_token),
        "expires_at": time.time() + data.get("expires_in", 3600),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    logger.info("Access token refreshed successfully")
    return data["access_token"]


async def _get_access_token() -> str:
    tokens = _load_tokens()
    access_token = tokens.get("access_token", "")
    if access_token and not _token_expired(access_token):
        return access_token
    return await _refresh_access_token()


# ---------------------------------------------------------------------------
# HTTP Client
# ---------------------------------------------------------------------------
async def _api_request(
    method: str, path: str, data: Optional[dict] = None,
    params: Optional[dict] = None, base_url: Optional[str] = None,
) -> dict:
    token = await _get_access_token()
    url = f"{base_url or BASE_URL}{path}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(
            method, url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=data if method.upper() in ("POST", "PUT", "PATCH") else None,
            params=params,
        )
        for h in ("RateLimit-Limit", "RateLimit-Remaining", "RateLimit-Reset"):
            val = resp.headers.get(h)
            if val:
                logger.debug(f"{h}: {val}")
        if resp.status_code == 408:
            return {"error": "Vehicle unavailable (408). It may be asleep. Try waking it first."}
        if resp.status_code == 429:
            reset = resp.headers.get("RateLimit-Reset", "unknown")
            return {"error": f"Rate limited (429). Retry after {reset} seconds."}
        resp.raise_for_status()
        return resp.json()


def _vin(vin: Optional[str] = None) -> str:
    v = vin or TESLA_VIN
    if not v:
        raise ValueError("No VIN provided and TESLA_VIN not set in environment.")
    return _validate_vin(v)


# ---------------------------------------------------------------------------
# API KEY AUTHENTICATION (same pattern as GoTo MCP)
# ---------------------------------------------------------------------------
API_KEY_FILE = Path(__file__).parent / ".api_key"


def get_api_key() -> str:
    """Load or generate API key."""
    api_key = os.environ.get("MCP_API_KEY")
    if not api_key:
        if API_KEY_FILE.exists():
            api_key = API_KEY_FILE.read_text().strip()
        else:
            api_key = secrets.token_urlsafe(32)
            API_KEY_FILE.write_text(api_key)
            API_KEY_FILE.chmod(0o600)
            print(f"[tesla-mcp] Generated new API key and saved to {API_KEY_FILE}", file=sys.stderr)
    return api_key


API_KEY = get_api_key()


# ---------------------------------------------------------------------------
# FastMCP Server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "Tesla Fleet API",
    instructions=(
        "Control and monitor Tesla vehicles via the official Fleet API. "
        "96 tools covering vehicle commands, data retrieval, charging, "
        "energy (Powerwall/Solar), fleet telemetry, and OAuth token management. "
        "Commands require the vehicle to be awake -- use tesla_wait_for_wake first. "
        "Destructive actions (unlock, remote_start) should be confirmed with the user."
    ),
    auth=StaticTokenVerifier(API_KEY),
)

# ===========================================================================
# OAUTH / SETUP TOOLS
# ===========================================================================
@mcp.tool()
async def tesla_oauth_url(
    redirect_uri: str = "https://bigboyserver.ca/morpheus/callback",
    scopes: str = "openid offline_access user_data vehicle_device_data vehicle_location vehicle_cmds vehicle_charging_cmds",
) -> str:
    """Generate the Tesla OAuth authorization URL. User must visit this URL,
    log in, and copy the 'code' parameter from the redirect URL."""
    if not TESLA_CLIENT_ID:
        return "Error: TESLA_CLIENT_ID not set in environment."
    import urllib.parse
    params = {
        "client_id": TESLA_CLIENT_ID, "locale": "en-US", "prompt": "login",
        "redirect_uri": redirect_uri, "response_type": "code",
        "scope": scopes, "state": "tesla_mcp_setup",
    }
    url = f"{AUTH_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
    return (
        f"Visit this URL to authorize:\n\n{url}\n\n"
        f"After login, you'll be redirected to {redirect_uri}?code=XXXXX\n"
        f"Copy the 'code' parameter and use tesla_oauth_exchange to complete setup."
    )


@mcp.tool()
async def tesla_oauth_exchange(code: str, redirect_uri: str = "https://bigboyserver.ca/morpheus/callback") -> str:
    """Exchange an OAuth authorization code for access + refresh tokens."""
    if not TESLA_CLIENT_ID or not TESLA_CLIENT_SECRET:
        return "Error: TESLA_CLIENT_ID and TESLA_CLIENT_SECRET must be set in environment."
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(AUTH_URL, data={
            "grant_type": "authorization_code", "client_id": TESLA_CLIENT_ID,
            "client_secret": TESLA_CLIENT_SECRET, "code": code,
            "audience": BASE_URL, "redirect_uri": redirect_uri,
        })
        if resp.status_code != 200:
            return f"Token exchange failed (HTTP {resp.status_code}). Check your code and credentials."
        data = resp.json()
    _save_tokens({
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "expires_at": time.time() + data.get("expires_in", 3600),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    return "Tokens saved successfully. You're all set!"


@mcp.tool()
async def tesla_register_partner(domain: str) -> dict:
    """Register your app domain with Tesla Fleet API.
    Public key must be at: https://{domain}/.well-known/appspecific/com.tesla.3p.public-key.pem"""
    domain = _validate_domain(domain)
    if not TESLA_CLIENT_ID or not TESLA_CLIENT_SECRET:
        return {"error": "TESLA_CLIENT_ID and TESLA_CLIENT_SECRET required"}
    async with httpx.AsyncClient(timeout=30) as client:
        tr = await client.post(AUTH_URL, data={
            "grant_type": "client_credentials", "client_id": TESLA_CLIENT_ID,
            "client_secret": TESLA_CLIENT_SECRET,
            "scope": "openid vehicle_device_data vehicle_cmds vehicle_charging_cmds",
            "audience": BASE_URL,
        })
        tr.raise_for_status()
        partner_token = tr.json().get("access_token", "")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BASE_URL}/api/1/partner_accounts",
            headers={"Authorization": f"Bearer {partner_token}", "Content-Type": "application/json"},
            json={"domain": domain},
        )
        resp.raise_for_status()
    return {"status": "registered", "domain": domain}


@mcp.tool()
async def tesla_token_status() -> dict:
    """Check the current token status (validity, expiry, granted scopes)."""
    tokens = _load_tokens()
    if not tokens.get("access_token"):
        return {"status": "no_token", "message": "No access token found. Run OAuth setup."}
    try:
        payload = jwt.decode(tokens["access_token"], options={"verify_signature": False})
        exp = payload.get("exp", 0)
        return {
            "status": "valid" if not _token_expired(tokens["access_token"]) else "expired",
            "expires_at": datetime.fromtimestamp(exp, tz=timezone.utc).isoformat(),
            "scopes": payload.get("scp", []),
            "updated_at": tokens.get("updated_at", "unknown"),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ===========================================================================
# VEHICLE ENDPOINTS (Data / Read)
# ===========================================================================
@mcp.tool()
async def tesla_vehicles() -> dict:
    """List all vehicles on the account."""
    return await _api_request("GET", "/api/1/vehicles")

@mcp.tool()
async def tesla_vehicle(vin: Optional[str] = None) -> dict:
    """Get info about a specific vehicle."""
    return await _api_request("GET", f"/api/1/vehicles/{_vin(vin)}")

@mcp.tool()
async def tesla_vehicle_data(
    vin: Optional[str] = None,
    endpoints: str = "charge_state;climate_state;drive_state;gui_settings;vehicle_config;vehicle_state;location_data",
) -> dict:
    """Fetch live vehicle data. Warning: this wakes the car and costs money.
    endpoints: semicolon-separated list of data groups."""
    return await _api_request("GET", f"/api/1/vehicles/{_vin(vin)}/vehicle_data", params={"endpoints": endpoints})

@mcp.tool()
async def tesla_wake_up(vin: Optional[str] = None) -> dict:
    """Wake the vehicle from sleep. May take 10-60 seconds to come online."""
    return await _api_request("POST", f"/api/1/vehicles/{_vin(vin)}/wake_up")

@mcp.tool()
async def tesla_mobile_enabled(vin: Optional[str] = None) -> dict:
    """Check if mobile access is enabled."""
    return await _api_request("GET", f"/api/1/vehicles/{_vin(vin)}/mobile_enabled")

@mcp.tool()
async def tesla_nearby_charging(vin: Optional[str] = None) -> dict:
    """Get nearby Superchargers and destination chargers."""
    return await _api_request("GET", f"/api/1/vehicles/{_vin(vin)}/nearby_charging_sites")

@mcp.tool()
async def tesla_vehicle_options(vin: Optional[str] = None) -> dict:
    """Get vehicle option details (trim, packages, etc.)."""
    return await _api_request("GET", "/api/1/dx/vehicles/options", params={"vin": _vin(vin)})

@mcp.tool()
async def tesla_release_notes(vin: Optional[str] = None) -> dict:
    """Get firmware release notes."""
    return await _api_request("GET", f"/api/1/vehicles/{_vin(vin)}/release_notes")

@mcp.tool()
async def tesla_service_data(vin: Optional[str] = None) -> dict:
    """Get service status information."""
    return await _api_request("GET", f"/api/1/vehicles/{_vin(vin)}/service_data")

@mcp.tool()
async def tesla_recent_alerts(vin: Optional[str] = None) -> dict:
    """Get recent vehicle alerts."""
    return await _api_request("GET", f"/api/1/vehicles/{_vin(vin)}/recent_alerts")

@mcp.tool()
async def tesla_fleet_status(vins: Optional[list[str]] = None) -> dict:
    """Check fleet status (key pairing, firmware, protocol requirements)."""
    vin_list = [_validate_vin(v) for v in (vins or [_vin()])]
    return await _api_request("POST", "/api/1/vehicles/fleet_status", data={"vins": vin_list})

@mcp.tool()
async def tesla_drivers(vin: Optional[str] = None) -> dict:
    """List all allowed drivers. Owner only."""
    return await _api_request("GET", f"/api/1/vehicles/{_vin(vin)}/drivers")

@mcp.tool()
async def tesla_warranty_details() -> dict:
    """Get warranty information."""
    return await _api_request("GET", "/api/1/dx/warranty/details")

@mcp.tool()
async def tesla_eligible_subscriptions(vin: Optional[str] = None) -> dict:
    """Get eligible vehicle subscriptions."""
    return await _api_request("GET", "/api/1/dx/vehicles/subscriptions/eligibility", params={"vin": _vin(vin)})

@mcp.tool()
async def tesla_eligible_upgrades(vin: Optional[str] = None) -> dict:
    """Get eligible vehicle upgrades."""
    return await _api_request("GET", "/api/1/dx/vehicles/upgrades/eligibility", params={"vin": _vin(vin)})


# ===========================================================================
# VEHICLE COMMANDS
# ===========================================================================
async def _cmd(vin: Optional[str], command: str, data: Optional[dict] = None) -> dict:
    return await _api_request("POST", f"/api/1/vehicles/{_vin(vin)}/command/{command}", data=data)

# --- Locks ---
@mcp.tool()
async def tesla_lock(vin: Optional[str] = None) -> dict:
    """Lock the vehicle."""
    return await _cmd(vin, "door_lock")

@mcp.tool()
async def tesla_unlock(vin: Optional[str] = None) -> dict:
    """Unlock the vehicle. Confirm with user before executing."""
    return await _cmd(vin, "door_unlock")

# --- Trunk ---
@mcp.tool()
async def tesla_open_frunk(vin: Optional[str] = None) -> dict:
    """Open the front trunk (frunk)."""
    return await _cmd(vin, "actuate_trunk", {"which_trunk": "front"})

@mcp.tool()
async def tesla_open_trunk(vin: Optional[str] = None) -> dict:
    """Open/close the rear trunk/tailgate."""
    return await _cmd(vin, "actuate_trunk", {"which_trunk": "rear"})

# --- Climate ---
@mcp.tool()
async def tesla_climate_on(vin: Optional[str] = None) -> dict:
    """Start climate preconditioning (heat/AC)."""
    return await _cmd(vin, "auto_conditioning_start")

@mcp.tool()
async def tesla_climate_off(vin: Optional[str] = None) -> dict:
    """Stop climate preconditioning."""
    return await _cmd(vin, "auto_conditioning_stop")

@mcp.tool()
async def tesla_set_temps(driver_temp: float = 21.0, passenger_temp: float = 21.0, vin: Optional[str] = None) -> dict:
    """Set cabin temperature in Celsius (15-28)."""
    return await _cmd(vin, "set_temps", {
        "driver_temp": _clamp(driver_temp, 15.0, 28.0, "driver_temp"),
        "passenger_temp": _clamp(passenger_temp, 15.0, 28.0, "passenger_temp"),
    })

@mcp.tool()
async def tesla_set_climate_keeper(mode: int = 0, vin: Optional[str] = None) -> dict:
    """Set climate keeper mode. 0=Off, 1=Keep, 2=Dog, 3=Camp."""
    return await _cmd(vin, "set_climate_keeper_mode", {"climate_keeper_mode": int(_clamp(mode, 0, 3, "mode"))})

@mcp.tool()
async def tesla_seat_heater(seat: int = 0, level: int = 3, vin: Optional[str] = None) -> dict:
    """Set seat heater (0-3). Seats: 0=driver, 1=passenger, 2=rear-left, 4=rear-center, 5=rear-right. Requires climate on."""
    return await _cmd(vin, "remote_seat_heater_request", {"heater": seat, "level": int(_clamp(level, 0, 3, "level"))})

@mcp.tool()
async def tesla_seat_cooler(seat: int = 1, level: int = 3, vin: Optional[str] = None) -> dict:
    """Set seat cooling (0-3). 1=front-left, 2=front-right. Requires climate on."""
    return await _cmd(vin, "remote_seat_cooler_request", {"seat_position": seat, "seat_cooler_level": int(_clamp(level, 0, 3, "level"))})

@mcp.tool()
async def tesla_steering_wheel_heater(on: bool = True, vin: Optional[str] = None) -> dict:
    """Toggle steering wheel heater. Requires climate on."""
    return await _cmd(vin, "remote_steering_wheel_heater_request", {"on": on})

@mcp.tool()
async def tesla_steering_wheel_heat_level(level: int = 3, vin: Optional[str] = None) -> dict:
    """Set steering wheel heat level (0-3). Requires climate on."""
    return await _cmd(vin, "remote_steering_wheel_heat_level_request", {"level": int(_clamp(level, 0, 3, "level"))})

@mcp.tool()
async def tesla_set_bioweapon_mode(on: bool = True, manual_override: bool = False, vin: Optional[str] = None) -> dict:
    """Toggle Bioweapon Defense Mode."""
    return await _cmd(vin, "set_bioweapon_mode", {"on": on, "manual_override": manual_override})

@mcp.tool()
async def tesla_defrost(on: bool = True, vin: Optional[str] = None) -> dict:
    """Set max defrost on/off."""
    return await _cmd(vin, "set_preconditioning_max", {"on": on})

@mcp.tool()
async def tesla_cabin_overheat_protection(on: bool = True, fan_only: bool = False, vin: Optional[str] = None) -> dict:
    """Enable/disable cabin overheat protection."""
    return await _cmd(vin, "set_cabin_overheat_protection", {"on": on, "fan_only": fan_only})

@mcp.tool()
async def tesla_set_cop_temp(cop_temp: int = 1, vin: Optional[str] = None) -> dict:
    """Set COP temperature. 0=Low(90F/30C), 1=Medium(95F/35C), 2=High(100F/40C)."""
    return await _cmd(vin, "set_cop_temp", {"cop_temp": int(_clamp(cop_temp, 0, 2, "cop_temp"))})

# --- Charging ---
@mcp.tool()
async def tesla_charge_start(vin: Optional[str] = None) -> dict:
    """Start charging."""
    return await _cmd(vin, "charge_start")

@mcp.tool()
async def tesla_charge_stop(vin: Optional[str] = None) -> dict:
    """Stop charging."""
    return await _cmd(vin, "charge_stop")

@mcp.tool()
async def tesla_set_charge_limit(percent: int = 80, vin: Optional[str] = None) -> dict:
    """Set the charge limit percentage (50-100)."""
    return await _cmd(vin, "set_charge_limit", {"percent": int(_clamp(percent, 50, 100, "percent"))})

@mcp.tool()
async def tesla_set_charging_amps(amps: int = 32, vin: Optional[str] = None) -> dict:
    """Set charging amperage (1-48)."""
    return await _cmd(vin, "set_charging_amps", {"charging_amps": int(_clamp(amps, 1, 48, "amps"))})

@mcp.tool()
async def tesla_charge_port_open(vin: Optional[str] = None) -> dict:
    """Open the charge port door."""
    return await _cmd(vin, "charge_port_door_open")

@mcp.tool()
async def tesla_charge_port_close(vin: Optional[str] = None) -> dict:
    """Close the charge port door."""
    return await _cmd(vin, "charge_port_door_close")

@mcp.tool()
async def tesla_charge_standard(vin: Optional[str] = None) -> dict:
    """Set charge mode to Standard."""
    return await _cmd(vin, "charge_standard")

@mcp.tool()
async def tesla_charge_max_range(vin: Optional[str] = None) -> dict:
    """Set charge to max range mode. Use sparingly."""
    return await _cmd(vin, "charge_max_range")

@mcp.tool()
async def tesla_add_charge_schedule(
    id: int = 0, name: str = "", days_of_week: int = 127,
    start_enabled: bool = True, start_time: int = 0, end_enabled: bool = False, end_time: int = 0,
    one_time: bool = False, enabled: bool = True, latitude: float = 0, longitude: float = 0,
    vin: Optional[str] = None,
) -> dict:
    """Add a charge schedule. Times are minutes after midnight (0-1439)."""
    return await _cmd(vin, "add_charge_schedule", {
        "id": id, "name": name, "days_of_week": days_of_week,
        "start_enabled": start_enabled, "start_time": int(_clamp(start_time, 0, 1439, "start_time")),
        "end_enabled": end_enabled, "end_time": int(_clamp(end_time, 0, 1439, "end_time")),
        "one_time": one_time, "enabled": enabled, "latitude": latitude, "longitude": longitude,
    })

@mcp.tool()
async def tesla_remove_charge_schedule(id: int = 0, vin: Optional[str] = None) -> dict:
    """Remove a charge schedule by ID."""
    return await _cmd(vin, "remove_charge_schedule", {"id": id})

@mcp.tool()
async def tesla_add_precondition_schedule(
    id: int = 0, name: str = "", days_of_week: int = 127, precondition_time: int = 420,
    one_time: bool = False, enabled: bool = True, latitude: float = 0, longitude: float = 0,
    vin: Optional[str] = None,
) -> dict:
    """Add a preconditioning schedule. Time is minutes after midnight (420 = 7:00 AM)."""
    return await _cmd(vin, "add_precondition_schedule", {
        "id": id, "name": name, "days_of_week": days_of_week,
        "precondition_time": int(_clamp(precondition_time, 0, 1439, "precondition_time")),
        "one_time": one_time, "enabled": enabled, "latitude": latitude, "longitude": longitude,
    })

@mcp.tool()
async def tesla_remove_precondition_schedule(id: int = 0, vin: Optional[str] = None) -> dict:
    """Remove a preconditioning schedule by ID."""
    return await _cmd(vin, "remove_precondition_schedule", {"id": id})

# --- Horn / Lights ---
@mcp.tool()
async def tesla_honk(vin: Optional[str] = None) -> dict:
    """Honk the horn. Vehicle must be in park. Cannot be undone."""
    return await _cmd(vin, "honk_horn")

@mcp.tool()
async def tesla_flash_lights(vin: Optional[str] = None) -> dict:
    """Flash the headlights. Vehicle must be in park."""
    return await _cmd(vin, "flash_lights")

@mcp.tool()
async def tesla_boombox(sound_id: int = 2000, vin: Optional[str] = None) -> dict:
    """Play external speaker sound. 0=random fart, 2000=locate ping."""
    return await _cmd(vin, "remote_boombox", {"sound": sound_id})

# --- Windows / Sunroof ---
@mcp.tool()
async def tesla_vent_windows(vin: Optional[str] = None) -> dict:
    """Vent all windows. Vehicle must be in park."""
    return await _cmd(vin, "window_control", {"command": "vent", "lat": 0, "lon": 0})

@mcp.tool()
async def tesla_close_windows(lat: float = 0, lon: float = 0, vin: Optional[str] = None) -> dict:
    """Close all windows. Provide lat/lon for proximity check (not needed on Model 3 platform)."""
    return await _cmd(vin, "window_control", {"command": "close", "lat": lat, "lon": lon})

@mcp.tool()
async def tesla_sunroof(state: str = "vent", vin: Optional[str] = None) -> dict:
    """Control sunroof. States: stop, close, vent."""
    if state not in ("stop", "close", "vent"):
        raise ValueError("Sunroof state must be: stop, close, or vent")
    return await _cmd(vin, "sun_roof_control", {"state": state})

# --- Media ---
@mcp.tool()
async def tesla_media_toggle(vin: Optional[str] = None) -> dict:
    """Toggle media play/pause."""
    return await _cmd(vin, "media_toggle_playback")

@mcp.tool()
async def tesla_media_next(vin: Optional[str] = None) -> dict:
    """Next track."""
    return await _cmd(vin, "media_next_track")

@mcp.tool()
async def tesla_media_prev(vin: Optional[str] = None) -> dict:
    """Previous track."""
    return await _cmd(vin, "media_prev_track")

@mcp.tool()
async def tesla_media_next_fav(vin: Optional[str] = None) -> dict:
    """Next favorite track."""
    return await _cmd(vin, "media_next_fav")

@mcp.tool()
async def tesla_media_prev_fav(vin: Optional[str] = None) -> dict:
    """Previous favorite track."""
    return await _cmd(vin, "media_prev_fav")

@mcp.tool()
async def tesla_media_volume_down(vin: Optional[str] = None) -> dict:
    """Volume down by one notch."""
    return await _cmd(vin, "media_volume_down")

@mcp.tool()
async def tesla_adjust_volume(volume: float = 5.0, vin: Optional[str] = None) -> dict:
    """Set media volume (0.0-11.0). Requires user present + mobile access."""
    return await _cmd(vin, "adjust_volume", {"volume": _clamp(volume, 0.0, 11.0, "volume")})

# --- Navigation ---
@mcp.tool()
async def tesla_navigate(lat: float, lon: float, order: int = 1, vin: Optional[str] = None) -> dict:
    """Navigate to GPS coordinates."""
    _clamp(lat, -90, 90, "latitude"); _clamp(lon, -180, 180, "longitude")
    return await _cmd(vin, "navigation_gps_request", {"lat": lat, "lon": lon, "order": order})

@mcp.tool()
async def tesla_navigate_address(address: str, locale: str = "en-US", vin: Optional[str] = None) -> dict:
    """Send an address to the vehicle navigation system."""
    if not address.strip():
        raise ValueError("Address cannot be empty.")
    return await _cmd(vin, "navigation_request", {
        "type": "share_ext_content_raw", "locale": locale,
        "timestamp_ms": str(int(time.time() * 1000)),
        "value": {"android.intent.extra.TEXT": address},
    })

@mcp.tool()
async def tesla_navigate_supercharger(id: int, order: int = 1, vin: Optional[str] = None) -> dict:
    """Navigate to a Supercharger by ID."""
    return await _cmd(vin, "navigation_sc_request", {"id": id, "order": order})

# --- Sentry ---
@mcp.tool()
async def tesla_sentry_mode(on: bool = True, vin: Optional[str] = None) -> dict:
    """Enable/disable Sentry Mode."""
    return await _cmd(vin, "set_sentry_mode", {"on": on})

# --- HomeLink ---
@mcp.tool()
async def tesla_homelink(lat: float, lon: float, vin: Optional[str] = None) -> dict:
    """Trigger HomeLink (garage door). Provide vehicle's current lat/lon."""
    _clamp(lat, -90, 90, "latitude"); _clamp(lon, -180, 180, "longitude")
    return await _cmd(vin, "trigger_homelink", {"lat": lat, "lon": lon})

# --- Valet / PIN ---
@mcp.tool()
async def tesla_valet_mode(on: bool = True, pin: Optional[str] = None, vin: Optional[str] = None) -> dict:
    """Enable/disable Valet Mode with a 4-digit PIN."""
    data: dict[str, Any] = {"on": on}
    if pin:
        data["password"] = _validate_pin(pin)
    return await _cmd(vin, "set_valet_mode", data)

@mcp.tool()
async def tesla_set_pin_to_drive(pin: str, vin: Optional[str] = None) -> dict:
    """Set a 4-digit PIN required before driving. Owner/fleet manager only."""
    return await _cmd(vin, "set_pin_to_drive", {"on": True, "password": _validate_pin(pin)})

@mcp.tool()
async def tesla_clear_pin_to_drive(vin: Optional[str] = None) -> dict:
    """Remove PIN to Drive. Firmware 2023.44+, owner/fleet manager only."""
    return await _cmd(vin, "clear_pin_to_drive_admin")

# --- Speed Limit ---
@mcp.tool()
async def tesla_speed_limit_activate(pin: str, vin: Optional[str] = None) -> dict:
    """Activate Speed Limit Mode with a 4-digit PIN."""
    return await _cmd(vin, "speed_limit_activate", {"pin": _validate_pin(pin)})

@mcp.tool()
async def tesla_speed_limit_deactivate(pin: str, vin: Optional[str] = None) -> dict:
    """Deactivate Speed Limit Mode with your 4-digit PIN."""
    return await _cmd(vin, "speed_limit_deactivate", {"pin": _validate_pin(pin)})

@mcp.tool()
async def tesla_speed_limit_set(limit_mph: int, vin: Optional[str] = None) -> dict:
    """Set max speed for Speed Limit Mode (50-120 MPH)."""
    return await _cmd(vin, "speed_limit_set_limit", {"limit_mph": int(_clamp(limit_mph, 50, 120, "limit_mph"))})

@mcp.tool()
async def tesla_speed_limit_clear_pin(pin: str, vin: Optional[str] = None) -> dict:
    """Clear Speed Limit Mode PIN."""
    return await _cmd(vin, "speed_limit_clear_pin", {"pin": _validate_pin(pin)})

# --- Remote Start ---
@mcp.tool()
async def tesla_remote_start(vin: Optional[str] = None) -> dict:
    """Start the vehicle remotely. Keyless driving must be enabled. Confirm with user."""
    return await _cmd(vin, "remote_start_drive")

# --- Software Update ---
@mcp.tool()
async def tesla_schedule_update(offset_sec: int = 7200, vin: Optional[str] = None) -> dict:
    """Schedule a software update in offset_sec seconds (default 2h, max 24h)."""
    return await _cmd(vin, "schedule_software_update", {"offset_sec": int(_clamp(offset_sec, 0, 86400, "offset_sec"))})

@mcp.tool()
async def tesla_cancel_update(vin: Optional[str] = None) -> dict:
    """Cancel a pending software update."""
    return await _cmd(vin, "cancel_software_update")

# --- Vehicle Name ---
@mcp.tool()
async def tesla_set_name(name: str, vin: Optional[str] = None) -> dict:
    """Change the vehicle's name (1-50 chars)."""
    name = name.strip()
    if not name or len(name) > 50:
        raise ValueError("Vehicle name must be 1-50 characters.")
    return await _cmd(vin, "set_vehicle_name", {"vehicle_name": name})

# --- Guest Mode ---
@mcp.tool()
async def tesla_guest_mode(on: bool = True, vin: Optional[str] = None) -> dict:
    """Enable/disable Guest Mode."""
    return await _cmd(vin, "guest_mode", {"enable": on})


# ===========================================================================
# CHARGING ENDPOINTS (Billing / History)
# ===========================================================================
@mcp.tool()
async def tesla_charging_history(vin: Optional[str] = None, page: int = 1, per_page: int = 25) -> dict:
    """Get charging history (paginated)."""
    params: dict[str, Any] = {"page": int(_clamp(page, 1, 1000, "page")), "per_page": int(_clamp(per_page, 1, 100, "per_page"))}
    if vin:
        params["vin"] = _validate_vin(vin)
    return await _api_request("GET", "/api/1/dx/charging/history", params=params)

@mcp.tool()
async def tesla_charging_sessions() -> dict:
    """Get charging session data (business fleet owners only)."""
    return await _api_request("GET", "/api/1/dx/charging/sessions")


# ===========================================================================
# ENERGY ENDPOINTS
# ===========================================================================
@mcp.tool()
async def tesla_products() -> dict:
    """List all Tesla products (vehicles + energy sites)."""
    return await _api_request("GET", "/api/1/products")

@mcp.tool()
async def tesla_energy_site_info(energy_site_id: str) -> dict:
    """Get energy site info (Powerwall, Solar)."""
    return await _api_request("GET", f"/api/1/energy_sites/{_validate_id(energy_site_id, 'energy_site_id')}/site_info")

@mcp.tool()
async def tesla_energy_live_status(energy_site_id: str) -> dict:
    """Get live power/energy status of an energy site."""
    return await _api_request("GET", f"/api/1/energy_sites/{_validate_id(energy_site_id, 'energy_site_id')}/live_status")

@mcp.tool()
async def tesla_energy_history(
    energy_site_id: str, kind: str = "energy", start_date: str = "",
    end_date: str = "", period: str = "day", time_zone: str = "",
) -> dict:
    """Get energy history. kind: energy|backup. period: day|week|month|year."""
    sid = _validate_id(energy_site_id, "energy_site_id")
    if kind not in ("energy", "backup"):
        raise ValueError("kind must be 'energy' or 'backup'")
    if period not in ("day", "week", "month", "year"):
        raise ValueError("period must be: day, week, month, year")
    params: dict[str, str] = {"kind": kind, "period": period}
    if time_zone: params["time_zone"] = time_zone
    if start_date: params["start_date"] = start_date
    if end_date: params["end_date"] = end_date
    return await _api_request("GET", f"/api/1/energy_sites/{sid}/calendar_history", params=params)

@mcp.tool()
async def tesla_energy_backup_reserve(energy_site_id: str, backup_reserve_percent: int = 20) -> dict:
    """Set backup reserve percentage (0-100)."""
    sid = _validate_id(energy_site_id, "energy_site_id")
    return await _api_request("POST", f"/api/1/energy_sites/{sid}/backup",
        data={"backup_reserve_percent": int(_clamp(backup_reserve_percent, 0, 100, "percent"))})

@mcp.tool()
async def tesla_energy_operation_mode(energy_site_id: str, mode: str = "self_consumption") -> dict:
    """Set energy site mode. 'autonomous' (time-based) or 'self_consumption' (self-powered)."""
    sid = _validate_id(energy_site_id, "energy_site_id")
    if mode not in ("autonomous", "self_consumption"):
        raise ValueError("mode must be 'autonomous' or 'self_consumption'")
    return await _api_request("POST", f"/api/1/energy_sites/{sid}/operation", data={"default_real_mode": mode})

@mcp.tool()
async def tesla_energy_storm_mode(energy_site_id: str, enabled: bool = True) -> dict:
    """Enable/disable Storm Watch."""
    sid = _validate_id(energy_site_id, "energy_site_id")
    return await _api_request("POST", f"/api/1/energy_sites/{sid}/storm_mode", data={"enabled": enabled})


# ===========================================================================
# SHARE INVITES
# ===========================================================================
@mcp.tool()
async def tesla_share_invites(vin: Optional[str] = None) -> dict:
    """Get active share invites for a vehicle."""
    return await _api_request("GET", f"/api/1/vehicles/{_vin(vin)}/invitations")

@mcp.tool()
async def tesla_create_share_invite(vin: Optional[str] = None) -> dict:
    """Create a share invite link (single-use, expires 24h). Up to 5 drivers."""
    return await _api_request("POST", f"/api/1/vehicles/{_vin(vin)}/invitations")

@mcp.tool()
async def tesla_revoke_share_invite(invite_id: str, vin: Optional[str] = None) -> dict:
    """Revoke a share invite by ID."""
    return await _api_request("POST", f"/api/1/vehicles/{_vin(vin)}/invitations/{_validate_id(invite_id, 'invite_id')}/revoke")


# ===========================================================================
# FLEET TELEMETRY CONFIG
# ===========================================================================
@mcp.tool()
async def tesla_fleet_telemetry_config_get(vin: Optional[str] = None) -> dict:
    """Get Fleet Telemetry configuration."""
    return await _api_request("GET", f"/api/1/vehicles/{_vin(vin)}/fleet_telemetry_config")

@mcp.tool()
async def tesla_fleet_telemetry_config_delete(vin: Optional[str] = None) -> dict:
    """Remove Fleet Telemetry configuration."""
    return await _api_request("DELETE", f"/api/1/vehicles/{_vin(vin)}/fleet_telemetry_config")

@mcp.tool()
async def tesla_fleet_telemetry_errors(vin: Optional[str] = None) -> dict:
    """Get recent Fleet Telemetry errors."""
    return await _api_request("GET", f"/api/1/vehicles/{_vin(vin)}/fleet_telemetry_errors")


# ===========================================================================
# PARTNER ENDPOINTS
# ===========================================================================
@mcp.tool()
async def tesla_partner_public_key(domain: str) -> dict:
    """Verify the public key registered for a domain."""
    return await _api_request("GET", "/api/1/partner_accounts/public_key", params={"domain": _validate_domain(domain)})


# ===========================================================================
# USER ENDPOINTS
# ===========================================================================
@mcp.tool()
async def tesla_user_region() -> dict:
    """Get the user's region for API routing."""
    return await _api_request("GET", "/api/1/users/region")


# ===========================================================================
# UTILITY
# ===========================================================================
@mcp.tool()
async def tesla_wait_for_wake(timeout_sec: int = 60, poll_interval: int = 5, vin: Optional[str] = None) -> dict:
    """Wake the vehicle and wait until online (up to timeout_sec)."""
    timeout_sec = int(_clamp(timeout_sec, 10, 300, "timeout_sec"))
    poll_interval = int(_clamp(poll_interval, 2, 30, "poll_interval"))
    v = _vin(vin)
    await _api_request("POST", f"/api/1/vehicles/{v}/wake_up")
    start = time.time()
    while time.time() - start < timeout_sec:
        try:
            result = await _api_request("GET", f"/api/1/vehicles/{v}")
            if result.get("response", {}).get("state") == "online":
                return {"status": "online", "elapsed_sec": round(time.time() - start, 1)}
        except Exception:
            pass
        await asyncio.sleep(poll_interval)
    return {"status": "timeout", "elapsed_sec": timeout_sec}

@mcp.tool()
async def tesla_key_pairing_url(domain: str, vin: Optional[str] = None) -> str:
    """Generate the URL for pairing your app's virtual key to a vehicle."""
    domain = _validate_domain(domain)
    url = f"https://tesla.com/_ak/{domain}"
    if vin:
        url += f"?vin={_validate_vin(vin)}"
    return f"Send this link to the vehicle owner to pair the virtual key:\n{url}"


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    transport = os.getenv("TESLA_MCP_TRANSPORT", "streamable-http")
    host = os.getenv("TESLA_MCP_HOST", "0.0.0.0")
    port = int(os.getenv("TESLA_MCP_PORT", "8752"))

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http", host=host, port=port)
