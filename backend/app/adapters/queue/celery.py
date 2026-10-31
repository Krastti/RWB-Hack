"""Celery доставляет только UUID команды; её состояние хранится в PostgreSQL."""
import logging
from uuid import UUID

from celery import Celery
from celery.signals import worker_process_init
from kombu import Exchange, Queue
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ...workers.execution import StageWorker, WorkerExecutionFailed
from .contracts import LeaseBusy, Orchestrator, QueueConfig, RecoveryHandler
from .postgresql import PostgreSQLQueueStore


EXECUTE_TASK = "rwb.stage.execute.v1"
DISPATCH_TASK = "rwb.queue.dispatch.v1"
logger = logging.getLogger(__name__)


class CeleryQueueAdapter:
    def __init__(self, app: Celery, store: PostgreSQLQueueStore) -> None:
        self.app = app
        self.store = store

    def enqueue(self, transaction: Session, attempt_id: UUID) -> UUID:
        """Публикация в брокер произойдёт только после общего commit."""
        return self.store.enqueue(transaction, attempt_id)

    def publish_due(self, limit: int = 100) -> dict[str, int]:
        published = failed = 0
        for command_id in self.store.reserve_due(limit):
            try:
                self.app.send_task(
                    EXECUTE_TASK, args=[str(command_id)], task_id=str(command_id),
                    queue=self.store.config.queue_name, serializer="json", delivery_mode=2,
                    retry=False,
                )
                published += 1
            except Exception:
                # Окно повторной отправки уже сохранено: сбой брокера не теряет команду.
                logger.exception("Command publication failed: command_id=%s", command_id)
                failed += 1
        return {"published": published, "failed": failed}


def create_worker_app(
    *, broker_url: str, sessions: sessionmaker[Session], orchestrator: Orchestrator,
    recovery: RecoveryHandler, config: QueueConfig | None = None,
    store: PostgreSQLQueueStore | None = None, before_dispatch=None,
) -> Celery:
    if not broker_url:
        raise ValueError("A broker URL is required")
    config = config or (store.config if store is not None else QueueConfig())
    if store is not None and (store.sessions is not sessions or store.config != config):
        raise ValueError("Worker and queue must share sessions and configuration")
    app = Celery("rwb_workers", broker=broker_url)
    exchange = Exchange("rwb", type="direct", durable=True)
    app.conf.update(
        accept_content=["json"], task_serializer="json", result_serializer="json",
        task_ignore_result=True, task_store_errors_even_if_ignored=False,
        task_acks_late=True, task_reject_on_worker_lost=True,
        task_acks_on_failure_or_timeout=False, worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True, broker_connection_timeout=5,
        broker_transport_options={"confirm_publish": True, "socket_timeout": 5,
                                  "socket_connect_timeout": 5},
        task_default_delivery_mode="persistent", task_default_queue=config.queue_name,
        task_queues=(
            Queue(config.queue_name, exchange=exchange, routing_key=config.queue_name, durable=True),
            Queue(config.control_queue, exchange=exchange, routing_key=config.control_queue, durable=True),
        ),
        task_routes={EXECUTE_TASK: {"queue": config.queue_name},
                     DISPATCH_TASK: {"queue": config.control_queue}},
        beat_schedule={"dispatch-stage-commands": {
            "task": DISPATCH_TASK, "schedule": config.dispatch_seconds,
            "options": {"queue": config.control_queue},
        }},
        enable_utc=True, timezone="UTC",
    )
    store = store if store is not None else PostgreSQLQueueStore(sessions, config)
    adapter = CeleryQueueAdapter(app, store)
    worker = StageWorker(store, orchestrator, recovery)
    app.queue_adapter = adapter
    app.stage_worker = worker

    @worker_process_init.connect(weak=False)
    def reset_connection_pool(**kwargs):
        # Prefork-потомок не должен использовать открытые соединения родителя.
        engine = sessions.kw.get("bind")
        if engine is not None:
            engine.dispose(close=False)

    @app.task(name=EXECUTE_TASK, shared=False)
    def execute_stage(command_id: str):
        try:
            parsed_id = UUID(command_id)
        except (ValueError, TypeError, AttributeError):
            return "invalid_command"
        try:
            return worker.execute(parsed_id)
        except LeaseBusy:
            # ACK дубликата: следующее окно доставки уже хранится в PostgreSQL.
            return "lease_busy"
        except SQLAlchemyError:
            logger.exception("Command delivery failed: command_id=%s", parsed_id)
            return "delivery_pending"
        except WorkerExecutionFailed:
            return "recovery_pending"

    @app.task(name=DISPATCH_TASK, shared=False)
    def dispatch_commands():
        intake = before_dispatch() if before_dispatch is not None else None
        result = adapter.publish_due()
        if intake is not None:
            result["intake"] = intake
        return result

    return app
