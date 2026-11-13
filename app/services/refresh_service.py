import threading

from app.core.errors import RefreshInProgress
from app.ingestion.pipeline import IngestionPipeline


class RefreshService:
    """Serialises refreshes so two admins (or startup + admin) can't run ingestion at once."""

    def __init__(self, pipeline: IngestionPipeline):
        self.pipeline = pipeline
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def refresh(self, *, force: bool = False, trigger: str = "api") -> dict:
        if not self._lock.acquire(blocking=False):
            raise RefreshInProgress()
        try:
            return self.pipeline.run(force=force, trigger=trigger)
        finally:
            self._lock.release()
