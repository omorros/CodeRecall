from openai import OpenAI
from app.config import settings

client = OpenAI(api_key=settings.openai_api_key)


def get_embedding(text: str) -> list[float]:
    """Embed a single text. Returns a list of 1536 floats."""
    response = client.embeddings.create(
        input=text,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )
    return response.data[0].embedding


def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Embed multiple texts in one API call. Returns a list of vectors."""
    response = client.embeddings.create(
        input=texts,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )
    return [item.embedding for item in response.data]
