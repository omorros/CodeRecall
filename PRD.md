# CodeRecall - Product Requirements Document

## Overview

CodeRecall is a RAG (Retrieval-Augmented Generation) application that lets users chat with GitHub repositories. The goal is to learn AI engineering basics: embeddings, vector databases, and LLM context augmentation.

---

## Tech Stack

- **Frontend:** Next.js (App Router), TypeScript, Tailwind CSS
- **Backend:** Python 3.11+, FastAPI
- **Database:** PostgreSQL 16 + pgvector (Docker)
- **Queue:** Redis (Docker) + RQ
- **AI:** OpenAI API (gpt-4o-mini for chat, text-embedding-3-small for embeddings)
- **ORM:** SQLModel (recommended over SQLAlchemy — same author as FastAPI, less boilerplate, Pydantic-native)

---

## Project Structure

```
CodeRecall/
├── docker-compose.yml
├── .env.example
├── .gitignore
├── README.md
├── PRD.md
│
├── backend/
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   ├── script.py.mako
│   │   └── versions/
│   │       └── 001_initial_schema.py
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                 # FastAPI app, CORS, lifespan
│   │   ├── config.py               # Pydantic Settings
│   │   ├── database.py             # Engine, session factory
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── repository.py       # Repository table
│   │   │   ├── document.py         # Document + Chunk (with pgvector embedding) tables
│   │   │   ├── conversation.py     # Conversation + Message tables
│   │   │   └── enums.py            # IngestionStatus, MessageRole
│   │   ├── schemas/                # Pydantic request/response DTOs
│   │   │   ├── __init__.py
│   │   │   ├── repository.py
│   │   │   ├── chat.py
│   │   │   └── conversation.py
│   │   ├── api/                    # FastAPI routers (thin layer)
│   │   │   ├── __init__.py
│   │   │   ├── repositories.py
│   │   │   ├── chat.py
│   │   │   └── conversations.py
│   │   ├── services/               # Business logic
│   │   │   ├── __init__.py
│   │   │   ├── github_service.py   # Clone repos (shallow, --depth 1)
│   │   │   ├── chunking_service.py # Parse + chunk code files
│   │   │   ├── embedding_service.py# OpenAI embeddings API
│   │   │   ├── ingestion_service.py# Orchestrator: clone -> chunk -> embed -> store
│   │   │   ├── retrieval_service.py# pgvector cosine similarity search
│   │   │   └── chat_service.py     # Build prompt + stream LLM response
│   │   ├── workers/
│   │   │   ├── __init__.py
│   │   │   └── ingestion_worker.py # RQ job entry point
│   │   └── utils/
│   │       ├── __init__.py
│   │       └── file_filters.py     # Skip binaries, node_modules, etc.
│   └── tests/
│       └── __init__.py
│
└── frontend/
    ├── package.json
    ├── tsconfig.json
    ├── tailwind.config.ts
    ├── postcss.config.js
    ├── next.config.ts
    └── src/
        ├── app/
        │   ├── layout.tsx
        │   ├── page.tsx             # Repo management landing page
        │   ├── globals.css
        │   └── chat/[repoId]/
        │       └── page.tsx         # Chat interface
        ├── components/
        │   ├── RepoCard.tsx
        │   ├── AddRepoForm.tsx
        │   ├── ChatMessage.tsx
        │   ├── ChatInput.tsx
        │   ├── ChatWindow.tsx
        │   └── SourceChunk.tsx      # Shows referenced code in answers
        ├── lib/
        │   ├── api.ts               # Fetch wrapper for backend
        │   └── types.ts
        └── hooks/
            ├── useChat.ts           # SSE streaming state management
            └── useRepositories.ts
```

---

## Database Schema

### `repositories`
| Column | Type | Notes |
|---|---|---|
| id | UUID | PK |
| github_url | TEXT | Unique, indexed |
| owner, name | TEXT | Extracted from URL |
| default_branch | TEXT | Default "main" |
| ingestion_status | ENUM | pending/cloning/chunking/embedding/completed/failed |
| ingestion_error | TEXT | Nullable |
| total_files, processed_files | INT | For progress tracking |
| last_ingested_at | TIMESTAMP | Nullable |
| created_at, updated_at | TIMESTAMP | |

### `documents`
| Column | Type | Notes |
|---|---|---|
| id | UUID | PK |
| repository_id | UUID | FK -> repositories |
| file_path | TEXT | e.g. "src/utils/parser.py" |
| language | TEXT | Detected from extension |
| content_hash | TEXT | SHA-256, for change detection on re-ingestion |

### `chunks`
| Column | Type | Notes |
|---|---|---|
| id | UUID | PK |
| document_id | UUID | FK -> documents |
| repository_id | UUID | FK -> repositories, indexed |
| content | TEXT | The chunk text (with metadata header prepended) |
| chunk_index | INT | Order within document |
| start_line, end_line | INT | Line range in original file |
| chunk_type | TEXT | "code" (future: "function", "class") |
| metadata_text | TEXT | Searchable summary for potential hybrid search |
| **embedding** | **vector(1536)** | **pgvector column, HNSW indexed** |

### `conversations` + `messages`
Standard chat history. Messages store `role` (user/assistant), `content`, and optionally `context_chunk_ids` (JSON array of chunk UUIDs used as context).

---

## API Endpoints

### Repositories
| Method | Path | Description |
|---|---|---|
| POST | `/api/repositories` | Add repo (body: `{github_url}`) |
| GET | `/api/repositories` | List all repos with status |
| GET | `/api/repositories/{id}` | Single repo details |
| POST | `/api/repositories/{id}/ingest` | Enqueue ingestion job (returns `202`) |
| GET | `/api/repositories/{id}/status` | Poll ingestion progress |
| DELETE | `/api/repositories/{id}` | Remove repo + all data |

### Chat
| Method | Path | Description |
|---|---|---|
| POST | `/api/chat/{repo_id}` | Send message, get **SSE streaming** response |

### Conversations
| Method | Path | Description |
|---|---|---|
| GET | `/api/conversations?repo_id={id}` | List conversations for repo |
| GET | `/api/conversations/{id}` | Full conversation with messages |
| DELETE | `/api/conversations/{id}` | Delete conversation |

---

## Core Flows

### Ingestion Pipeline (async via RQ)

```
POST /api/repositories/{id}/ingest
  -> Enqueue RQ job -> Worker picks it up:
     1. CLONING:   git clone --depth 1 (shallow)
     2. CHUNKING:  Walk files -> filter -> split into ~50-line chunks with 10-line overlap
     3. EMBEDDING: Batch embed via OpenAI (100 chunks per API call)
     4. STORE:     Insert Documents + Chunks with vectors into PostgreSQL
     5. COMPLETED: Update repo status
```

**Chunking strategy:** Semantic line chunking — split on blank lines near the target size, with overlap. Each chunk gets a metadata header prepended:
```
# File: src/auth/middleware.py
# Language: python
# Lines: 15-65
```
This header dramatically improves retrieval quality because the embedding captures file path context.

### RAG Chat Flow

```
User sends message
  -> Embed query via text-embedding-3-small
  -> pgvector cosine similarity search (top-k=10 chunks)
  -> Build prompt: system prompt + retrieved chunks as context + conversation history + user message
  -> Stream response from gpt-4o-mini via SSE
  -> Return source references (file paths + line numbers) at end of stream
```

---

## Implementation Phases

### Phase 1: Infrastructure + Models ✅
- `docker-compose.yml` (PostgreSQL + pgvector, Redis)
- Backend scaffold: `pyproject.toml`, `config.py`, `database.py`, `main.py`
- All SQLModel models + enums
- Alembic setup + initial migration
- `.env.example`, `.gitignore`

### Phase 2: Ingestion Pipeline ✅
- `github_service.py` (clone)
- `file_filters.py` (walk + filter)
- `chunking_service.py` (chunk with metadata headers)
- `embedding_service.py` (OpenAI batch embedding)
- `ingestion_service.py` (orchestrator)
- Repository API endpoints + RQ worker

### Phase 3: RAG Chat ✅
- `retrieval_service.py` (pgvector similarity search)
- `chat_service.py` (prompt building + LLM streaming)
- Chat API endpoint with SSE
- Conversation/message persistence

### Phase 4: Frontend ✅
- Next.js project scaffold
- Landing page (repo management + ingestion progress polling)
- Chat page (streaming messages + source references)
- `useChat` hook for SSE consumption
- `useRepositories` hook for repo management

### Phase 5: Testing & Verification (Next)
- `docker compose up -d`, connect to PostgreSQL, confirm `CREATE EXTENSION vector` works
- `alembic upgrade head` to apply migrations
- Add a small repo, trigger ingestion, confirm vectors stored
- Test RAG chat with curl
- Test frontend end-to-end

### Phase 6: Polish (Future)
- Token counting with `tiktoken` to respect context limits
- Change detection on re-ingestion (content_hash comparison)
- Hybrid search (keyword + vector with Reciprocal Rank Fusion)
- AST-based chunking via tree-sitter

---

## Key Design Decisions

1. **SQLModel over SQLAlchemy** — less boilerplate, native Pydantic integration, same author as FastAPI
2. **Metadata headers on chunks** — prepending file path + language to each chunk before embedding is the highest-impact trick for code RAG quality
3. **HNSW index over IVFFlat** — better recall, no retraining needed as data grows
4. **SSE over WebSockets for streaming** — simpler, unidirectional, built on HTTP, perfect for LLM streaming
5. **Shallow clone (`--depth 1`)** — only need latest code state, dramatically faster
6. **Embedding on Chunk table directly** — each chunk has exactly one embedding, so a single query returns both the match and all metadata. Fewer JOINs
7. **Next.js rewrites** — proxy `/api/*` to the FastAPI backend to avoid CORS issues during development
