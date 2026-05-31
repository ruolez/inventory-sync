import threading
import logging
import time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_schedulers = {}
_lock = threading.Lock()


class StoreScheduler:
    def __init__(self, store_id, interval_hours, pg):
        self.store_id = store_id
        self.interval_hours = interval_hours
        self.pg = pg
        self._stop_event = threading.Event()
        self._thread = None
        self.running = False
        self.last_run_at = None

    def _loop(self):
        from app.sync_engine import run_sync

        logger.info(
            "Scheduler started for store %d (every %d hours)",
            self.store_id,
            self.interval_hours,
        )
        while not self._stop_event.is_set():
            try:
                next_sync = datetime.now(timezone.utc) + timedelta(
                    hours=self.interval_hours
                )
                self.pg.update_store(
                    self.store_id, {"next_sync_at": next_sync.isoformat()}
                )

                result = run_sync(self.store_id, self.pg)
                self.last_run_at = datetime.now(timezone.utc)
                logger.info(
                    "Scheduled sync for store %d: %s", self.store_id, result["status"]
                )
            except Exception as e:
                logger.error(
                    "Scheduler error for store %d: %s", self.store_id, str(e)
                )

            sleep_seconds = self.interval_hours * 3600
            self._stop_event.wait(sleep_seconds)

        logger.info("Scheduler stopped for store %d", self.store_id)

    def start(self):
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.running = True

    def stop(self):
        if not self.running:
            return
        self._stop_event.set()
        self.running = False
        self.pg.update_store(self.store_id, {"next_sync_at": None})


def start_scheduler(store_id, interval_hours, pg):
    with _lock:
        if store_id in _schedulers and _schedulers[store_id].running:
            return False
        scheduler = StoreScheduler(store_id, interval_hours, pg)
        scheduler.start()
        _schedulers[store_id] = scheduler
        pg.update_store(store_id, {"sync_enabled": True})
        return True


def stop_scheduler(store_id, pg):
    with _lock:
        scheduler = _schedulers.get(store_id)
        if not scheduler or not scheduler.running:
            return False
        scheduler.stop()
        pg.update_store(store_id, {"sync_enabled": False})
        return True


_purge_stop = threading.Event()
_purge_thread = None


def _purge_loop(pg, initial_delay_seconds, interval_hours):
    # Initial delay lets the background idx_product_logs_created build (also
    # spawned at startup) finish, so the first purge uses the index.
    if _purge_stop.wait(initial_delay_seconds):
        return
    logger.info("Log purge started (every %d hours)", interval_hours)
    while not _purge_stop.is_set():
        try:
            deleted = pg.purge_old_product_logs(stop_event=_purge_stop)
            logger.info("Log purge removed %d old product_logs rows", deleted)
        except Exception as e:
            logger.error("Log purge failed: %s", str(e))
        _purge_stop.wait(interval_hours * 3600)
    logger.info("Log purge stopped")


def start_log_purge_scheduler(pg, initial_delay_seconds=120, interval_hours=24):
    global _purge_thread
    if _purge_thread and _purge_thread.is_alive():
        return False
    _purge_stop.clear()
    _purge_thread = threading.Thread(
        target=_purge_loop,
        args=(pg, initial_delay_seconds, interval_hours),
        daemon=True,
    )
    _purge_thread.start()
    return True


def get_all_statuses():
    with _lock:
        statuses = {}
        for store_id, scheduler in _schedulers.items():
            statuses[store_id] = {
                "running": scheduler.running,
                "interval_hours": scheduler.interval_hours,
                "last_run_at": (
                    scheduler.last_run_at.isoformat() if scheduler.last_run_at else None
                ),
            }
        return statuses
