import threading


class LamportClock:
    def __init__(self, value: int = 0):
        self._value = value
        self._lock = threading.Lock()

    def tick(self) -> int:
        with self._lock:
            self._value += 1
            return self._value

    def receive(self, remote_value: int) -> int:
        if not isinstance(remote_value, int) or remote_value < 0:
            raise ValueError("remote Lamport counter must be a non-negative integer")
        with self._lock:
            self._value = max(self._value, remote_value) + 1
            return self._value

    @property
    def value(self) -> int:
        with self._lock:
            return self._value
