from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_name: str = "Sudogemini"
    database_url: str = "sqlite:///./sudogemini-dev.db"
    jwt_secret: str
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    stripe_secret_key: str
    stripe_webhook_secret: str
    stripe_automatic_tax: bool = True
    stripe_success_url: str
    stripe_cancel_url: str
    stripe_portal_return_url: str
    stripe_price_starter: str
    stripe_price_professional: str
    stripe_price_business: str
    stripe_price_enterprise: str
    stripe_price_dataset: str
    connect_marketplace_enabled: bool = False
    connect_commission_bps: int = 1000
    connect_refresh_url: str
    connect_return_url: str
    @property
    def subscription_prices(self):
        return {"starter": self.stripe_price_starter, "professional": self.stripe_price_professional,
                "business": self.stripe_price_business, "enterprise": self.stripe_price_enterprise}

@lru_cache
def get_settings():
    return Settings()
