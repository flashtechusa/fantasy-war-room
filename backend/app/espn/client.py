"""Thin, defensive wrapper around `espn-api`.

Design notes
------------
* `espn-api` handles auth, endpoint switching and the awkward parts of ESPN's
  API, so we lean on it for the league / teams / rosters / draft.
* For the **draftable player pool** we issue one extra `kona_player_info`
  request through the same authenticated session.  We need the *raw integer
  stat ids* for the season projection, and `espn-api`'s Player class converts
  them to names -- a lossy mapping (six stat ids collide on four names).  The
  raw ids are what let us re-score every projection under our own league rules.
* Every field access is defensive.  ESPN changes its payloads without notice
  and a missing key mid-draft should degrade a number, not crash the app.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from espn_api.football import League as EspnLeague
from espn_api.requests.espn_requests import (
    ESPNAccessDenied,
    ESPNInvalidLeague,
    ESPNUnknownError,
)

from .constants import (
    DEFAULT_POSITION_ID_TO_POSITION,
    NON_STARTER_SLOTS,
    PRO_TEAM_MAP,
    SLOT_ID_TO_LABEL,
    normalise_position,
    normalise_slot_label,
    stat_label,
)

log = logging.getLogger(__name__)

#: Weeks an NFL regular season can span; used to find bye weeks.
REGULAR_SEASON_WEEKS = range(1, 19)


class EspnConnectionError(RuntimeError):
    """Raised when we cannot talk to ESPN for this league."""


@dataclass
class PlayerRecord:
    """One draftable player, normalised out of ESPN's payload."""

    espn_player_id: int
    name: str
    position: str
    pro_team: str
    eligible_slots: list[str] = field(default_factory=list)
    bye_week: int | None = None
    projected_games: float = 17.0
    injury_status: str = "ACTIVE"
    injured: bool = False
    percent_owned: float = 0.0
    percent_started: float = 0.0
    adp: float | None = None
    espn_rank: int | None = None
    espn_position_rank: int | None = None
    espn_projected_points: float = 0.0
    rookie: bool = False
    #: {stat_id_as_str: projected season total}
    raw_stats: dict[str, float] = field(default_factory=dict)
    #: {week: {stat_id_as_str: projected total for that week}}
    weekly_stats: dict[int, dict[str, float]] = field(default_factory=dict)
    #: {week: ESPN's own applied total for that week}
    weekly_points: dict[int, float] = field(default_factory=dict)
    #: FREEAGENT / WAIVERS / ONTEAM
    availability: str = ""
    on_team_id: int | None = None


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _i(value: Any, default: int | None = None) -> int | None:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


class EspnClient:
    """Authenticated handle on one ESPN league/season."""

    def __init__(
        self,
        league_id: int,
        season: int,
        swid: str | None = None,
        espn_s2: str | None = None,
    ) -> None:
        self.league_id = league_id
        self.season = season
        self.swid = swid or None
        self.espn_s2 = espn_s2 or None
        self._league: EspnLeague | None = None
        self._raw_settings: dict | None = None
        self._bye_weeks: dict[str, int] | None = None

    # -- connection --------------------------------------------------------

    @property
    def is_private_auth(self) -> bool:
        return bool(self.swid and self.espn_s2)

    def connect(self) -> EspnLeague:
        """Fetch the league. Raises EspnConnectionError with an actionable message."""
        if self._league is not None:
            return self._league
        try:
            self._league = EspnLeague(
                league_id=self.league_id,
                year=self.season,
                swid=self.swid,
                espn_s2=self.espn_s2,
            )
        except ESPNAccessDenied as exc:
            hint = (
                "This looks like a private league. Set FWR_ESPN_SWID and FWR_ESPN_S2 "
                "from your browser cookies."
                if not self.is_private_auth
                else "The SWID/espn_s2 cookies were rejected -- they may have expired. "
                "Re-copy them from your browser."
            )
            raise EspnConnectionError(f"ESPN denied access to league {self.league_id}. {hint}") from exc
        except ESPNInvalidLeague as exc:
            raise EspnConnectionError(
                f"ESPN has no league {self.league_id} for season {self.season}. "
                "Check FWR_ESPN_LEAGUE_ID and FWR_ESPN_SEASON."
            ) from exc
        except ESPNUnknownError as exc:
            raise EspnConnectionError(f"ESPN returned an unexpected response: {exc}") from exc
        except Exception as exc:  # network errors, JSON decode, ...
            raise EspnConnectionError(f"Could not reach ESPN: {exc}") from exc
        return self._league

    def check_connection(self) -> dict:
        """Lightweight connectivity probe used by the /api/health endpoint."""
        league = self.connect()
        return {
            "connected": True,
            "league_id": self.league_id,
            "season": self.season,
            "league_name": getattr(league.settings, "name", ""),
            "team_count": getattr(league.settings, "team_count", len(league.teams)),
            "private_auth": self.is_private_auth,
        }

    # -- raw settings ------------------------------------------------------

    def raw_settings(self) -> dict:
        """ESPN's `settings` blob.

        `espn-api` keeps only part of it, and its `position_slot_counts` zips
        slot labels against counts positionally, which mislabels slots for many
        leagues.  We parse the raw payload instead.
        """
        if self._raw_settings is not None:
            return self._raw_settings
        league = self.connect()
        try:
            data = league.espn_request.league_get(params={"view": "mSettings"})
            self._raw_settings = data.get("settings", {}) or {}
        except Exception as exc:  # pragma: no cover - network dependent
            log.warning("Falling back to espn-api settings: %s", exc)
            self._raw_settings = {}
        return self._raw_settings

    # -- Phase 1: league settings -----------------------------------------

    def league_settings(self) -> dict:
        league = self.connect()
        raw = self.raw_settings()
        s = league.settings

        roster_settings = raw.get("rosterSettings", {}) or {}
        draft_settings = raw.get("draftSettings", {}) or {}
        acq = raw.get("acquisitionSettings", {}) or {}
        sched = raw.get("scheduleSettings", {}) or {}
        scoring = raw.get("scoringSettings", {}) or {}

        slot_counts = self._parse_slot_counts(roster_settings)
        starters, bench, ir = self._split_slots(slot_counts)

        scoring_rules = self._parse_scoring_rules(scoring, s)
        ppr = self._detect_ppr(scoring_rules)

        team_count = raw.get("size") or getattr(s, "team_count", len(league.teams))

        return {
            "espn_league_id": self.league_id,
            "season": self.season,
            "name": raw.get("name") or getattr(s, "name", "") or f"League {self.league_id}",
            "team_count": int(team_count or len(league.teams) or 10),
            "scoring_type": scoring.get("scoringType") or getattr(s, "scoring_type", "") or "",
            "is_ppr": ppr > 0,
            "ppr_value": ppr,
            "roster_slots": starters,
            "bench_slots": bench,
            "ir_slots": ir,
            "all_slot_counts": slot_counts,
            "draft_type": (draft_settings.get("type") or "SNAKE").upper(),
            "draft_order": [t for t in (draft_settings.get("pickOrder") or []) if t],
            "draft_date": draft_settings.get("date"),
            "draft_time_per_selection": draft_settings.get("timePerSelection"),
            "draft_completed": bool(self._draft_completed(league)),
            "keeper_count": _i(draft_settings.get("keeperCount"), 0),
            "waiver_type": self._waiver_type(acq),
            "uses_faab": bool(acq.get("isUsingAcquisitionBudget")),
            "acquisition_budget": _i(acq.get("acquisitionBudget"), 0),
            "waiver_process_days": acq.get("waiverProcessDays") or [],
            "waiver_hours": acq.get("waiverHours"),
            "acquisition_limit": acq.get("acquisitionLimit"),
            "regular_season_weeks": _i(sched.get("matchupPeriodCount"), 14),
            "playoff_team_count": _i(sched.get("playoffTeamCount"), 6),
            "playoff_matchup_length": _i(sched.get("playoffMatchupPeriodLength"), 1),
            "playoff_seed_tie_rule": sched.get("playoffSeedingRule") or "",
            "trade_deadline": _i(getattr(s, "trade_deadline", 0), 0),
            "veto_votes_required": _i(getattr(s, "veto_votes_required", 0), 0),
            "scoring_rules": scoring_rules,
            "source": "espn",
            "raw_settings": {
                "rosterSettings": roster_settings,
                "draftSettings": {k: v for k, v in draft_settings.items() if k != "pickOrder"},
                "acquisitionSettings": acq,
                "scheduleSettings": {
                    k: v for k, v in sched.items() if k != "matchupPeriods"
                },
            },
        }

    @staticmethod
    def _waiver_type(acquisition_settings: dict) -> str:
        if acquisition_settings.get("isUsingAcquisitionBudget"):
            return "FAAB"
        if acquisition_settings.get("waiverOrderReset"):
            return "REVERSE_STANDINGS"
        return "ROLLING_LIST"

    @staticmethod
    def _draft_completed(league: EspnLeague) -> bool:
        try:
            return bool(league.draft)
        except Exception:  # pragma: no cover
            return False

    @staticmethod
    def _parse_slot_counts(roster_settings: dict) -> dict[str, int]:
        """{"QB": 1, "RB": 2, ..., "BE": 6, "IR": 1} keyed by slot label."""
        counts: dict[str, int] = {}
        raw_counts = roster_settings.get("lineupSlotCounts", {}) or {}
        for slot_id, count in raw_counts.items():
            n = _i(count, 0)
            if n <= 0:
                continue
            label = SLOT_ID_TO_LABEL.get(_i(slot_id, -1))
            if not label:
                continue
            counts[label] = counts.get(label, 0) + n
        return counts

    @staticmethod
    def _split_slots(slot_counts: dict[str, int]) -> tuple[dict[str, int], int, int]:
        starters = {k: v for k, v in slot_counts.items() if k not in NON_STARTER_SLOTS}
        bench = slot_counts.get("BE", 0)
        ir = slot_counts.get("IR", 0) + slot_counts.get("ER", 0)
        return starters, bench, ir

    @staticmethod
    def _parse_scoring_rules(scoring: dict, settings_obj: Any) -> list[dict]:
        items = scoring.get("scoringItems")
        if not items:
            # Fall back to what espn-api parsed.
            items = [
                {
                    "statId": entry.get("id"),
                    "points": entry.get("points", 0),
                    "pointsOverrides": {},
                }
                for entry in getattr(settings_obj, "scoring_format", []) or []
            ]
        rules: list[dict] = []
        for item in items:
            stat_id = _i(item.get("statId"))
            if stat_id is None:
                continue
            abbrev, label = stat_label(stat_id)
            overrides = item.get("pointsOverrides") or {}
            rules.append(
                {
                    "stat_id": stat_id,
                    "abbrev": abbrev,
                    "label": label,
                    "points": _f(item.get("points"), 0.0),
                    "points_overrides": {str(k): _f(v) for k, v in overrides.items()},
                }
            )
        return rules

    @staticmethod
    def _detect_ppr(scoring_rules: list[dict]) -> float:
        """Points per reception, read from the rules rather than guessed."""
        for rule in scoring_rules:
            if rule["stat_id"] == 53:  # "Each reception"
                return rule["points"]
        for rule in scoring_rules:
            if rule["stat_id"] == 41:  # "Receptions"
                return rule["points"]
        return 0.0

    # -- Phase 1: teams & rosters -----------------------------------------

    def teams(self) -> list[dict]:
        league = self.connect()
        raw = self.raw_settings()
        pick_order = [t for t in ((raw.get("draftSettings") or {}).get("pickOrder") or []) if t]
        slot_by_team = {team_id: idx + 1 for idx, team_id in enumerate(pick_order)}

        out: list[dict] = []
        for team in league.teams:
            owners = []
            for owner in getattr(team, "owners", []) or []:
                if isinstance(owner, dict):
                    name = " ".join(
                        p for p in [owner.get("firstName"), owner.get("lastName")] if p
                    ).strip()
                    owners.append(name or owner.get("displayName") or "Unknown owner")
                elif owner:
                    owners.append(str(owner))

            roster = []
            for player in getattr(team, "roster", []) or []:
                roster.append(
                    {
                        "espn_player_id": getattr(player, "playerId", None),
                        "name": getattr(player, "name", ""),
                        "position": normalise_position(getattr(player, "position", "")),
                        "slot": normalise_slot_label(getattr(player, "lineupSlot", "")),
                        "pro_team": getattr(player, "proTeam", ""),
                        "injury_status": getattr(player, "injuryStatus", "") or "ACTIVE",
                    }
                )

            out.append(
                {
                    "espn_team_id": team.team_id,
                    "name": getattr(team, "team_name", "") or f"Team {team.team_id}",
                    "abbrev": getattr(team, "team_abbrev", "") or "",
                    "owners": owners,
                    "logo_url": getattr(team, "logo_url", "") or "",
                    "division_name": getattr(team, "division_name", "") or "",
                    "draft_slot": slot_by_team.get(team.team_id),
                    "wins": getattr(team, "wins", 0) or 0,
                    "losses": getattr(team, "losses", 0) or 0,
                    "ties": getattr(team, "ties", 0) or 0,
                    "roster": roster,
                }
            )
        return out

    # -- Phase 1: draft results -------------------------------------------

    def draft_results(self, season: int | None = None) -> list[dict]:
        """Completed draft picks. Returns [] if the draft has not happened."""
        league = self.connect()
        season = season or self.season
        team_count = max(len(league.teams), 1)

        picks: list[dict] = []
        for idx, pick in enumerate(getattr(league, "draft", []) or []):
            round_num = _i(getattr(pick, "round_num", None), 0)
            round_pick = _i(getattr(pick, "round_pick", None), 0)
            overall = (
                (round_num - 1) * team_count + round_pick if round_num and round_pick else idx + 1
            )
            team = getattr(pick, "team", None)
            picks.append(
                {
                    "season": season,
                    "overall_pick": overall,
                    "round_num": round_num,
                    "round_pick": round_pick,
                    "espn_team_id": getattr(team, "team_id", None),
                    "team_name": getattr(team, "team_name", "") or "",
                    "espn_player_id": _i(getattr(pick, "playerId", None)),
                    "player_name": getattr(pick, "playerName", "") or "",
                    "bid_amount": _i(getattr(pick, "bid_amount", 0), 0),
                    "keeper": bool(getattr(pick, "keeper_status", False)),
                }
            )
        picks.sort(key=lambda p: p["overall_pick"])
        return picks

    def previous_draft_results(self, seasons: list[int] | None = None) -> dict[int, list[dict]]:
        """Draft results for prior seasons, best-effort."""
        league = self.connect()
        candidates = seasons or list(getattr(league, "previousSeasons", []) or [])
        results: dict[int, list[dict]] = {}
        for year in sorted(candidates, reverse=True)[:3]:
            try:
                prior = EspnClient(self.league_id, year, self.swid, self.espn_s2)
                picks = prior.draft_results(season=year)
                if picks:
                    results[year] = picks
            except Exception as exc:  # pragma: no cover - network dependent
                log.info("No draft history for %s: %s", year, exc)
        return results

    # -- Phase 2: player pool ---------------------------------------------

    def bye_weeks(self) -> dict[str, int]:
        """{pro team abbrev: bye week}."""
        if self._bye_weeks is not None:
            return self._bye_weeks
        league = self.connect()
        byes: dict[str, int] = {}
        try:
            schedule = league._get_all_pro_schedule()  # noqa: SLF001 - no public accessor
            for pro_team_id, games_by_week in (schedule or {}).items():
                abbrev = PRO_TEAM_MAP.get(_i(pro_team_id, 0))
                if not abbrev or abbrev == "None":
                    continue
                played = {_i(w, 0) for w in (games_by_week or {})}
                missing = [w for w in REGULAR_SEASON_WEEKS if w not in played]
                # A real bye sits mid-season; weeks missing at the tail are just
                # the schedule not extending that far.
                mid_season = [w for w in missing if 4 <= w <= 14]
                if mid_season:
                    byes[abbrev] = mid_season[0]
        except Exception as exc:  # pragma: no cover - network dependent
            log.warning("Could not derive bye weeks: %s", exc)
        self._bye_weeks = byes
        return byes

    @property
    def current_week(self) -> int:
        """The scoring period ESPN considers current. 1 before the season opens."""
        league = self.connect()
        return max(int(getattr(league, "current_week", 1) or 1), 1)

    def player_pool(
        self,
        limit: int = 600,
        ppr: bool = False,
        week: int | None = None,
        free_agents_only: bool = False,
    ) -> list[PlayerRecord]:
        """The player pool with raw projected stat totals.

        One `kona_player_info` request against the league endpoint.  Because it
        goes through the *league*, ESPN also returns `appliedTotal` already
        scored under this league's rules -- we keep that as a cross-check
        alongside the projection we compute ourselves.

        `week` asks ESPN for that scoring period, which makes the weekly splits
        appear in the payload; `free_agents_only` narrows to the waiver wire.
        """
        league = self.connect()
        rank_type = "PPR" if ppr else "STANDARD"

        player_filter: dict = {
            "limit": int(limit),
            "sortDraftRanks": {
                "sortPriority": 100,
                "sortAsc": True,
                "value": rank_type,
            },
        }
        if free_agents_only:
            player_filter["filterStatus"] = {"value": ["FREEAGENT", "WAIVERS"]}
            # Waiver value tracks demand, so lead with what the market wants.
            player_filter["sortPercOwned"] = {"sortPriority": 1, "sortAsc": False}

        filters = {"players": player_filter}
        params = {
            "view": "kona_player_info",
            "scoringPeriodId": int(week) if week else 0,
        }
        headers = {"x-fantasy-filter": json.dumps(filters)}

        try:
            data = league.espn_request.league_get(params=params, headers=headers)
        except Exception as exc:
            raise EspnConnectionError(f"Could not load the ESPN player pool: {exc}") from exc

        entries = data.get("players") or []
        if not entries:
            if free_agents_only:
                return []      # a picked-clean wire is a valid answer, not an error
            raise EspnConnectionError(
                "ESPN returned an empty player pool. The season may not be open yet."
            )

        byes = self.bye_weeks()
        records: list[PlayerRecord] = []
        for entry in entries:
            record = self._parse_player_entry(entry, rank_type, byes)
            if record is not None:
                records.append(record)
        return records

    def _parse_player_entry(
        self, entry: dict, rank_type: str, byes: dict[str, int]
    ) -> PlayerRecord | None:
        player = entry.get("player") or entry.get("playerPoolEntry", {}).get("player") or {}
        player_id = _i(player.get("id") or entry.get("id"))
        name = player.get("fullName") or ""
        if player_id is None or not name:
            return None

        position = DEFAULT_POSITION_ID_TO_POSITION.get(_i(player.get("defaultPositionId"), -1))
        eligible_slots = [
            SLOT_ID_TO_LABEL.get(_i(s, -1), "")
            for s in (player.get("eligibleSlots") or [])
        ]
        eligible_slots = [s for s in eligible_slots if s and s not in NON_STARTER_SLOTS]
        if position is None:
            # Fall back to the first single-position eligible slot.
            for slot in eligible_slots:
                if slot in {"QB", "RB", "WR", "TE", "K", "DST"}:
                    position = slot
                    break
        if position is None:
            return None

        pro_team = PRO_TEAM_MAP.get(_i(player.get("proTeamId"), 0), "FA")
        if pro_team == "None":
            pro_team = "FA"

        ownership = player.get("ownership") or {}
        adp = _f(ownership.get("averageDraftPosition"), 0.0) or None

        draft_ranks = player.get("draftRanksByRankType") or {}
        rank_entry = draft_ranks.get(rank_type) or draft_ranks.get("STANDARD") or {}
        espn_rank = _i(rank_entry.get("rank"))

        raw_stats, applied_total, projected_games = self._season_projection(player)
        weekly_stats, weekly_points = self._weekly_projections(player)

        injury_status = (player.get("injuryStatus") or "ACTIVE").upper()

        return PlayerRecord(
            espn_player_id=player_id,
            name=name,
            position=normalise_position(position),
            pro_team=pro_team,
            eligible_slots=eligible_slots,
            bye_week=byes.get(pro_team),
            projected_games=projected_games,
            injury_status=injury_status,
            injured=bool(player.get("injured")),
            percent_owned=round(_f((player.get("ownership") or {}).get("percentOwned"), 0.0), 2),
            percent_started=round(
                _f((player.get("ownership") or {}).get("percentStarted"), 0.0), 2
            ),
            adp=adp,
            espn_rank=espn_rank,
            espn_position_rank=_i(player.get("positionalRanking")),
            espn_projected_points=round(applied_total, 2),
            rookie=25 in (player.get("eligibleSlots") or []),
            raw_stats=raw_stats,
            weekly_stats=weekly_stats,
            weekly_points=weekly_points,
            availability=(entry.get("status") or "").upper(),
            on_team_id=_i(entry.get("onTeamId")),
        )

    def _weekly_projections(
        self, player: dict
    ) -> tuple[dict[int, dict[str, float]], dict[int, float]]:
        """Per-week projections, pulled from the payload we already have.

        ESPN returns weekly splits alongside the season total in the same
        `stats` array -- a weekly entry has `statSplitTypeId == 1` and a real
        `scoringPeriodId`. Extracting them here means in-season features cost
        no additional requests.
        """
        stats_by_week: dict[int, dict[str, float]] = {}
        points_by_week: dict[int, float] = {}

        for stats in player.get("stats") or []:
            if _i(stats.get("statSourceId"), -1) != 1:
                continue          # actuals, not projections
            if _i(stats.get("seasonId"), -1) != self.season:
                continue
            if _i(stats.get("statSplitTypeId"), -1) != 1:
                continue          # season totals are handled separately
            week = _i(stats.get("scoringPeriodId"), 0) or 0
            if week <= 0:
                continue
            stats_by_week[week] = {str(k): _f(v) for k, v in (stats.get("stats") or {}).items()}
            points_by_week[week] = round(_f(stats.get("appliedTotal"), 0.0), 2)

        return (stats_by_week, points_by_week)

    def _season_projection(self, player: dict) -> tuple[dict[str, float], float, float]:
        """Pull the full-season projection: raw stat totals + ESPN's applied total.

        ESPN marks projections with `statSourceId == 1`; the season total is the
        entry with `statSplitTypeId == 0` and `scoringPeriodId == 0`.
        """
        best: dict | None = None
        for stats in player.get("stats") or []:
            if _i(stats.get("statSourceId"), -1) != 1:
                continue
            if _i(stats.get("seasonId"), -1) != self.season:
                continue
            split = _i(stats.get("statSplitTypeId"), -1)
            period = _i(stats.get("scoringPeriodId"), -1)
            if split == 0 and period == 0:
                best = stats
                break
            if best is None and period == 0:
                best = stats

        if best is None:
            return ({}, 0.0, 17.0)

        raw = {str(k): _f(v) for k, v in (best.get("stats") or {}).items()}
        applied_total = _f(best.get("appliedTotal"), 0.0)
        applied_avg = _f(best.get("appliedAverage"), 0.0)

        # ESPN doesn't publish a projected-games field, but total/average is it.
        if applied_avg > 0.01 and applied_total > 0:
            games = applied_total / applied_avg
        else:
            games = 17.0
        games = max(1.0, min(17.0, round(games, 1)))
        return (raw, applied_total, games)

    # -- Phase 5: live draft sync -----------------------------------------

    def live_draft_picks(self) -> list[dict]:
        """Re-poll ESPN's draft detail. Safe to call repeatedly during a draft.

        Returns picks made so far; empty list before the draft starts.
        """
        league = self.connect()
        try:
            league.refresh_draft()
        except Exception as exc:
            raise EspnConnectionError(f"Could not refresh the ESPN draft: {exc}") from exc
        return self.draft_results()
