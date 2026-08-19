#!/usr/bin/env python3
"""Interactive discovery + verification for the ESPN Email Code (OTP) flow.

Run this from a machine that can reach `registerdisney.go.com` (i.e. not the CI
sandbox). It walks the four OTP steps one at a time, printing FOUND / NOT FOUND
for the things that matter -- never the values themselves -- and finishes by
proving the resulting cookies read a private league.

    python scripts/test_espn_otp.py

You will be prompted for your ESPN email and, after Disney sends it, the code.

TWO JOBS
--------
1. Confirm the flow works end to end (the SUCCESS path).
2. When a step's contract is wrong -- which is expected on the first live run,
   because the request/response shapes were guessed -- show the response
   *structure* (keys only, values redacted) so the guesses in
   `app/espn/oneid.py` can be corrected in one pass. Pass `--show-shapes`.

CONFIG
------
    FWR_ESPN_ONEID_API_KEY   the `authorization: APIKEY ...` value espn.com sends
                             on its registerdisney calls (Network tab). Required.
    ESPN_TEST_LEAGUE_ID      a PRIVATE league you are in, for the proof step.
    ESPN_SEASON              defaults to 2026.

Nothing is written to disk. The email, code, tokens and cookies are never
printed; only FOUND / NOT FOUND and redacted structure.
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
    print(f"  {label:<12} {'FOUND' if value else 'NOT FOUND'}")


def shape(node, depth: int = 0, max_depth: int = 4):
    """Keys and value *types* only -- never values. Redacts any string it prints."""
    pad = "    " * (depth + 1)
    if isinstance(node, dict):
        for k in list(node)[:20]:
            v = node[k]
            if isinstance(v, (dict, list)):
                print(f"{pad}{k}: {type(v).__name__}")
                if depth < max_depth:
                    shape(v, depth + 1, max_depth)
            else:
                kind = type(v).__name__
                print(f"{pad}{k}: {kind}")
    elif isinstance(node, list):
        print(f"{pad}[{len(node)} items]")
        if node and depth < max_depth:
            shape(node[0], depth + 1, max_depth)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--show-shapes", action="store_true",
                        help="Print redacted response structure for each step.")
    args = parser.parse_args()

    api_key = oneid.api_key_from_env()
    if not api_key:
        print("Set FWR_ESPN_ONEID_API_KEY first (the APIKEY value from espn.com).")
        return 2

    league_id = os.environ.get("ESPN_TEST_LEAGUE_ID", "").strip()
    season = int(os.environ.get("ESPN_SEASON", "2026"))

    email = input("ESPN email: ").strip()
    if not oneid.looks_like_email(email):
        print("That does not look like an email address.")
        return 2

    client = oneid.DisneyOneID(api_key=api_key)
    try:
        print("\nStarting Disney flow...")
        r1 = client.start_flow()
        found("flow token", client._flow_token)  # noqa: SLF001 - diagnostic
        if args.show_shapes:
            shape(r1.raw)

        print("\nRequesting OTP...")
        r2 = client.request_otp(email)
        print("  Code sent (check your email).")
        if args.show_shapes:
            shape(r2.raw)

        code = input("\nEnter code from your email: ").strip()

        print("\nRedeeming code...")
        r3 = client.submit_otp(code)
        found("disney token", client._disney_token)  # noqa: SLF001
        if args.show_shapes:
            shape(r3.raw)

        print("\nEstablishing ESPN session...")
        try:
            swid, espn_s2 = client.establish_espn_session()
            found("SWID", swid)
            found("espn_s2", espn_s2)
        except oneid.OneIDError as exc:
            found("SWID", client._cookie_jar.get("SWID"))  # noqa: SLF001
            found("espn_s2", client._cookie_jar.get("espn_s2"))  # noqa: SLF001
            print(f"\n  establish failed: {redact(exc)}")
            print("  Cookies seen across the flow (names only):",
                  sorted(client._cookie_jar))  # noqa: SLF001 - names are not secret
            if args.show_shapes:
                print("  redeem response shape:")
                shape(r3.raw)
            return 1
    finally:
        client.close()

    if not league_id:
        print("\nSWID + espn_s2 obtained. Set ESPN_TEST_LEAGUE_ID to also prove a "
              "private league read.")
        return 0

    print(f"\nTesting private league {league_id}...")
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
            print("\nSUCCESS -- phone-only private ESPN onboarding works via OTP.")
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
