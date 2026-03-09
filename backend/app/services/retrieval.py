from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import text
from openai import OpenAI

from app.config import settings
from app.models import Conversation, Message
from app.services.embeddings import get_embedding

client = OpenAI(api_key=settings.openai_api_key)

TOP_K = 20

SYSTEM_PROMPT = """You are a senior software engineer acting as a code assistant. You answer questions about a GitHub repository based on the code context provided.

Rules:
- Answer based ONLY on the provided code context
- If the context doesn't contain enough information, say so honestly
- Reference specific files and paths when discussing code
- Use code blocks with the appropriate language for code snippets
- When asked "what is this repo" or similar overview questions, look at README files, main entry points, and config files to give a high-level summary first, then details
- When multiple files are relevant, synthesize the information — don't just describe each file separately
- Be concise and precise
- Prefer explaining architecture and purpose over listing file contents"""


def search_similar_chunks(db: Session, repo_id: UUID, query_embedding: list[float], top_k: int = TOP_K) -> list[dict]:
    """Find the top-K most similar chunks using cosine distance.

    Uses a diversity strategy: retrieves more candidates, then picks the best
    chunks while ensuring coverage across different files.
    """
    # Fetch extra candidates so we can diversify
    candidate_k = top_k * 3

    results = db.execute(
        text("""
            SELECT id, file_path, content, chunk_index, token_count,
                   1 - (embedding <=> CAST(:embedding AS vector)) as similarity
            FROM chunks
            WHERE repo_id = CAST(:repo_id AS uuid)
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT :top_k
        """),
        {"repo_id": str(repo_id), "embedding": str(query_embedding), "top_k": candidate_k},
    ).fetchall()

    # Diversify: pick top chunks but limit per-file to avoid one file dominating
    MAX_CHUNKS_PER_FILE = 3
    file_counts: dict[str, int] = {}
    selected = []

    for row in results:
        fp = row.file_path
        if file_counts.get(fp, 0) >= MAX_CHUNKS_PER_FILE:
            continue
        file_counts[fp] = file_counts.get(fp, 0) + 1
        selected.append({
            "chunk_id": str(row.id),
            "file_path": row.file_path,
            "content": row.content,
            "chunk_index": row.chunk_index,
            "similarity": round(float(row.similarity), 4),
        })
        if len(selected) >= top_k:
            break

    return selected


def build_context(chunks: list[dict]) -> str:
    """Format retrieved chunks into a readable context block for the LLM.

    Groups chunks by file for better readability.
    """
    # Group by file path, preserving order of first appearance
    file_chunks: dict[str, list[dict]] = {}
    for chunk in chunks:
        fp = chunk["file_path"]
        if fp not in file_chunks:
            file_chunks[fp] = []
        file_chunks[fp].append(chunk)

    parts = []
    for fp, fc in file_chunks.items():
        # Sort chunks within a file by chunk_index for logical order
        fc.sort(key=lambda c: c["chunk_index"])
        for chunk in fc:
            parts.append(f"--- File: {fp} (chunk {chunk['chunk_index']}, similarity: {chunk['similarity']}) ---\n{chunk['content']}")

    return "\n\n".join(parts)


def get_conversation_history(db: Session, conversation_id: UUID, limit: int = 10) -> list[dict]:
    """Load previous messages so the LLM can follow up on earlier questions."""
    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .limit(limit)
        .all()
    )
    return [{"role": msg.role, "content": msg.content} for msg in messages]


def chat_with_repo(db: Session, repo_id: UUID, user_message: str, conversation_id: UUID | None = None) -> dict:
    """Full RAG pipeline: embed question → search → build prompt → call LLM → save."""

    # 1. Embed the user's question
    query_embedding = get_embedding(user_message)

    # 2. Find the most relevant code chunks (diversified across files)
    chunks = search_similar_chunks(db, repo_id, query_embedding)

    # 3. Build the context from retrieved chunks
    context = build_context(chunks)

    # 4. Get or create a conversation
    if conversation_id:
        conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if not conversation:
            conversation = Conversation(repo_id=repo_id)
            db.add(conversation)
            db.flush()
    else:
        conversation = Conversation(repo_id=repo_id)
        db.add(conversation)
        db.flush()

    # 5. Build the messages array for the LLM
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Include previous messages if continuing a conversation
    if conversation_id:
        history = get_conversation_history(db, conversation.id)
        messages.extend(history)

    # The user's question with the retrieved code context
    user_prompt = f"""Here is the relevant code context from the repository:

{context}

User question: {user_message}"""

    messages.append({"role": "user", "content": user_prompt})

    # 6. Call the LLM
    response = client.chat.completions.create(
        model=settings.chat_model,
        messages=messages,
        temperature=0.1,
        max_tokens=2000,
    )

    answer = response.choices[0].message.content

    # 7. Save messages + source references (deduplicated by file)
    seen_files = set()
    sources = []
    for c in chunks:
        if c["file_path"] not in seen_files:
            sources.append({
                "file_path": c["file_path"],
                "chunk_id": c["chunk_id"],
                "similarity": c["similarity"],
            })
            seen_files.add(c["file_path"])

    user_msg = Message(
        conversation_id=conversation.id,
        role="user",
        content=user_message,
    )
    assistant_msg = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=answer,
        sources=sources,
    )
    db.add(user_msg)
    db.add(assistant_msg)
    db.commit()

    return {
        "answer": answer,
        "sources": sources,
        "conversation_id": conversation.id,
    }
