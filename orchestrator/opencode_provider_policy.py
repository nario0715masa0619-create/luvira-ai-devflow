"""Stable, secret-free outcome policy for OpenCode Go with Zen fallback.

OpenCode performs Go-to-Zen fallback behind its Go-compatible endpoint.  The
orchestrator must therefore never implement a second provider switch or retry
the same request against a different API key.  It only classifies the final
outcome returned by that one provider boundary.
"""

from __future__ import annotations


# These codes are deliberately narrow: a terminal balance or monthly-limit
# response cannot become an unbounded Scheduler retry.  The exact provider
# body is never persisted or returned to callers.
_TERMINAL_MARKERS = (
    "monthly limit",
    "monthly_limit",
    "zen balance",
    "insufficient balance",
    "insufficient credits",
    "credit balance",
    "billing limit",
)


def classify_opencode_failure(status_code: int | None, body: str | None = None, *, timeout: bool = False) -> str:
    """Classify an OpenCode outcome without retaining its body.

    A 429 is retryable unless it explicitly identifies the Zen budget as the
    terminal cause.  This preserves automatic Go-to-Zen fallback while
    preventing a 24-hour Broker from spending or retrying forever after the
    account's configured monthly cap is reached.
    """
    if timeout:
        return "OPENCODE_TIMEOUT_RETRYABLE"
    normalized = body.lower() if isinstance(body, str) else ""
    if any(marker in normalized for marker in _TERMINAL_MARKERS):
        return "OPENCODE_BUDGET_EXHAUSTED_FINAL"
    if status_code in {408, 409, 429, 500, 502, 503, 504}:
        return f"OPENCODE_HTTP_{status_code}_RETRYABLE"
    if status_code in {400, 401, 403, 404, 413, 422}:
        return f"OPENCODE_HTTP_{status_code}_FINAL"
    return "OPENCODE_PROTOCOL_FINAL"
