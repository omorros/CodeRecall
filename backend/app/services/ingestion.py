import os
import shutil
import uuid
import time
import logging
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import tiktoken
from git import Repo as GitRepo
from sqlalchemy.orm import Session

from app.models import Repo, Chunk
from app.services.embeddings import get_embeddings_batch

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".md", ".json", ".yaml", ".yml",
    ".toml", ".cfg", ".ini", ".txt", ".html", ".css", ".scss", ".sql",
    ".sh", ".bash", ".go", ".rs", ".java", ".c", ".cpp", ".h", ".hpp",
    ".rb", ".php", ".swift", ".kt", ".scala", ".r", ".vue", ".svelte",
    ".ex", ".exs", ".erl", ".hs", ".lua", ".pl", ".pm",
}

MAX_FILE_SIZE = 1_000_000       # skip files larger than 1 MB
CHUNK_TOKEN_LIMIT = 1500        # max tokens per chunk
CHUNK_OVERLAP_TOKENS = 200      # overlap between consecutive chunks
MAX_EMBEDDING_TOKENS = 8000     # text-embedding-3-small limit is 8 191
MAX_BATCH_TOKENS = 100_000      # stay well under OpenAI's 300 K/request limit
CONCURRENT_BATCHES = 5          # how many embedding requests to run in parallel

# Files to always skip (auto-generated / not useful for understanding code)
SKIP_FILENAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "composer.lock",
    "Cargo.lock", "Gemfile.lock", "poetry.lock", ".terraform.lock.hcl",
    ".DS_Store", "thumbs.db",
    ".gitignore", ".gitattributes", ".editorconfig", ".prettierrc",
    ".eslintignore", ".dockerignore", ".npmrc", ".nvmrc",
    "license-policy.toml", ".spellcheck-en.txt",
}

# Filename prefixes/patterns to skip (matched with startswith/endswith)
SKIP_FILENAME_PREFIXES = {".pre-commit", ".eslintrc", ".stylelintrc", ".browserslistrc"}

# Directory names to skip entirely
SKIP_DIRS = {
    ".git", "node_modules", "vendor", "venv", ".venv", "__pycache__",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".next", ".nuxt", "out",
    "coverage", ".coverage", "htmlcov",
    ".idea", ".vscode", ".settings",
    "target",           # Rust / Java build output
    "eggs", "*.egg-info",
    "site-packages",
    "docs",             # generated docs (README.md at root is still kept)
    ".github",          # CI/CD workflows
}

# Path fragments that signal test / fixture files we can skip
SKIP_PATH_PATTERNS = {
    "/test/", "/tests/", "/__tests__/", "/spec/", "/specs/",
    "/test_", "/_test.", ".test.", ".spec.",
    "/fixtures/", "/testdata/", "/mock/", "/mocks/",
    "/e2e/", "/cypress/",
}

# tiktoken encoder for the model family used by text-embedding-3-small
encoding = tiktoken.get_encoding("cl100k_base")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def count_tokens(text: str) -> int:
    return len(encoding.encode(text, disallowed_special=()))


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    tokens = encoding.encode(text, disallowed_special=())
    if len(tokens) <= max_tokens:
        return text
    return encoding.decode(tokens[:max_tokens])


def split_into_chunks(content: str) -> list[dict]:
    """Split file content into chunks of ~1500 tokens with ~200 token overlap."""
    token_count = count_tokens(content)
    if token_count <= CHUNK_TOKEN_LIMIT:
        return [{"content": content, "chunk_index": 0, "token_count": token_count}]

    lines = content.split("\n")
    chunks: list[dict] = []
    current_lines: list[str] = []
    current_tokens = 0
    chunk_index = 0

    for line in lines:
        line_tokens = count_tokens(line + "\n")

        if current_tokens + line_tokens > CHUNK_TOKEN_LIMIT and current_lines:
            chunk_text = "\n".join(current_lines)
            chunks.append({
                "content": chunk_text,
                "chunk_index": chunk_index,
                "token_count": count_tokens(chunk_text),
            })
            chunk_index += 1

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

    if current_lines:
        chunk_text = "\n".join(current_lines)
        chunks.append({
            "content": chunk_text,
            "chunk_index": chunk_index,
            "token_count": count_tokens(chunk_text),
        })

    return chunks


def clone_repo(github_url: str) -> str:
    clone_dir = tempfile.mkdtemp(prefix="coderecall_")
    GitRepo.clone_from(github_url, clone_dir, depth=1)
    return clone_dir


def _should_skip_path(relative: str) -> bool:
    """Return True if the file path matches a skip pattern."""
    rel_lower = "/" + relative.lower()
    for pattern in SKIP_PATH_PATTERNS:
        if pattern in rel_lower:
            return True
    return False


def walk_files(repo_dir: str) -> list[tuple[str, str]]:
    """Walk the repo and return (relative_path, content) for supported files.

    Applies smart filtering: skips vendored dirs, test files, build artifacts,
    lock files, and other non-essential content.
    """
    files = []
    repo_path = Path(repo_dir)

    for file_path in repo_path.rglob("*"):
        if not file_path.is_file():
            continue

        # Skip entire directories
        if any(part in SKIP_DIRS for part in file_path.relative_to(repo_path).parts):
            continue

        if file_path.name in SKIP_FILENAMES:
            continue
        if any(file_path.name.startswith(p) for p in SKIP_FILENAME_PREFIXES):
            continue
        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if file_path.stat().st_size > MAX_FILE_SIZE:
            continue

        relative = str(file_path.relative_to(repo_path)).replace("\\", "/")

        # Skip test / fixture files
        if _should_skip_path(relative):
            continue

        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        if not content.strip():
            continue

        files.append((relative, content))

    return files


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------

def _embed_with_retry(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts with exponential-backoff retry on rate limit."""
    for attempt in range(5):
        try:
            return get_embeddings_batch(texts)
        except Exception as e:
            if "rate_limit" in str(e).lower() or "429" in str(e):
                time.sleep(2 ** attempt)
            else:
                raise
    return get_embeddings_batch(texts)


def _build_batches(all_chunks: list[dict]) -> list[list[dict]]:
    """Split chunks into batches that fit within MAX_BATCH_TOKENS."""
    batches: list[list[dict]] = []
    batch: list[dict] = []
    batch_tokens = 0

    for chunk in all_chunks:
        chunk_tokens = count_tokens(chunk["embed_text"])
        if batch and batch_tokens + chunk_tokens > MAX_BATCH_TOKENS:
            batches.append(batch)
            batch = []
            batch_tokens = 0
        batch.append(chunk)
        batch_tokens += chunk_tokens

    if batch:
        batches.append(batch)
    return batches


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def ingest_repo(db: Session, repo_id: uuid.UUID, github_url: str):
    """Full ingestion pipeline: clone → chunk → embed (concurrent) → store.

    - Smart filtering skips tests, vendored code, build artifacts
    - Embedding batches run concurrently for speed
    - Progress is updated in the DB so the frontend can show a progress bar
    - Each batch is stored immediately to keep memory low
    """
    repo = db.query(Repo).filter(Repo.id == repo_id).first()
    if not repo:
        return

    repo.status = "processing"
    repo.progress = 0
    db.commit()

    clone_dir = None
    try:
        # 1. Clone
        clone_dir = clone_repo(github_url)

        # 2. Walk files + chunk
        files = walk_files(clone_dir)
        if not files:
            repo.status = "failed"
            repo.error_message = "No supported files found in repository"
            db.commit()
            return

        logger.info("Repo %s: %d files found after filtering", repo_id, len(files))

        all_chunks: list[dict] = []
        for file_path, content in files:
            for chunk in split_into_chunks(content):
                embed_text = f"File: {file_path}\n\n{chunk['content']}"
                embed_text = truncate_to_tokens(embed_text, MAX_EMBEDDING_TOKENS)
                all_chunks.append({
                    "file_path": file_path,
                    "content": chunk["content"],
                    "chunk_index": chunk["chunk_index"],
                    "token_count": chunk["token_count"],
                    "embed_text": embed_text,
                })

        if not all_chunks:
            repo.status = "failed"
            repo.error_message = "No supported files found in repository"
            db.commit()
            return

        # 3. Split into token-aware batches
        batches = _build_batches(all_chunks)
        total_batches = len(batches)
        completed = 0

        logger.info("Repo %s: %d chunks in %d batches, embedding concurrently...",
                     repo_id, len(all_chunks), total_batches)

        # 4. Embed concurrently and store each batch as it completes
        def _process_batch(batch_idx: int, batch: list[dict]):
            texts = [c["embed_text"] for c in batch]
            embeddings = _embed_with_retry(texts)
            return batch_idx, batch, embeddings

        with ThreadPoolExecutor(max_workers=CONCURRENT_BATCHES) as pool:
            futures = {
                pool.submit(_process_batch, i, b): i
                for i, b in enumerate(batches)
            }

            for future in as_completed(futures):
                _, batch, embeddings = future.result()

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

                completed += 1
                repo.progress = int((completed / total_batches) * 100)
                db.commit()

                logger.info("  Batch done (%d/%d) — %d%% complete",
                            completed, total_batches, repo.progress)

        logger.info("Repo %s: done — %d chunks stored.", repo_id, len(all_chunks))
        repo.status = "ready"
        repo.progress = 100
        db.commit()

    except Exception as e:
        repo.status = "failed"
        repo.error_message = str(e)[:500]
        db.commit()

    finally:
        if clone_dir and os.path.exists(clone_dir):
            shutil.rmtree(clone_dir, ignore_errors=True)


def ingest_repo_job(repo_id_str: str, github_url: str):
    """Entry point for the RQ worker."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        ingest_repo(db, uuid.UUID(repo_id_str), github_url)
    finally:
        db.close()
