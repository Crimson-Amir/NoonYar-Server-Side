import logging.handlers
import queue
from pythonjsonlogger.jsonlogger import JsonFormatter

log_queue = queue.Queue(-1)  # infinite size

queue_handler = logging.handlers.QueueHandler(log_queue)

file_handler = logging.FileHandler("app.log")
formatter = JsonFormatter("%(asctime)s %(levelname)s %(message)s")
file_handler.setFormatter(formatter)

listener = logging.handlers.QueueListener(log_queue, file_handler)

logger = logging.getLogger("application")
logger.addHandler(queue_handler)   # log events go into queue
logger.setLevel(logging.INFO)