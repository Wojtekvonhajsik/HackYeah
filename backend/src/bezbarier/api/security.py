"""Zabezpieczenia API: klucz administratora dla płatnych operacji i limit zapytań dla głosów."""

from __future__ import annotations

import os
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    """Skan i analiza zdjęć kosztują (płatne API modelu) - tylko z nagłówkiem X-Admin-Token.

    Bez ustawionego ADMIN_TOKEN endpointy są otwarte - wygodne lokalnie, NIE do wdrożenia publicznego.
    """
    expected = os.environ.get("ADMIN_TOKEN")
    if not expected:
        return
    if x_admin_token is None or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=401, detail="Wymagany poprawny nagłówek X-Admin-Token")


class RateLimiter:
    """Prosty limit w oknie czasowym, w pamięci (jedna instancja serwera)."""

    def __init__(self, max_requests: int, window_s: float) -> None:
        self.max_requests = max_requests
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window_s:
                hits.popleft()
            if len(hits) >= self.max_requests:
                retry = int(self.window_s - (now - hits[0])) + 1
                raise HTTPException(
                    status_code=429,
                    detail=f"Za dużo głosów z tego adresu - spróbuj za {retry} s",
                    headers={"Retry-After": str(retry)},
                )
            hits.append(now)


_vote_limiter: RateLimiter | None = None


def vote_limiter() -> RateLimiter:
    """Tworzony przy pierwszym głosie, żeby VOTE_RATE_LIMIT_PER_HOUR z .env był już wczytany."""
    global _vote_limiter
    if _vote_limiter is None:
        _vote_limiter = RateLimiter(int(os.environ.get("VOTE_RATE_LIMIT_PER_HOUR", "30")), window_s=3600)
    return _vote_limiter


def limit_votes(request: Request) -> None:
    # Za reverse proxy adres klienta to adres proxy - wtedy uruchom uvicorn z --proxy-headers
    vote_limiter().check(request.client.host if request.client else "unknown")
