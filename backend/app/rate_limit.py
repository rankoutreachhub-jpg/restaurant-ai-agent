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

Stage 3 Step 6B generalizes the key a limiter buckets on: originally
every limiter was hardcoded to client IP (request.client.host). The
core counting logic now lives in check(key), keyed by any string;
__call__(request) — the FastAPI-dependency shape /chat and /admin still
use — just derives that string via an injectable key_func, defaulting
to the original client-IP behavior so chat_rate_limiter/admin_rate_limiter
are completely unchanged. WhatsApp message processing (app/
whatsapp_processing.py) runs inside a BackgroundTasks callback, not a
FastAPI request handler, so it has no Request to depend on — it calls
whatsapp_rate_limiter.check(...) directly with a key built from
phone_number_id + the customer's phone number instead.
"""

import logging
import time
from collections import defaultdict
from threading import Lock
from typing import Callable, Optional

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)


def _client_ip_key(request: Request) -> str:
    # request.client is None in some test/proxy setups; fall back to a
    # shared bucket rather than crashing.
    return request.client.host if request.client else "unknown"


class RateLimiter:
    """Raises 429 once a caller exceeds max_requests within a rolling
    window_seconds, identified by an arbitrary string key.

    Used two ways:
      - As a FastAPI dependency (`Depends(some_limiter)`), which derives
        the key from the Request via key_func (defaults to client IP —
        the original, unchanged behavior for chat_rate_limiter and
        admin_rate_limiter).
      - Directly, via check(key), for callers with no Request object at
        all (WhatsApp background processing) — see whatsapp_rate_limiter
        below and app/whatsapp_processing.py.
    """

    def __init__(
        self,
        max_requests: int,
        window_seconds: int,
        name: str,
        key_func: Optional[Callable[[Request], str]] = None,
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.name = name
        self.key_func = key_func or _client_ip_key
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def reset(self) -> None:
        """Clears all tracked hits. Used by tests to isolate cases."""
        with self._lock:
            self._hits.clear()

    def check(self, key: str, path: str = "", log_key: Optional[str] = None) -> None:
        """Core rate-limit check, keyed by an explicit string — usable
        with no Request object in scope at all. Raises HTTPException
        (429) once `key` exceeds max_requests within window_seconds.

        log_key controls what (if anything) identifies the throttled
        caller in the log line — pass it explicitly only when the key is
        safe to log (e.g. a client IP, as __call__ does below). Left as
        None (the default), nothing beyond the limiter name and path is
        logged — the safe default for a caller like
        whatsapp_rate_limiter, whose key contains a customer phone
        number that must never be logged (see app/whatsapp_processing.py)."""
        now = time.monotonic()
        window_start = now - self.window_seconds

        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] < window_start:
                hits.pop(0)

            if len(hits) >= self.max_requests:
                # Log the fact of the throttle (limiter, [client,] path) as
                # a security/abuse-relevant event — never any header or body.
                if log_key is not None:
                    logger.warning(
                        "Rate limit exceeded (limiter=%s client=%s path=%s)",
                        self.name, log_key, path,
                    )
                else:
                    logger.warning(
                        "Rate limit exceeded (limiter=%s path=%s)",
                        self.name, path,
                    )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded: max {self.max_requests} requests "
                           f"per {self.window_seconds} seconds. Please try again shortly.",
                    headers={"Retry-After": str(self.window_seconds)},
                )

            hits.append(now)

    def __call__(self, request: Request) -> None:
        # Original behavior, byte for byte: the client IP is safe to log,
        # so it's passed as log_key exactly as it always was.
        key = self.key_func(request)
        self.check(key, path=request.url.path, log_key=key)


# 10 messages/minute per IP: generous for a real conversation, tight
# enough to stop a script from running up the Gemini bill.
chat_rate_limiter = RateLimiter(max_requests=10, window_seconds=60, name="chat")

# 30 requests/minute per IP across all /admin/* routes: a real admin
# doing bulk edits won't hit this, but it bounds brute-force/flood
# attempts against the admin API key.
admin_rate_limiter = RateLimiter(max_requests=30, window_seconds=60, name="admin")

# 10 messages/minute per (phone_number_id, customer phone number): the
# same budget as chat_rate_limiter and for the same reason (Gemini costs
# real money), just keyed by the WhatsApp identity pair instead of an IP
# — see app/whatsapp_processing.py, which calls .check(key) directly
# since it runs outside any FastAPI request/dependency context.
whatsapp_rate_limiter = RateLimiter(max_requests=10, window_seconds=60, name="whatsapp")
