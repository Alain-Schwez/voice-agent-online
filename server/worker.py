# worker.py (robust: await async build_index, keep process alive, skip reindex if recent)
import os
import time
import logging
import inspect
import asyncio
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker")

from pathlib import Path
# Marker: use the FAISS index file that website_index.save_index() writes
INDEX_MARKER = Path(os.getenv("INDEX_MARKER_PATH", "vector_index.faiss"))
# TTL for considering index fresh (seconds). Default matches website_index.REFRESH_INTERVAL (86400s)
INDEX_TTL_SECONDS = int(os.getenv("INDEX_TTL_SECONDS", "86400"))
# Keep process alive after initial tasks (set KEEP_ALIVE=0 to exit)
KEEP_ALIVE = os.getenv("KEEP_ALIVE", "1") not in ("0", "false", "False")

def is_index_fresh() -> bool:
    if not INDEX_MARKER.exists():
        return False
    try:
        mtime = INDEX_MARKER.stat().st_mtime
        age = time.time() - mtime
        logger.info("Index marker age: %.0f seconds", age)
        return age <= INDEX_TTL_SECONDS
    except Exception:
        logger.exception("Failed checking index marker")
        return False


def mark_index_created():
    try:
        INDEX_MARKER.parent.mkdir(parents=True, exist_ok=True)
        INDEX_MARKER.write_text(str(time.time()))
    except Exception:
        logger.exception("Failed writing index marker")


def run_sync_or_async(fn):
    # call the function and if it returns awaitable, run it properly
    try:
        result = fn()
    except Exception:
        raise
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def main():
    logger.info("Worker start: running indexing tasks")
    try:
        from website_index import build_index
    except Exception:
        logger.exception("Worker import failed; ensure ML deps installed")
        return

    if is_index_fresh():
        logger.info("Existing index is fresh; skipping reindex.")
    else:
        try:
            run_sync_or_async(build_index)
            mark_index_created()
            logger.info("Indexing finished successfully")
        except Exception:
            logger.exception("Indexing failed")

    logger.info("Worker finished initial tasks")

    if KEEP_ALIVE:
        logger.info("KEEP_ALIVE enabled — keeping process alive. Ctrl-C to stop.")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            logger.info("Worker interrupted, exiting.")


if __name__ == "__main__":
    main()
