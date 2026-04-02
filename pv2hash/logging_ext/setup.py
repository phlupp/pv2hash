import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from pv2hash.logging_ext.handlers import RingBufferHandler
from pv2hash.logging_ext.ringbuffer import LogRingBuffer


LOG_DIR = Path("data/logs")
LOG_FILE = LOG_DIR / "pv2hash.log"

ringbuffer = LogRingBuffer(max_lines=1000)


def setup_logging(log_level: str = "INFO") -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    if root_logger.handlers:
        root_logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    ring_handler = RingBufferHandler(ringbuffer)
    ring_handler.setLevel(logging.DEBUG)
    ring_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(ring_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def get_ringbuffer_lines() -> list[str]:
    return ringbuffer.get_lines()


def get_log_file_path() -> Path:
    return LOG_FILE