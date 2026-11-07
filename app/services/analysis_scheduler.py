"""Event-driven, debounced analysis scheduler.

Replaces any fixed polling interval. Analysis runs only when meaningful new *final* transcript
content exists:
- debounce: wait `debounce_s` after the last new segment (rapid speech coalesces into one call),
- ceiling: never let new content wait more than `max_wait_s` while speech continues,
- urgent segments (contain digits, e.g. dates / counts / prices) use a short debounce,
- exactly one analysis in flight (a controlled, sequential queue); events arriving meanwhile accumulate,
- silence or unchanged transcript triggers nothing.
"""
import asyncio
import time
from typing import Awaitable, Callable, Optional


class AnalysisScheduler:
    def __init__(
        self,
        run: Callable[[], Awaitable[None]],
        debounce_s: float = 2.5,
        max_wait_s: float = 8.0,
        urgent_debounce_s: float = 1.0,
        min_words: int = 3,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._run_analysis = run
        self.debounce_s = debounce_s
        self.max_wait_s = max_wait_s
        self.urgent_debounce_s = urgent_debounce_s
        self.min_words = min_words
        self.clock = clock
        self.pending_words = 0
        self.first_pending: Optional[float] = None
        self.last_event: float = 0.0
        self.urgent = False
        self.runs = 0
        self._event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._stopped = False
        self._idle = asyncio.Event()
        self._idle.set()

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    def notify(self, text: str) -> None:
        """Call when a finalized transcript segment arrives."""
        words = len((text or "").split())
        if words == 0:
            return
        now = self.clock()
        if self.pending_words == 0:
            self.first_pending = now
        self.pending_words += words
        self.last_event = now
        if any(ch.isdigit() for ch in text):
            self.urgent = True
        self._idle.clear()
        self._event.set()

    def _due_in(self) -> float:
        now = self.clock()
        deb = self.urgent_debounce_s if self.urgent else self.debounce_s
        due = min(self.last_event + deb, (self.first_pending or now) + self.max_wait_s)
        return max(0.0, due - now)

    async def _loop(self) -> None:
        while not self._stopped:
            await self._event.wait()
            self._event.clear()
            while self.pending_words and not self._stopped:
                wait = self._due_in()
                if wait > 0:
                    await asyncio.sleep(min(wait, 0.25))
                    continue
                if self.pending_words < self.min_words and not self.urgent:
                    # too little to justify an LLM call; wait for more speech (bounded by max_wait)
                    if self.clock() - (self.first_pending or 0) < self.max_wait_s:
                        await asyncio.sleep(0.25)
                        continue
                self.pending_words, self.first_pending, self.urgent = 0, None, False
                self.runs += 1
                try:
                    await self._run_analysis()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - a failing analysis never kills the scheduler
                    pass
            if not self.pending_words:
                self._idle.set()

    async def drain(self, timeout: float = 30.0) -> None:
        """Wait until no analysis is pending or running (used before finalisation)."""
        try:
            await asyncio.wait_for(self._idle.wait(), timeout)
        except asyncio.TimeoutError:
            pass

    async def stop(self) -> None:
        self._stopped = True
        self._event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
