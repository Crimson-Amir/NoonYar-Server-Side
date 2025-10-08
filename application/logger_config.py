import logging.handlers
import queue
import sys
from pythonjsonlogger.jsonlogger import JsonFormatter

# FASTAPI LOGGER
# Shared queue for FastAPI
fastapi_queue = queue.Queue(-1)
fastapi_queue_handler = logging.handlers.QueueHandler(fastapi_queue)
fastapi_console_handler = logging.StreamHandler(sys.stdout)
formatter = JsonFormatter("%(asctime)s %(levelname)s %(message)s")
fastapi_console_handler.setFormatter(formatter)

# Listener for FastAPI (started in lifespan)
fastapi_listener = logging.handlers.QueueListener(fastapi_queue, fastapi_console_handler)

# FastAPI logger
logger = logging.getLogger("fastapi_app")
logger.addHandler(fastapi_queue_handler)
logger.setLevel(logging.INFO)
logger.propagate = False

# CELERY LOGGER
# Direct handler for Celery (no queue needed, separate process)
celery_console_handler = logging.StreamHandler(sys.stdout)
celery_console_handler.setFormatter(formatter)

celery_logger = logging.getLogger("celery_app")
celery_logger.addHandler(celery_console_handler)
celery_logger.setLevel(logging.INFO)
celery_logger.propagate = False