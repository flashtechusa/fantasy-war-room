"""Finding out which ESPN leagues a set of cookies can reach.

This is the piece that removes most of the setup friction. Previously a user
had to find their league id in a URL, type it in, remember which season, and
work out which of the twelve teams was theirs. All of that is derivable from
the cookies themselves:

* ESPN's *fan* profile lists every fantasy team an account manages, and each
  entry names the league it belongs to. That gives us the candidate list.
* Each candidate is then confirmed against the v3 league endpoint, which is
  what tells us authoritatively that it is a football league, what it is
  called, how big it is, and what its rules are.

The two-step matters. The fan profile is a personalisation surface: its shape
changes, it mixes sports, and it is not a contract. Treating it as a source of
*hints* and the league endpoint as the source of *truth* means a change to the
former degrades discovery to "we found fewer leagues" rather than to wrong
data. A league the user types in manually skips step one entirely and still
works, which is why manual entry stays supported.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .client import EspnClient
from .http import EspnHttpClient, EspnHttpError
from .redaction import redact

log = logging.getLogger(__name__)

#: ESPN's numeric game id for fantasy football, and the URL fragment that
#: identifies it. Either signal is enough; neither is required, because a
#: candidate with no sport signal is simply confirmed against the football
#: endpoint and drops out if it is not one.
FFL_GAME_IDS = {1, "1", "ffl", "FFL"}
FFL_URL_HINTS = ("/football/", "ffl")

#: Views that, together, describe a whole league in one request.
LEAGUE_PREVIEW_VIEWS = ["mSettings", "mTeam", "mDraftDetail"]


def _i(value, default: int | None = None) -> int | None:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def normalise_swid(swid: str | None) -> str:
    """ESPN's SWID is a brace-wrapped GUID. Accept it either way."""
    value = (swid or "").strip().strip('"')
    if not value:
        return ""
    if not value.startswith("{"):
        value = "{" + value
    if not value.endswith("}"):
        value = value + "}"
    return value.upper()


@dataclass
class LeagueCandidate:
    """A league the fan profile says this account has a team in."""

    league_id: int
    season: int
    name: str = ""
    team_id: int | None = None
    team_name: str = ""
    team_count: int = 0
    #: Where the hint came from, for the diagnostics screen.
    source: str = "fan"


@dataclass
class DiscoveredLeague:
    """A candidate confirmed against ESPN's league endpoint."""

    league_id: int
    season: int
    name: str
    team_count: int
    #: The team this account owns, when we could work it out.
    my_team_id: int | None = None
    my_team_name: str = ""
    scoring_type: str = ""
    is_ppr: bool = False
    ppr_value: float = 0.0
    draft_type: str = ""
    draft_completed: bool = False
    draft_in_progress: bool = False
    draft_pick_count: int = 0
    teams: list[dict] = field(default_factory=list)
    #: Populated only by `league_preview`; the picker itself stays light.
    settings: dict = field(default_factory=dict)
    latency_ms: float = 0.0

    def summary(self) -> dict:
        """What the league picker renders. Contains no credentials."""
        return {
            "league_id": self.league_id,
            "season": self.season,
            "name": self.name,
            "team_count": self.team_count,
            "my_team_id": self.my_team_id,
            "my_team_name": self.my_team_name,
            "scoring_type": self.scoring_type,
            "is_ppr": self.is_ppr,
            "ppr_value": self.ppr_value,
            "draft_type": self.draft_type,
            "draft_completed": self.draft_completed,
            "draft_in_progress": self.draft_in_progress,
            "draft_pick_count": self.draft_pick_count,
            "teams": self.teams,
            "latency_ms": self.latency_ms,
        }


# ---------------------------------------------------------------------------
# Step 1 -- candidates from the fan profile
# ---------------------------------------------------------------------------


def _entry_looks_like_football(entry: dict) -> bool | None:
    """True / False / None when the payload gives no sport signal at all."""
    game_id = entry.get("gameId")
    if game_id is not None:
        return game_id in FFL_GAME_IDS
    for key in ("entryURL", "url", "gameAbbrev", "abbrev"):
        value = str(entry.get(key) or "").lower()
        if value:
            return any(hint in value for hint in FFL_URL_HINTS)
    return None


def _walk_entries(node, found: list[dict]) -> None:
    """Collect every fantasy-entry-shaped dict anywhere in the payload.

    Walking rather than indexing a fixed path is deliberate: ESPN has moved
    this structure before, and a walk keeps working when it moves again.
    """
    if isinstance(node, dict):
        groups = node.get("groups")
        if isinstance(groups, list) and any(
            isinstance(g, dict) and g.get("groupId") is not None for g in groups
        ):
            found.append(node)
        for value in node.values():
            _walk_entries(value, found)
    elif isinstance(node, list):
        for value in node:
            _walk_entries(value, found)


def candidates_from_fan_profile(payload: dict, season: int | None = None) -> list[LeagueCandidate]:
    """Every football league in an ESPN fan profile, optionally one season."""
    entries: list[dict] = []
    _walk_entries(payload or {}, entries)

    candidates: dict[tuple[int, int], LeagueCandidate] = {}
    for entry in entries:
        is_football = _entry_looks_like_football(entry)
        if is_football is False:
            continue

        entry_season = _i(entry.get("seasonId")) or _i(entry.get("season"))
        team_id = _i(entry.get("entryId"))
        team_name = (
            entry.get("name")
            or " ".join(
                p for p in [entry.get("entryLocation"), entry.get("entryNickname")] if p
            ).strip()
            or ""
        )

        for group in entry.get("groups") or []:
            if not isinstance(group, dict):
                continue
            league_id = _i(group.get("groupId"))
            group_season = _i(group.get("seasonId")) or entry_season
            if not league_id or not group_season:
                continue
            if season is not None and group_season != int(season):
                continue

            key = (league_id, group_season)
            candidates[key] = LeagueCandidate(
                league_id=league_id,
                season=group_season,
                name=str(group.get("groupName") or "").strip(),
                team_id=_i(group.get("groupManagerTeamId")) or team_id,
                team_name=str(team_name).strip(),
                team_count=_i(group.get("groupSize"), 0) or 0,
            )

    return sorted(candidates.values(), key=lambda c: (-c.season, c.name.lower(), c.league_id))


# ---------------------------------------------------------------------------
# Step 2 -- confirm each candidate against the league endpoint
# ---------------------------------------------------------------------------


def _members_by_id(payload: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for member in payload.get("members") or []:
        if isinstance(member, dict) and member.get("id"):
            out[normalise_swid(str(member["id"]))] = member
    return out


def _member_name(member: dict | None) -> str:
    if not member:
        return ""
    name = " ".join(
        p for p in [member.get("firstName"), member.get("lastName")] if p
    ).strip()
    return name or str(member.get("displayName") or "")


def parse_league_payload(payload: dict, season: int, swid: str | None = None) -> DiscoveredLeague:
    """Turn one `mSettings`+`mTeam`+`mDraftDetail` response into a league.

    Team ownership is resolved through ESPN's `members` list: member ids use
    the same brace-wrapped GUID format as the SWID cookie, so the team whose
    owner id matches the cookie is the user's -- no typing required.
    """
    settings = payload.get("settings") or {}
    draft_settings = settings.get("draftSettings") or {}
    scoring = settings.get("scoringSettings") or {}
    detail = payload.get("draftDetail") or {}

    members = _members_by_id(payload)
    target_swid = normalise_swid(swid)

    teams: list[dict] = []
    my_team_id: int | None = None
    my_team_name = ""

    pick_order = [t for t in (draft_settings.get("pickOrder") or []) if t]
    slot_by_team = {team_id: idx + 1 for idx, team_id in enumerate(pick_order)}

    for team in payload.get("teams") or []:
        if not isinstance(team, dict):
            continue
        team_id = _i(team.get("id"))
        if team_id is None:
            continue
        owner_ids = [normalise_swid(str(o)) for o in (team.get("owners") or []) if o]
        primary = normalise_swid(str(team.get("primaryOwner") or ""))
        if primary and primary not in owner_ids:
            owner_ids.insert(0, primary)

        name = (
            team.get("name")
            or " ".join(
                p for p in [team.get("location"), team.get("nickname")] if p
            ).strip()
            or f"Team {team_id}"
        )
        is_mine = bool(target_swid) and target_swid in owner_ids
        if is_mine and my_team_id is None:
            my_team_id = team_id
            my_team_name = name

        teams.append(
            {
                "espn_team_id": team_id,
                "name": str(name).strip(),
                "abbrev": str(team.get("abbrev") or ""),
                "owners": [_member_name(members.get(oid)) or "Unknown owner" for oid in owner_ids],
                "logo_url": str(team.get("logo") or ""),
                "draft_slot": slot_by_team.get(team_id),
                "is_mine": is_mine,
            }
        )

    ppr = EspnClient._detect_ppr(EspnClient._parse_scoring_rules(scoring, None))

    return DiscoveredLeague(
        league_id=_i(payload.get("id"), 0) or 0,
        season=_i(payload.get("seasonId"), season) or int(season),
        name=str(settings.get("name") or "").strip() or f"League {payload.get('id') or ''}".strip(),
        team_count=_i(settings.get("size"), len(teams)) or len(teams),
        my_team_id=my_team_id,
        my_team_name=my_team_name,
        scoring_type=str(scoring.get("scoringType") or ""),
        is_ppr=ppr > 0,
        ppr_value=ppr,
        draft_type=str(draft_settings.get("type") or "").upper(),
        draft_completed=bool(detail.get("drafted")),
        draft_in_progress=bool(detail.get("inProgress")),
        draft_pick_count=len(
            [p for p in (detail.get("picks") or []) if _i((p or {}).get("playerId"), 0)]
        ),
        teams=teams,
        settings=settings,
    )


def league_preview(
    client: EspnHttpClient,
    league_id: int,
    season: int,
    swid: str | None = None,
) -> DiscoveredLeague:
    """Read one league completely: rules, teams, draft state, in one request."""
    response = client.league_view(league_id, season, LEAGUE_PREVIEW_VIEWS)
    league = parse_league_payload(response.data, season=season, swid=swid or client.swid)
    league.latency_ms = response.latency_ms
    if not league.league_id:
        league.league_id = int(league_id)
    return league


def discover_leagues(
    client: EspnHttpClient,
    season: int | None = None,
    swid: str | None = None,
    extra_league_ids: list[int] | None = None,
    limit: int = 25,
) -> tuple[list[DiscoveredLeague], list[str]]:
    """Every football league these cookies can reach, plus what went wrong.

    Returns `(leagues, warnings)`. Warnings are user-facing strings, already
    redacted -- a candidate that fails confirmation is reported rather than
    silently dropped, because "we found 2 of your 3 leagues" is a materially
    different message from "you have 2 leagues".
    """
    swid = normalise_swid(swid or client.swid)
    warnings: list[str] = []
    candidates: list[LeagueCandidate] = []

    try:
        profile = client.fan_profile(swid)
        candidates = candidates_from_fan_profile(profile.data, season=season)
    except EspnHttpError as exc:
        warnings.append(
            "ESPN did not return your league list automatically "
            f"({redact(exc)}). You can still enter a league id by hand."
        )

    for league_id in extra_league_ids or []:
        if not any(c.league_id == int(league_id) for c in candidates):
            candidates.append(
                LeagueCandidate(
                    league_id=int(league_id),
                    season=int(season or 0),
                    source="manual",
                )
            )

    leagues: list[DiscoveredLeague] = []
    seen: set[tuple[int, int]] = set()
    for candidate in candidates[:limit]:
        key = (candidate.league_id, candidate.season)
        if key in seen:
            continue
        seen.add(key)
        try:
            league = league_preview(client, candidate.league_id, candidate.season, swid=swid)
        except EspnHttpError as exc:
            # A 404 here is the normal way a non-football league drops out, so
            # it is only worth telling the user about when we had a name for it.
            if candidate.name:
                warnings.append(f"Could not read \"{candidate.name}\": {redact(exc)}")
            continue

        if candidate.name and not league.name:
            league.name = candidate.name
        if league.my_team_id is None and candidate.team_id:
            league.my_team_id = candidate.team_id
            match = next(
                (t for t in league.teams if t["espn_team_id"] == candidate.team_id), None
            )
            if match:
                match["is_mine"] = True
                league.my_team_name = match["name"]
        leagues.append(league)

    leagues.sort(key=lambda lg: (-lg.season, lg.name.lower()))
    return leagues, warnings


# ---------------------------------------------------------------------------
# Roster / rules summary for the confirmation step
# ---------------------------------------------------------------------------


def rules_summary(league: DiscoveredLeague) -> dict:
    """The rules a user is asked to confirm before importing.

    Deliberately the same parsing the importer uses, so what is confirmed here
    is what the engine will actually run on rather than a second rendering of
    it that could drift.
    """
    settings = league.settings or {}
    roster_settings = settings.get("rosterSettings") or {}
    draft_settings = settings.get("draftSettings") or {}
    acquisition = settings.get("acquisitionSettings") or {}
    schedule = settings.get("scheduleSettings") or {}
    scoring = settings.get("scoringSettings") or {}

    slot_counts = EspnClient._parse_slot_counts(roster_settings)
    starters, bench, ir = EspnClient._split_slots(slot_counts)
    scoring_rules = EspnClient._parse_scoring_rules(scoring, None)

    return {
        "roster_slots": starters,
        "bench_slots": bench,
        "ir_slots": ir,
        "all_slot_counts": slot_counts,
        "scoring_type": str(scoring.get("scoringType") or ""),
        "is_ppr": league.is_ppr,
        "ppr_value": league.ppr_value,
        "scoring_rule_count": len(scoring_rules),
        "scoring_rules": [r for r in scoring_rules if r["points"]][:40],
        "draft_type": league.draft_type,
        "draft_order": [t for t in (draft_settings.get("pickOrder") or []) if t],
        "draft_date": draft_settings.get("date"),
        "draft_time_per_selection": draft_settings.get("timePerSelection"),
        "keeper_count": _i(draft_settings.get("keeperCount"), 0),
        "waiver_type": EspnClient._waiver_type(acquisition),
        "uses_faab": bool(acquisition.get("isUsingAcquisitionBudget")),
        "acquisition_budget": _i(acquisition.get("acquisitionBudget"), 0),
        "waiver_process_days": acquisition.get("waiverProcessDays") or [],
        "waiver_hours": acquisition.get("waiverHours"),
        "regular_season_weeks": _i(schedule.get("matchupPeriodCount"), 14),
        "playoff_team_count": _i(schedule.get("playoffTeamCount"), 6),
        "playoff_matchup_length": _i(schedule.get("playoffMatchupPeriodLength"), 1),
        "playoff_seed_tie_rule": schedule.get("playoffSeedingRule") or "",
    }


def roster_slot_labels(roster_settings: dict) -> dict[str, int]:
    """Exposed for tests and for the connect screen's roster preview."""
    return EspnClient._parse_slot_counts(roster_settings or {})


__all__ = [
    "DiscoveredLeague",
    "LeagueCandidate",
    "candidates_from_fan_profile",
    "discover_leagues",
    "league_preview",
    "normalise_swid",
    "parse_league_payload",
    "roster_slot_labels",
    "rules_summary",
]
