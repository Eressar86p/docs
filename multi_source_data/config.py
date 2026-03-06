"""
Central configuration – reads from environment variables or a .env file.
Copy .env.example to .env and fill in your values.
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Microsoft 365 / Azure AD app registration ──────────────────────────
    # Create an app at https://portal.azure.com > Azure Active Directory > App registrations
    # Grant delegated permissions: Files.Read, Sites.Read.All, User.Read
    MS_CLIENT_ID: str = ""
    MS_TENANT_ID: str = "common"          # "common" for multi-tenant / personal accounts
    MS_REDIRECT_URI: str = "http://localhost"

    # ── Azure Storage (only needed if using Azure connector) ────────────────
    AZURE_STORAGE_CONNECTION_STRING: str = ""
    AZURE_STORAGE_ACCOUNT_NAME: str = ""

    # ── Database ────────────────────────────────────────────────────────────
    DATABASE_DIR: Path = Path("./databases")

    # ── API server ──────────────────────────────────────────────────────────
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    def db_path(self, name: str) -> Path:
        self.DATABASE_DIR.mkdir(parents=True, exist_ok=True)
        return self.DATABASE_DIR / f"{name}.db"


settings = Settings()
