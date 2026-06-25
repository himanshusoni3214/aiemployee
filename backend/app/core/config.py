from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://voryx:voryx_password@localhost:5432/voryx_ops"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    first_superuser_email: str = "admin@themealz.com"
    first_superuser_password: str = ""
    credential_encryption_key: str = ""
    hermes_base_url: str = "http://localhost:9000"
    hermes_jobs_path: str = "/jobs"
    hermes_data_path: str = ""
    hermes_sync_interval_seconds: int = 45
    class Config: env_file = ".env"
settings = Settings()
