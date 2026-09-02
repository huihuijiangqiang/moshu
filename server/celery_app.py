"""
Celery application configuration
"""
from celery import Celery

from config import settings

celery_app = Celery(
    "moshu_consistency",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    # A long chapter may require multiple 180-second streamed requests and retries.
    # Keep a finite ceiling, but do not kill valid chunked work after one slow retry.
    task_time_limit=1800,
    task_soft_time_limit=1740,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
    imports=("tasks.consistency", "tasks.codex"),
    task_routes={
        "consistency.dispatch_outbox": {"queue": "outbox"},
        "consistency.process_body_saved": {"queue": "consistency"},
        "consistency.extract_claims": {"queue": "consistency"},
        "consistency.generate_summary": {"queue": "consistency"},
        "consistency.scan_rules": {"queue": "consistency"},
        "codex.backfill_embeddings": {"queue": "consistency"},
        "codex.recover_stale_embedding_jobs": {"queue": "outbox"},
    },
    beat_schedule={
        "dispatch-consistency-outbox": {
            "task": "consistency.dispatch_outbox",
            "schedule": 2.0,
            "kwargs": {"batch_size": 20},
        },
        "recover-stale-embedding-jobs": {
            "task": "codex.recover_stale_embedding_jobs",
            "schedule": 60.0,
        },
    },
)

# Tasks are registered explicitly through ``imports`` above.  Using
# autodiscover_tasks(["tasks"]) would look for a Django-style tasks.tasks module.
