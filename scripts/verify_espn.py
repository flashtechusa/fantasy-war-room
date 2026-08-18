#!/usr/bin/env python
"""Verify the ESPN connection and print everything the engine will rely on.

Run this first, before trusting a single recommendation:

    python scripts/verify_espn.py

It resolves your configuration (redacting the cookies), connects to ESPN,
and prints the league settings, roster slots, scoring rules and a sample of
the player pool with both ESPN's projection and ours side by side.  Compare
the scoring table against your league's settings page -- those rules drive
every ranking in the app.

Exits non-zero if it cannot reach ESPN, so it also works as a smoke test.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import get_settings  # noqa: E402
from app.engine.league_shape import LeagueShape  # noqa: E402
from app.engine.scoring import LeagueScoring  # noqa: E402
from app.espn.client import EspnClient, EspnConnectionError  # noqa: E402


def redact(value: str | None, keep: int = 4) -> str:
    if not value:
        return "(not set)"
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}…{value[-keep:]} ({len(value)} chars)"


def rule(title: str) -> None:
    print(f"\n{title}\n{'─' * len(title)}")


def main() -> int:
    settings = get_settings()

    rule("Configuration")
    print(f"  League ID        : {settings.espn_league_id or '(not set)'}")
    print(f"  Season           : {settings.espn_season}")
    print(f"  SWID             : {redact(settings.espn_swid, 8)}")
    print(f"  espn_s2          : {redact(settings.espn_s2)}")
    print(f"  Private-league   : {'yes' if settings.has_espn_credentials else 'no (public only)'}")
    print(f"  Demo mode        : {settings.demo_mode}")

    if settings.demo_mode:
        print("\n⚠  FWR_DEMO_MODE is true — the app will ignore ESPN entirely.")
        print("   Set it to false to use your real league.")
        return 1
    if settings.espn_league_id is None:
        print("\n✗  No league id configured. Set ESPN_LEAGUE_ID (or FWR_ESPN_LEAGUE_ID).")
        return 1

    client = EspnClient(
        league_id=settings.espn_league_id,
        season=settings.espn_season,
        swid=settings.espn_swid,
        espn_s2=settings.espn_s2,
    )

    rule("Connection")
    try:
        info = client.check_connection()
    except EspnConnectionError as exc:
        print(f"  ✗ {exc}")
        return 1
    print(f"  ✓ Connected to '{info['league_name']}' ({info['team_count']} teams)")

    data = client.league_settings()
    shape = LeagueShape.from_slots(
        data["team_count"], data["roster_slots"], data["bench_slots"], data["ir_slots"],
        data["draft_type"],
    )
    scoring = LeagueScoring.from_dicts(data["scoring_rules"])

    rule("League")
    print(f"  Name             : {data['name']}")
    print(f"  Teams            : {data['team_count']}")
    print(f"  Scoring format   : {scoring.format_label}")
    print(f"  Scoring type     : {data['scoring_type'] or '—'}")
    print(f"  Draft type       : {data['draft_type']}")
    print(f"  Draft completed  : {data['draft_completed']}")
    print(f"  Keepers          : {data['keeper_count']}")
    print(f"  Waivers          : {data['waiver_type']}"
          + (f" (${data['acquisition_budget']} FAAB)" if data["uses_faab"] else ""))
    print(f"  Playoff teams    : {data['playoff_team_count']}"
          f" over {data['regular_season_weeks']} regular-season weeks")

    rule("Roster (verify this against ESPN's settings page)")
    for slot, count in data["roster_slots"].items():
        print(f"  {count}× {slot}")
    print(f"  {data['bench_slots']}× BE")
    if data["ir_slots"]:
        print(f"  {data['ir_slots']}× IR")
    print(f"\n  Starters {shape.starter_slots} · roster size {shape.roster_size}"
          f" · {shape.draft_rounds} draft rounds"
          f" · superflex {'yes' if shape.is_superflex() else 'no'}")

    unknown = set(data["all_slot_counts"]) - set(data["roster_slots"]) - {"BE", "IR", "ER"}
    if unknown:
        print(f"\n  ⚠  Slots the offensive engine ignores: {', '.join(sorted(unknown))}")
        print("     (IDP/other positions — see TODO.md)")

    rule("Scoring rules (verify this against ESPN's settings page)")
    active = [r for r in data["scoring_rules"] if r["points"] or r["points_overrides"]]
    for entry in sorted(active, key=lambda r: r["stat_id"]):
        override = ""
        if entry["points_overrides"]:
            override = f"   overrides: {entry['points_overrides']}"
        print(f"  {entry['points']:>7}  {entry['label']}{override}")
    print(f"\n  {len(active)} active rules of {len(data['scoring_rules'])} returned by ESPN")

    rule("Teams")
    for team in client.teams():
        mine = " ← you" if settings.my_team_id == team["espn_team_id"] else ""
        slot = f" slot {team['draft_slot']}" if team["draft_slot"] else ""
        print(f"  [{team['espn_team_id']:>2}] {team['name']}"
              f" — {', '.join(team['owners']) or 'unknown owner'}"
              f"{slot} · {len(team['roster'])} rostered{mine}")

    rule("Player pool")
    try:
        players = client.player_pool(limit=250, ppr=data["is_ppr"])
    except EspnConnectionError as exc:
        print(f"  ✗ {exc}")
        return 1

    by_position: dict[str, int] = {}
    for player in players:
        by_position[player.position] = by_position.get(player.position, 0) + 1
    print(f"  {len(players)} players: "
          + ", ".join(f"{count} {pos}" for pos, count in sorted(by_position.items())))

    missing_stats = sum(1 for p in players if not p.raw_stats)
    missing_adp = sum(1 for p in players if not p.adp)
    missing_bye = sum(1 for p in players if not p.bye_week)
    print(f"  Missing raw projections: {missing_stats}")
    print(f"  Missing ADP            : {missing_adp}")
    print(f"  Missing bye week       : {missing_bye}")
    if missing_stats > len(players) * 0.2:
        print("  ⚠  Many players have no raw projection — ESPN may not have published")
        print("     season projections yet. The app falls back to ESPN's own totals.")

    rule("Top 15 by our projection (ESPN's number alongside)")
    ranked = sorted(
        players,
        key=lambda p: scoring.score(p.raw_stats, p.position) or p.espn_projected_points,
        reverse=True,
    )[:15]
    print(f"  {'Player':<26}{'Pos':<5}{'Tm':<5}{'Ours':>8}{'ESPN':>8}{'ADP':>7}")
    for player in ranked:
        ours = scoring.score(player.raw_stats, player.position)
        print(f"  {player.name[:25]:<26}{player.position:<5}{player.pro_team:<5}"
              f"{ours:>8.1f}{player.espn_projected_points:>8.1f}"
              f"{(player.adp or 0):>7.1f}")

    print("\n  If 'Ours' and 'ESPN' diverge wildly, check the scoring table above —")
    print("  a mis-parsed rule is the usual cause.")

    rule("Draft history")
    current = client.draft_results()
    print(f"  {data['season']}: {len(current)} picks"
          + (" (draft complete)" if current else " (not drafted yet)"))
    for season, picks in client.previous_draft_results().items():
        print(f"  {season}: {len(picks)} picks")

    check_draft_sources(client, settings)
    check_discovery(settings)

    print("\n✓ Verification complete. Start the app and import from the League tab.")
    return 0


def check_draft_sources(client: EspnClient, settings) -> None:
    """Compare both live-draft paths against each other, on this league.

    This is the check worth running the week of a draft: it says which source
    would answer, how fast, and whether they agree. See
    docs/espn-api-comparison.md for why they can disagree.
    """
    rule("Live draft sources")
    print(f"  Configured source: {settings.espn_draft_source}")
    try:
        snapshot = client.draft_snapshot()
    except EspnConnectionError as exc:
        print(f"  ✗ mDraftDetail  : {exc}")
        return

    state = (
        "in progress" if snapshot.in_progress
        else "complete" if snapshot.drafted
        else "not started"
    )
    print(f"  mDraftDetail     : {snapshot.pick_count} picks, {state}, "
          f"{snapshot.latency_ms:.0f} ms")
    print(f"  espn-api         : {len(client.draft_results())} picks")
    if snapshot.in_progress and snapshot.pick_count:
        print("  → A draft is running. The direct read is the source that sees it.")


def check_discovery(settings) -> None:
    """Confirm ESPN will list this account's leagues.

    Only possible with cookies -- discovery is an account-level lookup, and a
    public league has no account behind it.
    """
    rule("League discovery")
    if not settings.has_espn_credentials:
        print("  Skipped: needs SWID and espn_s2 (public leagues have no account).")
        return

    from app.espn.discovery import discover_leagues
    from app.espn.http import EspnHttpClient

    with EspnHttpClient(swid=settings.espn_swid, espn_s2=settings.espn_s2) as http:
        leagues, warnings = discover_leagues(http, season=settings.espn_season)

    print(f"  Found {len(leagues)} league(s) for {settings.espn_season}:")
    for league in leagues:
        mine = league.my_team_name or "team not detected"
        print(f"    {league.league_id:>10}  {league.name[:34]:<36}"
              f"{league.team_count:>3} teams  ({mine})")
    for warning in warnings:
        print(f"  ⚠ {warning}")
    if leagues:
        print("  → Connect ESPN in the app will offer exactly these.")


if __name__ == "__main__":
    raise SystemExit(main())
