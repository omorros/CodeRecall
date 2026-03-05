from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # PostgreSQL connection string
    database_url: str = "postgresql://coderecall:coderecall@localhost:5432/coderecall"

    # Redis connection string
    redis_url: str = "redis://localhost:6379/0"

    # OpenAI
    openai_api_key: str = ""
    chat_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # Temp directory for cloning repos
    clone_dir: str = "./cloned_repos"

    # CORS origins allowed to call the API
    backend_cors_origins: list[str] = ["http://localhost:3000"]

    model_config = {"env_file": ".env"}


settings = Settings()
