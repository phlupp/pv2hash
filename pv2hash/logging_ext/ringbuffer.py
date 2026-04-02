from collections import deque
from threading import Lock


class LogRingBuffer:
    def __init__(self, max_lines: int = 500) -> None:
        self._lines: deque[str] = deque(maxlen=max_lines)
        self._lock = Lock()

    def append(self, line: str) -> None:
        with self._lock:
            self._lines.append(line)

    def get_lines(self) -> list[str]:
        with self._lock:
            return list(self._lines)

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()