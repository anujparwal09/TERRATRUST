"""
Application configuration — loads and validates all environment variables.

Uses pydantic-settings for type-safe access. If a required variable is
missing the application will fail fast with a clear error message.
"""

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """TerraTrust-AR application settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    # --- Supabase -----------------------------------------------------------
    SUPABASE_URL: str
    SUPABASE_SERVICE_KEY: str

    # --- Google Earth Engine -------------------------------------------------
    GEE_SERVICE_ACCOUNT_EMAIL: str = ""
    GEE_SERVICE_ACCOUNT_KEY_PATH: str = "./gee-service-account.json"

    # --- NASA NISAR ----------------------------------------------------------
    NASA_EARTHDATA_USERNAME: str = ""
    NASA_EARTHDATA_PASSWORD: str = ""

    # --- Blockchain ----------------------------------------------------------
    ADMIN_WALLET_PRIVATE_KEY: str = ""
    ADMIN_WALLET_ADDRESS: str = ""
    ALCHEMY_POLYGON_AMOY_URL: str = ""
    ALCHEMY_POLYGON_MAINNET_URL: str = ""
    CONTRACT_ADDRESS: str = ""

    # --- IPFS / Pinata -------------------------------------------------------
    PINATA_JWT: str = ""

    # --- PolygonScan ---------------------------------------------------------
    POLYGONSCAN_API_KEY: str = ""

    # --- Redis ---------------------------------------------------------------
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- App -----------------------------------------------------------------
    ENVIRONMENT: str = "development"
    CORS_ORIGIN: str = "http://localhost:8081"

    # --- Derived Supabase PostgreSQL DSN (async) -----------------------------
    @property
    def supabase_postgres_dsn(self) -> str:
        """Build an asyncpg-compatible DSN from the Supabase URL.

        Supabase URLs have the form ``https://<ref>.supabase.co``.
        The corresponding PostgreSQL DSN is
        ``postgresql+asyncpg://postgres.<ref>:<service_key>@aws-0-<region>.pooler.supabase.com:6543/postgres``

        Because the actual pooler host depends on the Supabase project region
        we fall back to a sensible default.  Override via the
        ``DATABASE_URL`` env var if your project uses a different region.
        """
        return (
            f"postgresql+asyncpg://postgres:{self.SUPABASE_SERVICE_KEY}"
            f"@db.{self._supabase_ref}.supabase.co:5432/postgres"
        )

    @property
    def _supabase_ref(self) -> str:
        """Extract the project reference from the Supabase URL."""
        return self.SUPABASE_URL.replace("https://", "").replace(".supabase.co", "")

    # Optional explicit override
    DATABASE_URL: Optional[str] = None


# Singleton — import ``settings`` from anywhere in the app.
settings = Settings()  # type: ignore[call-arg]
