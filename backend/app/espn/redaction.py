"""Keeping ESPN cookies out of logs, errors and API responses.

`SWID` and `espn_s2` are live session credentials for somebody's ESPN account.
The realistic way they escape is not a deliberate `print` -- it is an exception
whose message happens to contain the request URL, or a debug log of a header
dict. So redaction happens at the boundary rather than at each call site:

* `redact()` scrubs anything that *looks* like either cookie, whether or not we
  know the actual value.
* `RedactingFilter` is installed on the root logger so a third-party library
  logging our request cannot leak them either.

The patterns deliberately over-match. A redacted GUID that turned out to be
harmless costs nothing; a leaked one costs somebody their ESPN account.
"""

from __future__ import annotations

import logging
import re

SWID_PLACEHOLDER = "{SWID-REDACTED}"
S2_PLACEHOLDER = "[espn_s2-REDACTED]"

#: A brace-wrapped GUID. This is exactly the shape of the SWID cookie, and of
#: the ESPN member ids derived from it.
_SWID_GUID = re.compile(
    r"\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}?"
)

#: `espn_s2=<value>` in a cookie header, query string or error message. The
#: value is URL-encoded base64-ish and runs to the next delimiter.
_S2_ASSIGNMENT = re.compile(r"(espn_s2|ESPN_S2)\s*[=:]\s*\"?([^\s;,&\"']+)", re.IGNORECASE)

#: `SWID=<value>` written out as an assignment, in case the value is not a GUID.
_SWID_ASSIGNMENT = re.compile(r"(swid)\s*[=:]\s*\"?([^\s;,&\"']+)", re.IGNORECASE)

#: A bare espn_s2-shaped token: ESPN's is ~300 characters of base64 with `%`
#: escapes. Nothing else we log is that long, so this catches a value that
#: reached a message without its name attached.
_LOOSE_S2 = re.compile(r"\b[A-Za-z0-9%+/=_-]{120,}\b")

#: An email address. Used by the OTP connect flow, where the address is the one
#: piece of PII in play; scrubbing it here means a flow error can never carry it
#: into a log or an API response.
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

#: Header names whose values are never safe to render.
SENSITIVE_HEADERS = {"cookie", "set-cookie", "authorization", "x-espn-swid", "proxy-authorization"}


def redact(value: object) -> str:
    """Return `value` as a string with anything cookie-shaped removed.

    Safe to call on exceptions, URLs, header dicts and arbitrary text.
    """
    text = "" if value is None else str(value)
    if not text:
        return text
    text = _S2_ASSIGNMENT.sub(lambda m: f"{m.group(1)}={S2_PLACEHOLDER}", text)
    text = _SWID_ASSIGNMENT.sub(lambda m: f"{m.group(1)}={SWID_PLACEHOLDER}", text)
    text = _SWID_GUID.sub(SWID_PLACEHOLDER, text)
    text = _LOOSE_S2.sub(S2_PLACEHOLDER, text)
    text = _EMAIL.sub("[email-REDACTED]", text)
    return text


def redact_headers(headers: dict | None) -> dict:
    """A copy of `headers` safe to log or return, with secret values replaced."""
    if not headers:
        return {}
    out: dict = {}
    for key, value in headers.items():
        if str(key).lower() in SENSITIVE_HEADERS:
            out[str(key)] = "[REDACTED]"
        else:
            out[str(key)] = redact(value)
    return out


def redact_url(url: str) -> str:
    """A URL with any credential-bearing path segment or query value removed.

    ESPN's fan endpoint puts the SWID in the *path*, so stripping the query
    string alone is not enough.
    """
    return redact(url)


class RedactingFilter(logging.Filter):
    """Scrubs credentials from every log record that passes through.

    Applied to handlers rather than loggers so it also covers records emitted
    by `httpx`, `urllib3` and anything else that sees our request.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - logging API
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: redact(v) for k, v in record.args.items()}
            else:
                record.args = tuple(
                    redact(a) if isinstance(a, (str, bytes, Exception)) else a
                    for a in record.args
                )
        return True


def install_log_redaction(logger: logging.Logger | None = None) -> None:
    """Attach `RedactingFilter` to every handler on the root logger.

    Called once at start-up. Idempotent: re-running after `logging.basicConfig`
    adds a handler is exactly the intended usage.
    """
    target = logger or logging.getLogger()
    for handler in target.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())
