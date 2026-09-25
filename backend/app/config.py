from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_name: str = "portfolioai"
    database_user: str = "portfolioai"
    database_password: str = "portfolioai_dev"
    database_host: str = "localhost"
    database_port: int = 5432

    model_config = SettingsConfigDict(env_file="../.env", env_file_encoding="utf-8", extra="ignore")

    @property
    def database_url(self) -> str:
        return (f"postgresql+psycopg2://{self.database_user}:{self.database_password}"
                f"@{self.database_host}:{self.database_port}/{self.database_name}")


settings = Settings()
