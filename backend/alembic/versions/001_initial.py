"""Initial schema: repos, chunks, conversations, messages + pgvector

Revision ID: 001
Revises:
Create Date: 2026-03-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable the pgvector extension — must come before any VECTOR columns
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # --- repos ---
    op.create_table(
        "repos",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("github_url", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("github_url"),
    )

    # --- chunks ---
    op.create_table(
        "chunks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("repo_id", sa.UUID(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer()),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["repo_id"], ["repos.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_chunks_repo_id", "chunks", ["repo_id"])

    # --- conversations ---
    op.create_table(
        "repos_conversations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("repo_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["repo_id"], ["repos.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_conversations_repo_id", "repos_conversations", ["repo_id"])

    # --- messages ---
    op.create_table(
        "messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sources", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["conversation_id"], ["repos_conversations.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("repos_conversations")
    op.drop_table("chunks")
    op.drop_table("repos")
    op.execute("DROP EXTENSION IF EXISTS vector")
