from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings

# Engine: manages the actual connection pool to Postgres
engine = create_engine(settings.database_url)

# SessionLocal: factory that creates new database sessions
# autocommit=False  → we control when to commit (explicit transactions)
# autoflush=False   → we control when to send SQL to DB (no surprises)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


# Base: parent class for all ORM models. SQLAlchemy uses this
# to keep a registry of all tables (Repo, Chunk, etc.)
class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency that provides a DB session per request.

    Usage in a route:
        def my_endpoint(db: Session = Depends(get_db)):
            db.query(Repo).all()

    The `yield` makes this a context manager — the session is
    automatically closed when the request finishes, even if it errors.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
