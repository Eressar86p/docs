"""
Microsoft 365 MSAL authentication helper.
Uses the Device Code Flow so no browser redirect is needed in a terminal.
"""
from __future__ import annotations

import msal
from rich.console import Console
from rich.panel import Panel

from config import settings

console = Console()

# Microsoft Graph scopes required by the connectors
_SCOPES = [
    "User.Read",
    "Files.Read",
    "Files.Read.All",
    "Sites.Read.All",
]

_TOKEN_CACHE: dict = {}      # in-memory token cache (keyed by tenant)


def _build_app() -> msal.PublicClientApplication:
    cache = msal.SerializableTokenCache()
    app = msal.PublicClientApplication(
        client_id=settings.MS_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{settings.MS_TENANT_ID}",
        token_cache=cache,
    )
    return app


def get_access_token() -> str:
    """
    Acquire an access token using Device Code Flow.
    Returns the access token string.
    Raises RuntimeError if authentication fails.
    """
    if not settings.MS_CLIENT_ID:
        raise RuntimeError(
            "MS_CLIENT_ID is not set. "
            "Copy .env.example to .env and fill in your Azure AD app registration details."
        )

    app = _build_app()

    # Try silent first (uses cached tokens)
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(_SCOPES, account=accounts[0])
        if result and "access_token" in result:
            return result["access_token"]

    # Fall back to device code flow
    flow = app.initiate_device_flow(scopes=_SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Could not initiate device flow: {flow.get('error_description')}")

    console.print(
        Panel(
            f"[bold yellow]Open this URL in a browser:[/bold yellow]\n"
            f"  {flow['verification_uri']}\n\n"
            f"[bold yellow]Enter code:[/bold yellow]  [bold cyan]{flow['user_code']}[/bold cyan]",
            title="Microsoft 365 Login",
            border_style="cyan",
        )
    )

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(
            f"Authentication failed: {result.get('error_description', result.get('error'))}"
        )

    console.print("[green]✓ Authentication successful![/green]")
    return result["access_token"]
