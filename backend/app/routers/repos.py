import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import redis
from rq import Queue

from app.database import get_db
from app.config import settings
from app.models import Repo
from app.schemas import RepoCreate, RepoResponse

router = APIRouter(prefix="/repos", tags=["repos"])


def get_queue() -> Queue:
    return Queue(connection=redis.Redis.from_url(settings.redis_url))


def parse_github_url(url: str) -> str:
    """Extract 'owner/repo' from a GitHub URL.

    Accepts formats like:
      https://github.com/owner/repo
      https://github.com/owner/repo.git
      github.com/owner/repo
    """
    match = re.search(r"github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", url.strip())
    if not match:
        raise ValueError("Invalid GitHub URL")
    return match.group(1)


@router.post("", response_model=RepoResponse, status_code=201)
def create_repo(body: RepoCreate, db: Session = Depends(get_db)):
    # Validate and normalize the URL
    try:
        name = parse_github_url(body.github_url)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid GitHub URL")

    github_url = f"https://github.com/{name}.git"

    # Don't ingest the same repo twice
    existing = db.query(Repo).filter(Repo.github_url == github_url).first()
    if existing:
        return existing

    # Create repo record
    repo = Repo(github_url=github_url, name=name, status="pending")
    db.add(repo)
    db.commit()
    db.refresh(repo)

    # Queue ingestion as a background job — returns immediately
    q = get_queue()
    q.enqueue("app.services.ingestion.ingest_repo_job", str(repo.id), github_url, job_timeout=1800)

    return repo


@router.get("", response_model=list[RepoResponse])
def list_repos(db: Session = Depends(get_db)):
    return db.query(Repo).order_by(Repo.created_at.desc()).all()


@router.get("/{repo_id}", response_model=RepoResponse)
def get_repo(repo_id: UUID, db: Session = Depends(get_db)):
    repo = db.query(Repo).filter(Repo.id == repo_id).first()
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    return repo


@router.delete("/{repo_id}", status_code=204)
def delete_repo(repo_id: UUID, db: Session = Depends(get_db)):
    repo = db.query(Repo).filter(Repo.id == repo_id).first()
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    db.delete(repo)
    db.commit()
