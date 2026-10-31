from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://localhost/generated_project"
    jwt_secret: str = ""
    jwt_issuer: str = "generated-project"
    jwt_audience: str = "generated-project"

settings = Settings()
