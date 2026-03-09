import os
import shutil
import uuid
import time
import logging
import tempfile
from pathlib import Path

import tiktoken
from git import Repo as GitRepo
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.models import Repo, Chunk
from app.services.embeddings import get_embeddings_batch

# File types we know how to process
SUPPORTED_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".md", ".json", ".yaml", ".yml",
    ".toml", ".cfg", ".ini", ".txt", ".html", ".css", ".scss", ".sql",
    ".sh", ".bash", ".go", ".rs", ".java", ".c", ".cpp", ".h", ".hpp",
    ".rb", ".php", ".swift", ".kt", ".scala", ".r", ".vue", ".svelte",
    ".ex", ".exs", ".erl", ".hs", ".lua", ".pl", ".pm",
}

MAX_FILE_SIZE = 1_000_000      # skip files larger than 1MB
CHUNK_TOKEN_LIMIT = 1500       # max tokens per chunk
CHUNK_OVERLAP_TOKENS = 200     # overlap between consecutive chunks
EMBEDDING_BATCH_SIZE = 100     # how many chunks to embed per API call
MAX_EMBEDDING_TOKENS = 8000    # text-embedding-3-small limit is 8191

# Files to skip even if their extension is supported
SKIP_FILENAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "composer.lock",
    "Cargo.lock", "Gemfile.lock", "poetry.lock", ".terraform.lock.hcl",
}

# tiktoken encoder for the model family used by text-embedding-3-small
encoding = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Count how many tokens a string uses."""
    return len(encoding.encode(text, disallowed_special=()))


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to fit within a token limit."""
    tokens = encoding.encode(text, disallowed_special=())
    if len(tokens) <= max_tokens:
        return text
    return encoding.decode(tokens[:max_tokens])


def split_into_chunks(content: str) -> list[dict]:
    """Split file content into chunks of ~1500 tokens with ~200 token overlap.

    Small files (<=1500 tokens) become a single chunk.
    Large files are split on line boundaries to keep code readable.
    Overlap ensures context isn't lost at chunk boundaries.
    """
    token_count = count_tokens(content)
    if token_count <= CHUNK_TOKEN_LIMIT:
        return [{"content": content, "chunk_index": 0, "token_count": token_count}]

    lines = content.split("\n")
    chunks = []
    current_lines = []
    current_tokens = 0
    chunk_index = 0

    for line in lines:
        line_tokens = count_tokens(line + "\n")

        # If adding this line exceeds the limit, save current chunk
        if current_tokens + line_tokens > CHUNK_TOKEN_LIMIT and current_lines:
            chunk_text = "\n".join(current_lines)
            chunks.append({
                "content": chunk_text,
                "chunk_index": chunk_index,
                "token_count": count_tokens(chunk_text),
            })
            chunk_index += 1

            # Keep trailing lines as overlap for the next chunk
            overlap_tokens = 0
            overlap_start = len(current_lines)
            for i in range(len(current_lines) - 1, -1, -1):
                overlap_tokens += count_tokens(current_lines[i] + "\n")
                if overlap_tokens >= CHUNK_OVERLAP_TOKENS:
                    overlap_start = i
                    break
            current_lines = current_lines[overlap_start:]
            current_tokens = sum(count_tokens(l + "\n") for l in current_lines)

        current_lines.append(line)
        current_tokens += line_tokens

    # Don't forget the last chunk
    if current_lines:
        chunk_text = "\n".join(current_lines)
        chunks.append({
            "content": chunk_text,
            "chunk_index": chunk_index,
            "token_count": count_tokens(chunk_text),
        })

    return chunks


def clone_repo(github_url: str) -> str:
    """Clone a repo into a temp directory. Returns the path.
    depth=1 means only the latest commit (no full history), much faster.
    """
    clone_dir = tempfile.mkdtemp(prefix="coderecall_")
    GitRepo.clone_from(github_url, clone_dir, depth=1)
    return clone_dir


def walk_files(repo_dir: str) -> list[tuple[str, str]]:
    """Walk the repo and return (relative_path, content) for supported files."""
    files = []
    repo_path = Path(repo_dir)

    for file_path in repo_path.rglob("*"):
        if not file_path.is_file():
            continue
        if ".git" in file_path.parts:
            continue
        if file_path.name in SKIP_FILENAMES:
            continue
        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if file_path.stat().st_size > MAX_FILE_SIZE:
            continue

        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        if not content.strip():
            continue

        relative = str(file_path.relative_to(repo_path)).replace("\\", "/")
        files.append((relative, content))

    return files


def _embed_with_retry(texts: list[str], batch_num: int) -> list[list[float]]:
    """Embed a list of texts with retry on rate limit."""
    batch_tok = sum(count_tokens(t) for t in texts)
    logger.info("  Batch %d: %d texts, ~%d tokens", batch_num, len(texts), batch_tok)
    for attempt in range(5):
        try:
            return get_embeddings_batch(texts)
        except Exception as e:
            if "rate_limit" in str(e).lower() or "429" in str(e):
                wait = 2 ** attempt
                logger.warning("  Rate limited, retrying in %ds...", wait)
                time.sleep(wait)
            else:
                raise
    return get_embeddings_batch(texts)


def ingest_repo(db: Session, repo_id: uuid.UUID, github_url: str):
    """Full ingestion pipeline: clone, chunk, embed, store.

    Processes files in streaming batches to keep memory usage low.
    Each batch is embedded and written to DB before moving to the next.
    """
    repo = db.query(Repo).filter(Repo.id == repo_id).first()
    if not repo:
        return

    repo.status = "processing"
    db.commit()

    # OpenAI limit: 300K tokens per request, 8191 per individual text.
    # Use 100K to stay well under the limit.
    MAX_BATCH_TOKENS = 100_000

    clone_dir = None
    try:
        # 1. Clone
        clone_dir = clone_repo(github_url)

        # 2. Walk files
        files = walk_files(clone_dir)
        if not files:
            repo.status = "failed"
            repo.error_message = "No supported files found in repository"
            db.commit()
            return

        logger.info("Repo %s: %d files found, chunking and embedding...", repo_id, len(files))

        # 3. Stream: chunk → embed → store in batches
        batch: list[dict] = []
        batch_tokens = 0
        batch_num = 0
        total_chunks = 0

        def flush_batch():
            """Embed the current batch and write to DB."""
            nonlocal batch, batch_tokens, batch_num, total_chunks
            if not batch:
                return
            texts = [c["embed_text"] for c in batch]
            embeddings = _embed_with_retry(texts, batch_num)

            db_chunks = []
            for chunk, embedding in zip(batch, embeddings):
                db_chunks.append(Chunk(
                    repo_id=repo_id,
                    file_path=chunk["file_path"],
                    content=chunk["content"],
                    chunk_index=chunk["chunk_index"],
                    token_count=chunk["token_count"],
                    embedding=embedding,
                ))
            db.bulk_save_objects(db_chunks)
            db.commit()

            total_chunks += len(batch)
            logger.info("  Stored %d chunks (total: %d)", len(batch), total_chunks)
            batch = []
            batch_tokens = 0
            batch_num += 1

        for file_path, content in files:
            chunks = split_into_chunks(content)
            for chunk in chunks:
                embed_text = f"File: {file_path}\n\n{chunk['content']}"
                embed_text = truncate_to_tokens(embed_text, MAX_EMBEDDING_TOKENS)
                chunk_tokens = count_tokens(embed_text)

                if batch and batch_tokens + chunk_tokens > MAX_BATCH_TOKENS:
                    flush_batch()

                batch.append({
                    "file_path": file_path,
                    "content": chunk["content"],
                    "chunk_index": chunk["chunk_index"],
                    "token_count": chunk["token_count"],
                    "embed_text": embed_text,
                })
                batch_tokens += chunk_tokens

        flush_batch()

        if total_chunks == 0:
            repo.status = "failed"
            repo.error_message = "No supported files found in repository"
            db.commit()
            return

        logger.info("Repo %s: done — %d chunks stored.", repo_id, total_chunks)
        repo.status = "ready"
        db.commit()

    except Exception as e:
        repo.status = "failed"
        repo.error_message = str(e)[:500]
        db.commit()

    finally:
        # Always clean up the cloned repo
        if clone_dir and os.path.exists(clone_dir):
            shutil.rmtree(clone_dir, ignore_errors=True)


def ingest_repo_job(repo_id_str: str, github_url: str):
    """Entry point for the RQ worker.

    RQ runs this in a separate process, so it can't use FastAPI's
    request-scoped DB session. We create our own session here.
    """
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        ingest_repo(db, uuid.UUID(repo_id_str), github_url)
    finally:
        db.close()
