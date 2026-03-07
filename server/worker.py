# worker.py — run indexing immediately at startup, then refresh on schedule
import os
import time
import logging
import inspect
import asyncio
import signal
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger("worker")

# lifecycle info
logger.info("PID %s cwd=%s", os.getpid(), os.getcwd())

def handle_signal(sig, frame):
    logger.warning("Received signal %s, exiting", sig)
    sys.exit(0)

for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    try:
        signal.signal(s, handle_signal)
    except Exception:
        # some platforms may not support all signals
        pass


def run_sync_or_async(fn):
    try:
        result = fn()
    except Exception:
        raise
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def main():
    try:
        import website_index
        build_index = website_index.build_index
        default_interval = getattr(website_index, "REFRESH_INTERVAL", None)
        logger.info("Imported website_index")
    except Exception:
        logger.exception("Failed to import website_index; exiting")
        return

    refresh_interval = int(os.getenv("REFRESH_INTERVAL", str(default_interval or 86400)))
    logger.info("Starting: will run indexing now, then every %s seconds", refresh_interval)

    # First run immediately
    try:
        run_sync_or_async(build_index)
        logger.info("Initial indexing run completed")
    except Exception:
        logger.exception("Initial indexing run failed")

    # Subsequent runs after sleeping refresh_interval between runs
    while True:
        logger.info("Sleeping %s seconds before next run", refresh_interval)
        try:
            time.sleep(refresh_interval)
        except KeyboardInterrupt:
            logger.info("Interrupted, exiting")
            break

        try:
            run_sync_or_async(build_index)
            logger.info("Indexing run completed")
        except Exception:
            logger.exception("Indexing run failed")


if __name__ == "__main__":
    main()
