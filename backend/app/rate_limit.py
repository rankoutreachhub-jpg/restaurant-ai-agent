"""
Simple in-memory rate limiting.

MVP approach: a fixed-window request counter per client IP, held in a
plain dict in process memory — no Redis or other external service.
This is intentionally simple, matching app/auth.py's approach to admin
auth, and is enough to blunt casual abuse of a single-process
deployment:
  - /chat calls cost real money (the Gemini API), so it's kept tight
    enough to stop scripted abuse without bothering a normal customer
    having a real conversation.
  - /admin/* is throttled mainly to blunt brute-forcing or flooding of
    the admin API key, on top of the auth check in app/auth.py.

KNOWN LIMITATION: state is per-process and in memory, so it only holds
the stated limit correctly when the app runs as a single worker
process. Running multiple uvicorn/gunicorn workers (or instances behind
a load balancer) gives each process its own counters, so the effective
limit becomes max_requests times the number of processes. Fine for
this MVP's single-process deployment; a shared store (e.g. Redis) would
be needed to enforce a true global limit across multiple processes.
"""

import logging
import time
from collections import defaultdict
from threading import Lock

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)


class RateLimiter:
    """FastAPI dependency: raises 429 once a client exceeds max_requests
    within a rolling window_seconds, identified by client IP."""

    def __init__(self, max_requests: int, window_seconds: int, name: str):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.name = name
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    @staticmethod
    def _client_key(request: Request) -> str:
        # request.client is None in some test/proxy setups; fall back to a
        # shared bucket rather than crashing.
        return request.client.host if request.client else "unknown"

    def reset(self) -> None:
        """Clears all tracked hits. Used by tests to isolate cases."""
        with self._lock:
            self._hits.clear()

    def __call__(self, request: Request) -> None:
        key = self._client_key(request)
        now = time.monotonic()
        window_start = now - self.window_seconds

        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] < window_start:
                hits.pop(0)

            if len(hits) >= self.max_requests:
                # Log the fact of the throttle (limiter, client, path) as a
                # security/abuse-relevant event — never any header or body.
                logger.warning(
                    "Rate limit exceeded (limiter=%s client=%s path=%s)",
                    self.name,
                    key,
                    request.url.path,
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded: max {self.max_requests} requests "
                           f"per {self.window_seconds} seconds. Please try again shortly.",
                    headers={"Retry-After": str(self.window_seconds)},
                )

            hits.append(now)


# 10 messages/minute per IP: generous for a real conversation, tight
# enough to stop a script from running up the Gemini bill.
chat_rate_limiter = RateLimiter(max_requests=10, window_seconds=60, name="chat")

# 30 requests/minute per IP across all /admin/* routes: a real admin
# doing bulk edits won't hit this, but it bounds brute-force/flood
# attempts against the admin API key.
admin_rate_limiter = RateLimiter(max_requests=30, window_seconds=60, name="admin")
