import threading
import logging
import time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

_schedulers = {}
_lock = threading.Lock()

DEFAULT_INTERVAL_MINUTES = 360


def _clamp_interval(interval_minutes):
    # A 0 or NULL value would make the wait loop spin without sleeping, so the
    # scheduler never accepts anything below one minute regardless of what the
    # API or an old row hands it.
    try:
        return max(1, int(interval_minutes))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_MINUTES


class StoreScheduler:
    # How often the wait loop re-checks the interval, so an interval edited on a
    # running scheduler takes effect this cycle instead of the next one.
    POLL_SECONDS = 15

    def __init__(self, store_id, interval_minutes, pg):
        self.store_id = store_id
        self.interval_minutes = _clamp_interval(interval_minutes)
        self.pg = pg
        self._stop_event = threading.Event()
        self._thread = None
        self.running = False
        self.last_run_at = None
        self._wait_started_at = None

    def _loop(self):
        from app.sync_engine import run_sync

        logger.info(
            "Scheduler started for store %d (every %d minutes)",
            self.store_id,
            self.interval_minutes,
        )
        while not self._stop_event.is_set():
            try:
                result = run_sync(self.store_id, self.pg)
                self.last_run_at = datetime.now(timezone.utc)
                logger.info(
                    "Scheduled sync for store %d: %s", self.store_id, result["status"]
                )
            except Exception as e:
                logger.error(
                    "Scheduler error for store %d: %s", self.store_id, str(e)
                )

            self._wait_for_next_run()

        logger.info("Scheduler stopped for store %d", self.store_id)

    def _wait_for_next_run(self):
        # Waits one full interval measured from the end of the run, re-reading
        # self.interval_minutes as it goes so set_interval() applies within
        # POLL_SECONDS instead of after the old interval elapses.
        self._wait_started_at = datetime.now(timezone.utc)
        self._write_next_sync_at()
        wait_start = time.monotonic()
        while not self._stop_event.is_set():
            remaining = wait_start + self.interval_minutes * 60 - time.monotonic()
            if remaining <= 0:
                return
            self._stop_event.wait(min(remaining, self.POLL_SECONDS))

    def _write_next_sync_at(self):
        if self._wait_started_at is None:
            return
        next_sync = self._wait_started_at + timedelta(minutes=self.interval_minutes)
        try:
            self.pg.update_store(
                self.store_id, {"next_sync_at": next_sync.isoformat()}
            )
        except Exception as e:
            logger.error(
                "Could not update next_sync_at for store %d: %s", self.store_id, str(e)
            )

    def set_interval(self, interval_minutes):
        self.interval_minutes = _clamp_interval(interval_minutes)
        self._write_next_sync_at()
        logger.info(
            "Scheduler interval for store %d set to %d minutes",
            self.store_id,
            self.interval_minutes,
        )

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


def start_scheduler(store_id, interval_minutes, pg):
    with _lock:
        if store_id in _schedulers and _schedulers[store_id].running:
            return False
        scheduler = StoreScheduler(store_id, interval_minutes, pg)
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


def update_interval(store_id, interval_minutes):
    with _lock:
        scheduler = _schedulers.get(store_id)
        if not scheduler or not scheduler.running:
            return False
        scheduler.set_interval(interval_minutes)
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
                "interval_minutes": scheduler.interval_minutes,
                "last_run_at": (
                    scheduler.last_run_at.isoformat() if scheduler.last_run_at else None
                ),
            }
        return statuses
