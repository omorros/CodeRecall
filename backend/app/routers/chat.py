from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Repo, Conversation, Message
from app.schemas import (
    ChatRequest,
    ChatResponse,
    SourceReference,
    ConversationResponse,
    ConversationDetailResponse,
    MessageResponse,
)
from app.services.retrieval import chat_with_repo

router = APIRouter(tags=["chat"])


@router.post("/repos/{repo_id}/chat", response_model=ChatResponse)
def chat(repo_id: UUID, body: ChatRequest, db: Session = Depends(get_db)):
    repo = db.query(Repo).filter(Repo.id == repo_id).first()
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    if repo.status != "ready":
        raise HTTPException(status_code=400, detail=f"Repo is not ready (status: {repo.status})")

    result = chat_with_repo(db, repo_id, body.message, body.conversation_id)

    return ChatResponse(
        answer=result["answer"],
        sources=[
            SourceReference(file_path=s["file_path"], chunk_id=s["chunk_id"], similarity=s["similarity"])
            for s in result["sources"]
        ],
        conversation_id=result["conversation_id"],
    )


@router.get("/repos/{repo_id}/conversations", response_model=list[ConversationResponse])
def list_conversations(repo_id: UUID, db: Session = Depends(get_db)):
    repo = db.query(Repo).filter(Repo.id == repo_id).first()
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    return (
        db.query(Conversation)
        .filter(Conversation.repo_id == repo_id)
        .order_by(Conversation.created_at.desc())
        .all()
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
def get_conversation(conversation_id: UUID, db: Session = Depends(get_db)):
    conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .all()
    )

    return ConversationDetailResponse(
        id=conversation.id,
        repo_id=conversation.repo_id,
        created_at=conversation.created_at,
        messages=[
            MessageResponse(
                id=msg.id,
                role=msg.role,
                content=msg.content,
                sources=msg.sources,
                created_at=msg.created_at,
            )
            for msg in messages
        ],
    )
