# worker.py (robust: handles sync or async build_index, and keeps process alive)
import os
import time
import logging
import inspect
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker")


def run_sync_or_async(fn):
    """
    Call fn() and if it returns an awaitable, run/await it properly.
    Returns the final result (or None).
    """
    try:
        result = fn()
    except Exception:
        # If calling the function itself raises (e.g., import-time error), re-raise
        raise

    # If result is awaitable (a coroutine) run it
    if inspect.isawaitable(result):
        # Use asyncio.run so this works from a synchronous context
        return asyncio.run(result)
    return result


def main():
    logger.info("Worker start: running indexing tasks")
    try:
        from website_index import build_index
    except Exception:
        logger.exception("Worker import failed; ensure ML deps installed")
        return

    try:
        run_sync_or_async(build_index)
        logger.info("Indexing finished successfully")
    except Exception:
        logger.exception("Indexing failed")
        # continue to keep worker alive so you can inspect logs, or exit if desired
    finally:
        logger.info("Worker finished initial tasks")

    # Optionally keep the process alive so the worker can serve or background tasks run.
    # Set KEEP_ALIVE=0 to allow the process to exit after tasks complete.
    keep_alive = os.getenv("KEEP_ALIVE", "1")
    if keep_alive and keep_alive not in ("0", "false", "False"):
        logger.info("KEEP_ALIVE enabled — keeping process alive. Ctrl-C to stop.")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            logger.info("Worker interrupted, exiting.")


if __name__ == "__main__":
    main()
