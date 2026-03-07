# worker.py (run heavy tasks)
import os, logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker")

def main():
    logger.info("Worker start: running indexing tasks")
    try:
        from website_index import build_index
    except Exception:
        logger.exception("Worker import failed; ensure ML deps installed")
        return
    build_index()
    logger.info("Worker finished")

if __name__ == "__main__":
    main()
