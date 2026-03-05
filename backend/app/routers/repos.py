import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Repo
from app.schemas import RepoCreate, RepoResponse
from app.services.ingestion import ingest_repo

router = APIRouter(prefix="/repos", tags=["repos"])


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

    # Run ingestion synchronously for now (Phase 4 moves this to a background worker)
    ingest_repo(db, repo.id, github_url)
    db.refresh(repo)

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
