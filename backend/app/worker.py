"""RQ worker process.

Listens on the Redis queue and picks up ingestion jobs.
Run with: python -m app.worker
"""
import redis
from rq import Worker, Queue
from app.config import settings

if __name__ == "__main__":
    conn = redis.Redis.from_url(settings.redis_url)
    worker = Worker([Queue(connection=conn)], connection=conn)
    worker.work()
