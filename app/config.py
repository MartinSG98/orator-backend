from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ORATOR_", env_file=".env", extra="ignore")

    runtime: str = "local"  # "local" or "aws", selects the storage/persistence/jobs seams per ADR 0007
    s3_bucket: str = ""  # Polly staging bucket, required for synthesis, checked there rather than at startup
    media_bucket: str = ""  # media bucket, required in the aws runtime
    aws_region: str = "eu-west-2"
    aws_profile: str = ""  # local dev only, empty means the default boto3 chain (IAM role when deployed)
    cors_origin: str = "http://localhost:5173"
    database_url: str = "sqlite:///orator.db"
    media_dir: Path = Path("media")


@lru_cache
def get_settings() -> Settings:
    return Settings()
