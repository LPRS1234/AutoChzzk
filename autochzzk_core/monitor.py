"""Bounded background lookups; callers apply results on the UI thread."""
from __future__ import annotations

import threading
from queue import SimpleQueue


class LookupPool:
    def __init__(self, workers=4):
        self.capacity = workers
        self.pending = {}
        self.results = SimpleQueue()
        self.closed = False

    def submit(self, key, work, callback):
        # Called only by the UI thread. Completion does not free a slot until
        # drain(), so a slow UI cannot accumulate an unbounded result queue.
        if self.closed or key in self.pending or len(self.pending) >= self.capacity:
            return False
        self.pending[key] = callback
        def run():
            try:
                value, error = work(), None
            except Exception as exc:
                value, error = None, exc
            self.results.put((key, value, error))
        threading.Thread(target=run, daemon=True).start()
        return True

    def drain(self):
        while not self.results.empty():
            key, value, error = self.results.get()
            callback = self.pending.pop(key)
            if not self.closed:
                callback(value, error)

    def close(self):
        self.closed = True
