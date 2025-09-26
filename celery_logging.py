import logging
from pythonjsonlogger import jsonlogger


def setup_celery_logger():
    logger = logging.getLogger("app")  # same name as FastAPI logger
    logger.setLevel(logging.INFO)

    # Direct file handler (no queue needed for Celery)
    file_handler = logging.FileHandler("celery.log")
    formatter = jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(message)s")
    file_handler.setFormatter(formatter)

    # Avoid adding duplicate handlers
    if not logger.hasHandlers():
        logger.addHandler(file_handler)

    return logger


celery_logger = setup_celery_logger()
