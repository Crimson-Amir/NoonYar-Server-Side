import logging
from logging.handlers import RotatingFileHandler
import os

LOG_DIR = "./"
LOG_FILE = "app.log"
os.makedirs(LOG_DIR, exist_ok=True)

def setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)  # Set to DEBUG to capture everything

    if logger.hasHandlers():
        return logger  # Avoid duplicate handlers

    # 🌐 Console Handler: INFO and above (prints in terminal)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter("[%(levelname)s] %(message)s")
    console_handler.setFormatter(console_format)

    # 📝 File Handler: WARNING and above (writes to file)
    file_handler = RotatingFileHandler(
        filename=os.path.join(LOG_DIR, LOG_FILE),
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3
    )
    file_handler.setLevel(logging.WARNING)
    file_format = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
        "%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_format)

    # Attach handlers to logger
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger
