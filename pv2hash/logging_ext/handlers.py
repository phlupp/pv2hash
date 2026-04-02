import logging

from pv2hash.logging_ext.ringbuffer import LogRingBuffer


class RingBufferHandler(logging.Handler):
    def __init__(self, ringbuffer: LogRingBuffer) -> None:
        super().__init__()
        self.ringbuffer = ringbuffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.ringbuffer.append(msg)
        except Exception:
            self.handleError(record)