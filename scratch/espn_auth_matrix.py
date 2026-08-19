#!/usr/bin/env python3
"""Does ESPN's fantasy read API accept bearer-token auth, or is it cookie-only?

Throwaway diagnostic. Nothing in the app imports this, and it imports nothing
from the app -- stdlib only, so it runs on any machine with Python 3.9+ and no
install step.

WHY THIS EXISTS
---------------
Every published ESPN client authenticates with a `Cookie` header. Nobody has
posted a capture of the ESPN Fantasy *mobile app*'s headers, so it is unknown
whether `lm-api-reads` also accepts an `Authorization` header. That matters
because a Disney OneID login response carries an `access_token` *and* a
`refresh_token` -- and a refresh token would mean server-side renewal instead of
re-harvesting a decaying `espn_s2` every season.

Note the question being asked. Not "does the app send Bearer" (needs a proxy,
and ESPN pins) but "does the backend *accept* Bearer" -- which is answerable
from a laptop with a token you already have.

USAGE
-----
    export ESPN_LEAGUE_ID=123456          # a PRIVATE league you are in
    export ESPN_SWID='{XXXXXXXX-...}'
    export ESPN_S2='AEB...'
    export ESPN_ONEID_TOKEN='eyJ...'      # access_token from the OneID login response
    python scratch/espn_auth_matrix.py

To get the OneID token: sign in at espn.com with DevTools open on the Network
tab, find the `registerdisney` .../guest/login response, and copy
`data.token.access_token` out of the JSON body.

Optional:
    ESPN_SEASON=2026                      # defaults to 2026
    ESPN_DELAY=0.5                        # seconds between requests

SAFETY
------
Credentials are read from the environment, never written anywhere, and never
printed. Every line of output goes through `redact()` on the way out, so a
pasted transcript cannot leak a token. The script writes no files.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

LEAGUE_HOST = "https://lm-api-reads.fantasy.espn.com"
LEAGUE_PATH = "/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}"
FAN_HOST = "https://fan.api.espn.com"
FAN_PATH = "/apis/v2/fans/{swid}"

TIMEOUT = 20

# ---------------------------------------------------------------------------
# Redaction -- nothing credential-shaped may reach stdout
# ---------------------------------------------------------------------------

_SECRETS: list[str] = []

_GUID = re.compile(
    r"\{?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}?"
)
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9%+/=_.-]{60,}\b")


def register_secret(value: str | None) -> None:
    if value and value.strip():
        _SECRETS.append(value.strip())


def redact(text: object) -> str:
    """Scrub known secrets, then anything that merely looks like one."""
    out = "" if text is None else str(text)
    for secret in _SECRETS:
        if secret:
            out = out.replace(secret, "[REDACTED]")
            out = out.replace(urllib.parse.quote(secret, safe=""), "[REDACTED]")
    out = _GUID.sub("{SWID-REDACTED}", out)
    out = _LONG_TOKEN.sub("[TOKEN-REDACTED]", out)
    return out


def say(*parts: object) -> None:
    print(redact(" ".join(str(p) for p in parts)))


# ---------------------------------------------------------------------------
# Request matrix
# ---------------------------------------------------------------------------


@dataclass
class AuthMode:
    key: str
    label: str
    #: How the Authorization header is formed, if at all.
    scheme: str | None = None
    send_swid_cookie: bool = False
    send_s2_cookie: bool = False
    #: Baseline and control decide whether the run is trustworthy at all.
    role: str = ""

    def headers(self, token: str, swid: str, espn_s2: str) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.scheme is not None:
            headers["Authorization"] = (
                f"{self.scheme} {token}".strip() if self.scheme else token
            )
        cookies = []
        if self.send_s2_cookie and espn_s2:
            cookies.append(f"espn_s2={espn_s2}")
        if self.send_swid_cookie and swid:
            cookies.append(f"SWID={swid}")
        if cookies:
            headers["Cookie"] = "; ".join(cookies) + ";"
        return headers


MODES = [
    AuthMode("cookie", "Cookie only", send_swid_cookie=True, send_s2_cookie=True,
             role="baseline"),
    AuthMode("bearer", "Bearer only", scheme="Bearer"),
    AuthMode("bearer_swid", "Bearer + SWID cookie", scheme="Bearer", send_swid_cookie=True),
    AuthMode("bearer_cookies", "Bearer + full cookies", scheme="Bearer",
             send_swid_cookie=True, send_s2_cookie=True),
    AuthMode("none", "No auth", role="control"),
    # Disney's stack has historically used non-standard schemes, so try the
    # bare token and the APIKEY form before concluding bearer is unsupported.
    AuthMode("bare", "Bare token (no scheme)", scheme=""),
    AuthMode("apikey", "APIKEY <token>", scheme="APIKEY"),
]


@dataclass
class Result:
    endpoint: str
    mode: str
    status: int | str
    body_kind: str = ""
    detail: str = ""
    auth_headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.body_kind == "real data"


def fetch(url: str, headers: dict[str, str]) -> tuple[int | str, dict, bytes]:
    request = urllib.request.Request(url, method="GET")
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", "FantasyWarRoom-AuthMatrix/1.0")
    for name, value in headers.items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read() or b""
    except Exception as exc:  # network, TLS, DNS
        return f"ERROR ({type(exc).__name__})", {}, str(exc).encode()


def classify_league(payload: object) -> tuple[str, str]:
    """Real league data, or a 200-shaped shell?

    A 200 that carries no settings is not a pass -- ESPN answers some
    unauthenticated requests with a stub, and counting that as success would
    invert the entire conclusion.
    """
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        return ("not JSON", "")
    settings = payload.get("settings") or {}
    name = settings.get("name")
    teams = payload.get("teams") or []
    # Teams are the private, account-gated payload. Settings alone is the public
    # shell ESPN hands anyone, so it is NOT proof of authentication -- requiring
    # teams is what makes the baseline/control gates mean something.
    if name and teams:
        return ("real data", f"league {payload.get('id', '?')}, {len(teams)} teams")
    if name:
        return ("public shell", "settings only, no teams")
    if teams or payload.get("status"):
        return ("partial", f"keys: {sorted(payload)[:6]}")
    if payload.get("messages"):
        return ("shell", "messages only")
    return ("shell", f"keys: {sorted(payload)[:6]}" if payload else "empty")


def classify_fan(payload: object) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ("not JSON", "")
    prefs = payload.get("preferences")
    if isinstance(prefs, list) and prefs:
        fantasy = [
            p for p in prefs
            if isinstance(p, dict) and "entry" in json.dumps(p.get("metaData", {}))[:400]
        ]
        return ("real data", f"{len(prefs)} preferences, {len(fantasy)} look fantasy")
    if payload.get("id") or payload.get("displayName"):
        return ("partial", "profile without preferences")
    return ("shell", f"keys: {sorted(payload)[:6]}" if payload else "empty")


AUTH_HEADER_NAMES = ("www-authenticate", "x-espn-api-key", "x-error", "x-reason")


def interesting_headers(headers: dict) -> dict[str, str]:
    out = {}
    for name, value in headers.items():
        lowered = name.lower()
        if lowered in AUTH_HEADER_NAMES or lowered.startswith("x-espn"):
            out[name] = value
        elif lowered == "set-cookie":
            # Presence matters; the value never does.
            out[name] = "(present)"
    return out


def run_matrix(url: str, endpoint_label: str, classify, token, swid, espn_s2, delay):
    results = []
    for mode in MODES:
        if mode.scheme is not None and not token:
            results.append(Result(endpoint_label, mode.label, "skipped",
                                  detail="no ESPN_ONEID_TOKEN set"))
            continue
        headers = mode.headers(token, swid, espn_s2)
        status, response_headers, raw = fetch(url, headers)
        if status == 200:
            try:
                kind, detail = classify(json.loads(raw.decode("utf-8", "replace")))
            except (ValueError, json.JSONDecodeError):
                kind, detail = ("not JSON", f"{len(raw)} bytes")
        else:
            kind, detail = ("", "")
            snippet = raw.decode("utf-8", "replace").strip()[:120]
            if snippet:
                detail = snippet.replace("\n", " ")
        results.append(
            Result(endpoint_label, mode.label, status, kind, detail,
                   interesting_headers(response_headers))
        )
        time.sleep(delay)
    return results


def table(results: list[Result]) -> str:
    lines = [
        "| Auth mode | Status | Body | Detail |",
        "| --- | --- | --- | --- |",
    ]
    for r in results:
        detail = redact(r.detail)[:70]
        if r.auth_headers:
            detail += f" · hdrs: {redact(r.auth_headers)}"[:60]
        lines.append(f"| {r.mode} | {r.status} | {r.body_kind or '—'} | {detail} |")
    return "\n".join(lines)


def main() -> int:
    league_id = os.environ.get("ESPN_LEAGUE_ID", "").strip()
    season = os.environ.get("ESPN_SEASON", "2026").strip()
    swid = os.environ.get("ESPN_SWID", "").strip().strip('"')
    espn_s2 = os.environ.get("ESPN_S2", "").strip().strip('"')
    token = os.environ.get("ESPN_ONEID_TOKEN", "").strip().strip('"')
    delay = float(os.environ.get("ESPN_DELAY", "0.5"))

    for secret in (swid, espn_s2, token):
        register_secret(secret)

    if not league_id:
        say("ESPN_LEAGUE_ID is required (use a PRIVATE league you are in).")
        return 2
    if not swid or not espn_s2:
        say("ESPN_SWID and ESPN_S2 are required -- the baseline row needs them.")
        return 2
    if swid and not swid.startswith("{"):
        swid = "{" + swid + "}"
        register_secret(swid)
    if not token:
        say("! ESPN_ONEID_TOKEN not set -- every bearer row will be skipped.")
        say("  Sign in at espn.com with DevTools open, find the registerdisney")
        say("  guest/login response, copy data.token.access_token.\n")

    league_url = LEAGUE_HOST + LEAGUE_PATH.format(season=season, league_id=league_id)
    league_url += "?view=mSettings&view=mTeam&view=mRoster"
    fan_url = FAN_HOST + FAN_PATH.format(swid=urllib.parse.quote(swid, safe=""))
    fan_url += "?context=fantasy"

    say(f"# ESPN auth matrix — season {season}, league {league_id}\n")
    say("Testing whether the fantasy read API accepts bearer-token auth.\n")

    league_results = run_matrix(
        league_url, "league", classify_league, token, swid, espn_s2, delay
    )
    fan_results = run_matrix(fan_url, "fan", classify_fan, token, swid, espn_s2, delay)

    say(f"## `lm-api-reads` league endpoint\n\n`{LEAGUE_PATH.format(season=season, league_id='{LEAGUE_ID}')}?view=mSettings&view=mTeam&view=mRoster`\n")
    say(table(league_results))
    say("")
    say("## `fan.api.espn.com` profile endpoint\n\n`/apis/v2/fans/{SWID}?context=fantasy`\n")
    say(table(fan_results))
    say("")

    # -- validity gates -----------------------------------------------------
    def find(results, label):
        return next((r for r in results if r.mode == label), None)

    baseline = find(league_results, "Cookie only")
    control = find(league_results, "No auth")

    problems = []
    if baseline is None or not baseline.ok:
        problems.append(
            f"BASELINE FAILED (cookie-only returned {baseline.status if baseline else '?'}). "
            "Cookies are wrong/expired, or the league id is wrong. Nothing below is meaningful."
        )
    if control is not None and control.status == 200 and control.body_kind == "real data":
        problems.append(
            "CONTROL PASSED WITHOUT AUTH (no-auth returned your teams). Either the "
            "league is public or these views are not account-gated, so it cannot test "
            "bearer auth. Re-run against a league you know is private."
        )

    say("## Verdict\n")
    if problems:
        for problem in problems:
            say(f"- **{problem}**")
        say("\n**INVALID RUN — fix the above and re-run.**")
        return 1

    bearer_modes = ["Bearer only", "Bearer + SWID cookie", "Bare token (no scheme)",
                    "APIKEY <token>"]
    league_bearer = [find(league_results, m) for m in bearer_modes]
    fan_bearer = [find(fan_results, m) for m in bearer_modes]
    passing = [r for r in league_bearer + fan_bearer if r and r.ok]

    if not token:
        say("**UNTESTED** — no OneID token supplied, so bearer auth was never exercised.")
        return 1
    if passing:
        say("**YES — this backend accepts bearer auth.** Working combinations:\n")
        for r in passing:
            say(f"- `{r.endpoint}` via **{r.mode}**")
        say(
            "\nWorth following up: a OneID login also returns a `refresh_token`, so "
            "server-side renewal may be possible instead of re-harvesting espn_s2 "
            "each season."
        )
    else:
        say("**NO — cookie-only.** Every bearer variant failed against both endpoints "
            "while the cookie baseline passed, so `Authorization` is not an accepted "
            "auth path here. The webview cookie-capture design stands unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
