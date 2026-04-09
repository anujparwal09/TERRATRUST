"""Application configuration and environment loading."""

from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """TerraTrust-AR application settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    # --- Firebase Authentication -------------------------------------------
    FIREBASE_PROJECT_ID: str

    # --- Supabase -----------------------------------------------------------
    SUPABASE_URL: str
    SUPABASE_SERVICE_KEY: str
    DATABASE_URL: Optional[str] = None

    # --- Shared Google backend credentials ---------------------------------
    GOOGLE_CLOUD_PROJECT: str
    GOOGLE_APPLICATION_CREDENTIALS: str = "./backend-google-service-account.json"

    # --- NASA NISAR ---------------------------------------------------------
    NASA_EARTHDATA_USERNAME: str = ""
    NASA_EARTHDATA_PASSWORD: str = ""
    NISAR_PRODUCTION_READY: bool = False

    # --- Blockchain ---------------------------------------------------------
    ADMIN_WALLET_PRIVATE_KEY: str = ""
    ADMIN_WALLET_ADDRESS: str = ""
    ALCHEMY_POLYGON_AMOY_URL: str = ""
    CONTRACT_ADDRESS: str = ""

    # --- IPFS / Pinata ------------------------------------------------------
    PINATA_JWT: str = ""
    PINATA_GATEWAY_URL: str = ""

    # --- PolygonScan --------------------------------------------------------
    POLYGONSCAN_API_KEY: str = ""

    # --- Government APIs ----------------------------------------------------
    LGD_API_BASE: str = "http://115.124.105.220/API"

    # --- Redis --------------------------------------------------------------
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- App ----------------------------------------------------------------
    ENVIRONMENT: Literal["development", "production"] = "development"
    WEB_CORS_ORIGINS: str = ""
    MAINTENANCE_MODE: bool = False
    MAINTENANCE_MESSAGE: Optional[str] = None


# Singleton — import ``settings`` from anywhere in the app.
settings = Settings()  # type: ignore[call-arg]
