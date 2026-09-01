"""
config.py — all settings loaded from environment / .env
"""
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/sales_analytics"

    # LLM Provider Configuration
    LLM_PROVIDER: str = "openai"  # 'openai' or 'anthropic'
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

    # App
    DEBUG: bool = False
    ENVIRONMENT: str = "development"  # 'development' | 'staging' | 'production'
    TRAINING_DEMO_MODE: bool = True
    DEMO_MODE: bool = True
    DEMO_DEFAULT_ROLE: str = "executive"
    DEMO_DEFAULT_COMPANY: str = "techo-solutions"
    APP_TITLE: str = "Sales Analytics AI"
    APP_VERSION: str = "1.0.0"

    # Authentication — production mode (DEMO_MODE=false)
    # Requests must carry a signed JWT. AUTH_JWT_SECRET has no default on
    # purpose: an empty secret is a configuration error, not a permissive mode.
    AUTH_JWT_SECRET: str = ""
    AUTH_JWT_ALGORITHM: str = "HS256"
    AUTH_JWT_ISSUER: str = ""        # verified when set
    AUTH_JWT_AUDIENCE: str = ""      # verified when set
    AUTH_JWT_LEEWAY_SECONDS: int = 30

    # Ingestion — filesystem confinement for caller-supplied source paths
    INGESTION_SOURCE_ROOT: str = "companies"

    # Schema management
    AUTO_CREATE_TABLES: bool = True  # Set False in production; use Alembic instead

    # Destructive operations guard
    ALLOW_DESTRUCTIVE_LOAD: bool = False

    # CORS — add your React dev server
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]

settings = Settings()
