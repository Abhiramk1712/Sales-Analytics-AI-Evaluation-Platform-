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

    # CORS — add your React dev server
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]

settings = Settings()
