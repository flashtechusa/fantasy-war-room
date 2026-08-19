#!/usr/bin/env python3
"""Interactive discovery + verification for the ESPN Email Code (OTP) flow.

Run this from a machine that can reach `registerdisney.go.com` (i.e. not the CI
sandbox). It walks the four OTP steps one at a time, printing FOUND / NOT FOUND
for the things that matter -- never the values themselves -- and finishes by
proving the resulting cookies read a private league.

    python scripts/test_espn_otp.py

You will be prompted for your ESPN email and, after Disney sends it, the code.

The four steps and the fields each must yield (from the observed contract):
    1 /guest/recovery-methods     -> a recovery method exists
    2 /notification/otp/recovery  -> a session id
    3 /otp/redeem                 -> swid + recovery token
    4 /guest/login/recoveryToken  -> data.s2 (espn_s2) + data.profile.swid

If a step drifts, pass --show-shapes to print the redacted response structure so
`app/espn/oneid.py` can be corrected.

CONFIG
------
    ESPN_TEST_LEAGUE_ID   a PRIVATE league you are in, for the proof step.
    ESPN_SEASON           defaults to 2026.

No API key is needed -- the real Disney flow carries none. Nothing is written to
disk; the email, code, tokens and cookies are never printed, only FOUND /
NOT FOUND and redacted structure.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.espn import oneid  # noqa: E402
from app.espn.http import EspnHttpClient  # noqa: E402
from app.espn.redaction import redact  # noqa: E402


def found(label: str, value: object) -> None:
    print(f"  {label:<14} {'FOUND' if value else 'NOT FOUND'}")


def shape(node, depth: int = 0, max_depth: int = 4) -> None:
    """Keys and value *types* only -- never values."""
    pad = "    " * (depth + 1)
    if isinstance(node, dict):
        for k in list(node)[:25]:
            v = node[k]
            print(f"{pad}{k}: {type(v).__name__}")
            if isinstance(v, (dict, list)) and depth < max_depth:
                shape(v, depth + 1, max_depth)
    elif isinstance(node, list):
        print(f"{pad}[{len(node)} items]")
        if node and depth < max_depth:
            shape(node[0], depth + 1, max_depth)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--show-shapes", action="store_true",
                        help="Print redacted response structure for each step.")
    args = parser.parse_args()

    league_id = os.environ.get("ESPN_TEST_LEAGUE_ID", "").strip()
    season = int(os.environ.get("ESPN_SEASON", "2026"))

    email = input("ESPN email: ").strip()
    if not oneid.looks_like_email(email):
        print("That does not look like an email address.")
        return 2

    client = oneid.DisneyOneID()
    try:
        print("\n[1/4] Checking the account (recovery-methods)...")
        r1 = client.recovery_methods(email)
        print("  OK")
        if args.show_shapes:
            shape(r1.raw)

        print("\n[2/4] Requesting the code (notification/otp/recovery)...")
        r2 = client.request_otp(email)
        found("session id", client._session_id)  # noqa: SLF001 - diagnostic
        print("  Code sent — check your email.")
        if args.show_shapes:
            shape(r2.raw)

        code = input("\nEnter the code from your email: ").strip()

        print("\n[3/4] Redeeming the code (otp/redeem)...")
        r3 = client.submit_otp(code)
        found("redeemed SWID", client._redeemed_swid)  # noqa: SLF001
        found("recovery token", client._recovery_token)  # noqa: SLF001
        if args.show_shapes:
            shape(r3.raw)

        print("\n[4/4] Exchanging for espn_s2 (guest/login/recoveryToken)...")
        try:
            swid, espn_s2 = client.establish_espn_session()
            found("SWID", swid)
            found("espn_s2", espn_s2)
        except oneid.OneIDError as exc:
            print(f"\n  establish failed: {redact(exc)}")
            print("  Cookies seen across the flow (names only):",
                  sorted(client._cookie_jar))  # noqa: SLF001 - names are not secret
            if args.show_shapes:
                print("  redeem (step 3) response shape:")
                shape(r3.raw)
            return 1
    finally:
        client.close()

    if not league_id:
        print("\nSWID + espn_s2 obtained. Set ESPN_TEST_LEAGUE_ID to also prove a "
              "private league read.")
        return 0

    print(f"\nProving private league {league_id}...")
    http = EspnHttpClient(swid=swid, espn_s2=espn_s2)
    try:
        resp = http.league_view(int(league_id), season, ["mSettings", "mTeam"])
        data = resp.data
        settings = data.get("settings") or {}
        teams = data.get("teams") or []
        print(f"  HTTP {resp.status_code}")
        print(f"  League: {settings.get('name', '(no name)')}")
        print(f"  Teams:  {len(teams)}")
        if resp.status_code == 200 and settings.get("name") and teams:
            print("\nSUCCESS — phone-only private ESPN onboarding works via OTP.")
            return 0
        print("\nReached ESPN but did not get authenticated league data. "
              "Check the league id is one you are in.")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"  {redact(exc)}")
        return 1
    finally:
        http.close()


if __name__ == "__main__":
    raise SystemExit(main())
