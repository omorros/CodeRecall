from pydantic import BaseModel
from uuid import UUID
from datetime import datetime


# --- Repos ---

class RepoCreate(BaseModel):
    github_url: str


class RepoResponse(BaseModel):
    id: UUID
    github_url: str
    name: str
    status: str
    progress: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --- Chat ---

class ChatRequest(BaseModel):
    message: str
    conversation_id: UUID | None = None


class SourceReference(BaseModel):
    file_path: str
    chunk_id: str
    similarity: float


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceReference]
    conversation_id: UUID


# --- Conversations ---

class MessageResponse(BaseModel):
    id: UUID
    role: str
    content: str
    sources: list[dict] | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationResponse(BaseModel):
    id: UUID
    repo_id: UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationDetailResponse(BaseModel):
    id: UUID
    repo_id: UUID
    created_at: datetime
    messages: list[MessageResponse]
