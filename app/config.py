from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ORATOR_", env_file=".env", extra="ignore")

    s3_bucket: str = ""  # required for synthesis, checked there rather than at startup
    aws_region: str = "eu-west-1"
    aws_profile: str = ""  # local dev only, empty means the default boto3 chain (IAM role when deployed)
    cors_origin: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
