"""Durable local checkpoint adapter; workers may inject another LangGraph saver."""

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver


@contextmanager
def open_checkpoints(path: str | Path) -> Iterator[SqliteSaver]:
    """Open an existing-directory SQLite file; never silently use volatile RAM."""
    path = Path(path)
    if not path.is_absolute() or not path.parent.is_dir():
        raise ValueError("CHECKPOINT_PATH_MUST_BE_ABSOLUTE")
    with SqliteSaver.from_conn_string(str(path)) as saver:
        saver.serde = JsonPlusSerializer(
            pickle_fallback=False,
            allowed_msgpack_modules=[("langgraph.types", "Interrupt")],
        )
        saver.setup()
        yield saver
