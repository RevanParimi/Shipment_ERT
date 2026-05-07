"""Shared Groq client, retry utility, and constants used by all agents.

A single Groq() instance is safe to share across threads (httpx.Client
is thread-safe and benefits from connection pooling).
"""

import os
import time
from datetime import datetime

from dotenv import load_dotenv

# Simulation clock anchor — matches seed_data.py so date arithmetic is meaningful
SIM_NOW = datetime(2026, 5, 4, 8, 0, 0)

load_dotenv()

GROQ_AVAILABLE: bool = bool(os.getenv("GROQ_API_KEY"))
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

if GROQ_AVAILABLE:
    from groq import Groq, RateLimitError as _GroqRateLimitError
    groq_client: "Groq | None" = Groq(api_key=os.getenv("GROQ_API_KEY"))
else:
    groq_client = None
    _GroqRateLimitError = None  # type: ignore[assignment,misc]


def call_with_retry(fn, max_retries: int = 3):
    """Call fn() and retry on Groq rate-limit errors with exponential backoff.

    Backoff schedule: 2 s → 4 s (attempts 1 and 2).
    Any non-rate-limit exception is re-raised immediately.
    """
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as exc:
            is_rate_limit = (
                (_GroqRateLimitError is not None and isinstance(exc, _GroqRateLimitError))
                or "429" in str(exc)
                or "rate_limit" in str(exc).lower()
                or "rate limit" in str(exc).lower()
            )
            if is_rate_limit and attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))   # 2 s, 4 s
                continue
            raise   # non-rate-limit error or final attempt
