from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class AdminSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    admin_username: str = "admin"
    admin_password_hash: str = ""
    admin_jwt_secret: str = ""
    bot_token: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.admin_password_hash and len(self.admin_jwt_secret) >= 32)


@lru_cache
def get_admin_settings() -> AdminSettings:
    return AdminSettings()
