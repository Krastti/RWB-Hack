"""Production entrypoint: celery -A app.workers.pipeline:app worker -B.

Server-managed PIPELINE_POLICY_FILE is frozen at HTTP Run creation. Both API and
worker use DATABASE_URL; LLM configuration is required only by the worker.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from ..agents.llm import LLMConfig
from ..services.runtime import compose_runtime
from ..services.run_control.models import Limits
from ..services.pipeline import compose_pipeline


def create_pipeline_worker(*, sessions=None, config=None, broker_url=None, transports=None):
    if sessions is None:
        url = make_url(os.environ['DATABASE_URL'])
        if url.get_backend_name() != 'postgresql':
            raise ValueError('DATABASE_URL must use PostgreSQL')
        sessions = sessionmaker(create_engine(url.set(drivername='postgresql+psycopg'), pool_pre_ping=True, hide_parameters=True))
    config = config or LLMConfig(base_url=os.environ['LLM_BASE_URL'], model=os.environ['LLM_MODEL'],
        api_key=os.environ['LLM_API_KEY'], max_output_tokens=int(os.environ.get('LLM_MAX_OUTPUT_TOKENS', '30000')),
        timeout_seconds=float(os.environ.get('LLM_TIMEOUT_SECONDS', '60')),
        response_format=os.environ.get('LLM_RESPONSE_FORMAT', 'json_schema'))
    runtime = compose_runtime(sessions, retry_limits=Limits(max_calls=int(os.environ.get('RUN_MAX_CALLS', '60')),
        max_output_tokens=int(os.environ.get('RUN_MAX_OUTPUT_TOKENS', '180000'))))
    return compose_pipeline(runtime, config, broker_url=broker_url or os.environ['CELERY_BROKER_URL'], transports=transports)


def __getattr__(name):
    if name == 'app':
        instance = create_pipeline_worker()
        globals()['app'] = instance
        return instance
    raise AttributeError(name)
