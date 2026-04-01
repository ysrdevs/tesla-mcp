"""
FastMCP Personal Auth Provider (Modified)

OAuth 2.1 auth provider for FastMCP with static client ID + secret support.
Only pre-registered clients can authorize. Works with Claude.ai (enter
client ID + secret in Advanced Settings), Claude Code, Poke, etc.

Usage:
    from fastmcp import FastMCP
    from personal_auth import PersonalAuthProvider

    auth = PersonalAuthProvider(
        base_url="https://your-domain.com",
        client_id="my-client-id",
        client_secret="my-client-secret",
    )

    mcp = FastMCP(name="my-server", auth=auth)
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8050)
"""

import json
import secrets
import time
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from mcp.server.auth.settings import ClientRegistrationOptions

logger = logging.getLogger("personal-auth")

DEFAULT_ACCESS_TOKEN_EXPIRY = 30 * 24 * 60 * 60  # 30 days
DEFAULT_STATE_DIR = ".oauth-state"


class PersonalAuthProvider(InMemoryOAuthProvider):
    """OAuth 2.1 provider with static client ID + secret.

    Features:
    - Pre-registered client ID + secret (only matching clients can authorize)
    - DCR enabled for protocol compliance (Claude needs it for discovery)
    - PKCE support (handled by FastMCP framework)
    - Token persistence to a JSON file (survives restarts)
    - Configurable token expiry (default 30 days)
    """

    def __init__(
        self,
        base_url: str,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        allowed_redirect_domains: Optional[list[str]] = None,
        access_token_expiry_seconds: int = DEFAULT_ACCESS_TOKEN_EXPIRY,
        state_dir: Optional[str] = None,
    ):
        super().__init__(
            base_url=base_url,
            client_registration_options=ClientRegistrationOptions(enabled=True),
        )

        self._static_client_id = client_id
        self._static_client_secret = client_secret
        self.allowed_redirect_domains = allowed_redirect_domains if allowed_redirect_domains is not None else [
            "claude.ai", "claude.com", "localhost"
        ]
        self.access_token_expiry_seconds = access_token_expiry_seconds
        self._state_dir = Path(state_dir or DEFAULT_STATE_DIR)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._load_state()

        # Pre-register the static client if provided
        if self._static_client_id:
            self.clients[self._static_client_id] = OAuthClientInformationFull(
                client_id=self._static_client_id,
                client_secret=self._static_client_secret,
                client_name="tesla-mcp-client",
                redirect_uris=[
                    "https://claude.ai/api/mcp/auth_callback",
                    "https://claude.com/api/mcp/auth_callback",
                    "http://localhost:6274/oauth/callback",
                    "http://localhost:6274/oauth/callback/debug",
                ],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                token_endpoint_auth_method="client_secret_post",
            )
            logger.info(f"Pre-registered static client: {self._static_client_id}")

    # --- State persistence ---

    def _state_file(self) -> Path:
        return self._state_dir / "oauth_tokens.json"

    def _load_state(self):
        f = self._state_file()
        if not f.exists():
            return
        try:
            data = json.loads(f.read_text())
            for k, v in data.get("clients", {}).items():
                self.clients[k] = OAuthClientInformationFull(**v)
            for k, v in data.get("access_tokens", {}).items():
                self.access_tokens[k] = AccessToken(**v)
            for k, v in data.get("refresh_tokens", {}).items():
                self.refresh_tokens[k] = RefreshToken(**v)
            self._access_to_refresh_map = data.get("a2r", {})
            self._refresh_to_access_map = data.get("r2a", {})
            logger.info(
                f"Loaded OAuth state: {len(self.clients)} clients, "
                f"{len(self.access_tokens)} access tokens"
            )
        except Exception as e:
            logger.warning(f"Failed to load OAuth state from {f}: {e}")

    def _save_state(self):
        def serialize(obj):
            if hasattr(obj, "model_dump"):
                return obj.model_dump(mode="json")
            return {
                "token": obj.token, "client_id": obj.client_id,
                "scopes": obj.scopes, "expires_at": obj.expires_at,
            }

        data = {
            "clients": {k: v.model_dump(mode="json") for k, v in self.clients.items()},
            "access_tokens": {k: serialize(v) for k, v in self.access_tokens.items()},
            "refresh_tokens": {k: serialize(v) for k, v in self.refresh_tokens.items()},
            "a2r": self._access_to_refresh_map,
            "r2a": self._refresh_to_access_map,
        }
        self._state_file().write_text(json.dumps(data, indent=2))

    # --- Authorization gate ---

    def _is_redirect_allowed(self, redirect_uri: str) -> bool:
        if self.allowed_redirect_domains is None:
            return True
        try:
            host = urlparse(redirect_uri).hostname or ""
            return any(
                host == domain or host.endswith(f".{domain}")
                for domain in self.allowed_redirect_domains
            )
        except Exception:
            return False

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # If static client is configured, check if this DCR request matches it
        if self._static_client_id and self._static_client_secret:
            # Allow DCR but the client won't be able to authorize without
            # matching the static client_id + secret
            pass
        await super().register_client(client_info)
        self._save_state()

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        redirect = str(params.redirect_uri) if params.redirect_uri else ""

        # Check redirect domain
        if not self._is_redirect_allowed(redirect):
            raise AuthorizeError(
                error="access_denied",
                error_description="Redirect URI domain not allowed.",
            )

        # If static client credentials are configured, ONLY allow that client
        if self._static_client_id and self._static_client_secret:
            if client.client_id != self._static_client_id:
                logger.warning(f"Authorization denied: unknown client_id {client.client_id}")
                raise AuthorizeError(
                    error="access_denied",
                    error_description="Unknown client. Provide valid client_id and client_secret.",
                )
            if client.client_secret != self._static_client_secret:
                logger.warning(f"Authorization denied: wrong client_secret for {client.client_id}")
                raise AuthorizeError(
                    error="access_denied",
                    error_description="Invalid client credentials.",
                )

        result = await super().authorize(client, params)
        self._save_state()
        return result

    # --- Token exchange with configurable expiry ---

    def _validate_client(self, client: OAuthClientInformationFull):
        """Reject any client that doesn't match static credentials."""
        if self._static_client_id and self._static_client_secret:
            if client.client_id != self._static_client_id:
                raise TokenError("invalid_client", "Unknown client.")
            if client.client_secret != self._static_client_secret:
                raise TokenError("invalid_client", "Invalid client credentials.")

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        self._validate_client(client)

        if authorization_code.code not in self.auth_codes:
            raise TokenError("invalid_grant", "Authorization code not found or already used.")

        del self.auth_codes[authorization_code.code]

        access_token_value = f"pat_{secrets.token_hex(32)}"
        refresh_token_value = f"prt_{secrets.token_hex(32)}"
        access_token_expires_at = int(time.time() + self.access_token_expiry_seconds)

        if client.client_id is None:
            raise TokenError("invalid_client", "Client ID is required")

        self.access_tokens[access_token_value] = AccessToken(
            token=access_token_value,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=access_token_expires_at,
        )
        self.refresh_tokens[refresh_token_value] = RefreshToken(
            token=refresh_token_value,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=None,
        )

        self._access_to_refresh_map[access_token_value] = refresh_token_value
        self._refresh_to_access_map[refresh_token_value] = access_token_value
        self._save_state()

        return OAuthToken(
            access_token=access_token_value,
            token_type="Bearer",
            expires_in=self.access_token_expiry_seconds,
            refresh_token=refresh_token_value,
            scope=" ".join(authorization_code.scopes),
        )

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        self._validate_client(client)
        result = await super().exchange_refresh_token(client, refresh_token, scopes)
        self._save_state()
        return result

    async def revoke_token(self, token):
        await super().revoke_token(token)
        self._save_state()
