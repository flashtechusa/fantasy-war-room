"""Yahoo Fantasy Sports, behind the same interface as ESPN.

The shape of this module deliberately mirrors `app.espn.client`: the same four
questions (settings, teams, draft, player pool) answered from a different API,
returning the same dictionaries and the same `PlayerRecord`, so the importer,
the engine and every screen stay exactly as they are.

Three things Yahoo does differently, and what we do about them:

* **No projections.**  Yahoo publishes actual stats, ownership and draft
  analysis, but no projected stat line for anybody.  So a Yahoo league gets its
  player *pool* from Yahoo and its *projections* from a projection source
  (`app.projections.espn_public`, or FantasyPros if a key is configured).  This
  is why `player_pool` returns records with empty `raw_stats` and says so
  rather than inventing numbers.
* **25 players per request.**  Yahoo caps its player collection hard, so the
  pool is paged.  Each page is one request; 600 players is 24 of them.
* **Its own stat numbering.**  Translated to ESPN's at the edge -- see
  `app.yahoo.constants` for the mapping and its caveats.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..espn.client import PlayerRecord
from ..espn.constants import NON_STARTER_SLOTS
from .constants import (
    APPROXIMATE_STAT_IDS,
    espn_stat_id,
    is_private_stat,
    normalise_yahoo_position,
    normalise_yahoo_slot,
)
from .oauth import YahooAuthError, YahooOAuth
from .payload import (
    collection,
    deep_collection,
    integer,
    items,
    merge,
    num,
    sub_resource,
    text,
    truthy,
)

log = logging.getLogger(__name__)

API_ROOT = "https://fantasysports.yahooapis.com/fantasy/v2"

#: Yahoo's hard cap on one page of the player collection.
PLAYER_PAGE_SIZE = 25

#: Yahoo injury codes -> the labels the rest of the app uses.
INJURY_CODE_TO_STATUS = {
    "": "ACTIVE",
    "Q": "QUESTIONABLE",
    "D": "DOUBTFUL",
    "O": "OUT",
    "IR": "INJURY_RESERVE",
    "IR-R": "INJURY_RESERVE",
    "IR-NR": "INJURY_RESERVE",
    "PUP": "INJURY_RESERVE",
    "NFI": "INJURY_RESERVE",
    "NA": "OUT",
    "SUSP": "SUSPENSION",
    "PUP-R": "INJURY_RESERVE",
    "DTD": "DAY_TO_DAY",
}

#: Yahoo ownership types -> our availability vocabulary.
OWNERSHIP_TO_AVAILABILITY = {
    "team": "ONTEAM",
    "freeagents": "FREEAGENT",
    "waivers": "WAIVERS",
}


class YahooConnectionError(RuntimeError):
    """Raised when we cannot talk to Yahoo for this league."""


class YahooClient:
    """Authorised handle on one Yahoo league/season."""

    def __init__(
        self,
        league_id: int,
        season: int,
        oauth: YahooOAuth,
        game_key: str | None = None,
    ) -> None:
        self.league_id = int(league_id)
        self.season = int(season)
        self.oauth = oauth
        self._game_key = game_key
        self._settings: dict | None = None
        self._meta: dict | None = None
        self._draft: list[dict] | None = None

    # -- plumbing ----------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> dict:
        """One authorised GET, returned as parsed `fantasy_content`."""
        url = f"{API_ROOT}/{path.lstrip('/')}"
        request_params = {"format": "json", **(params or {})}
        try:
            response = self.oauth.get(url, params=request_params)
        except YahooAuthError as exc:
            raise YahooConnectionError(str(exc)) from exc

        if response.status_code == 401:
            raise YahooConnectionError(
                "Yahoo rejected the connection. Reconnect your Yahoo account on the "
                "League screen."
            )
        if response.status_code == 404:
            raise YahooConnectionError(
                f"Yahoo has no league {self.league_id} for {self.season}. Check the league id "
                "and season -- the id is the number in your league URL."
            )
        if response.status_code >= 400:
            raise YahooConnectionError(
                f"Yahoo returned {response.status_code} for {path}: "
                f"{(response.text or '').strip()[:200]}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise YahooConnectionError(
                f"Yahoo returned a response we could not read for {path}."
            ) from exc
        content = body.get("fantasy_content")
        if not isinstance(content, dict):
            raise YahooConnectionError(f"Yahoo returned no fantasy content for {path}.")
        return content

    def game_key(self) -> str:
        """The NFL game key for this season, e.g. "461" for 2026.

        Yahoo's league keys are `{game_key}.l.{league_id}`, and the game key
        changes every season, so it is looked up rather than hard-coded.
        """
        if self._game_key:
            return self._game_key
        content = self._get("games", {"game_codes": "nfl", "seasons": str(self.season)})
        for fragment in collection(content, "games", "game"):
            game = merge(fragment)
            key = text(game.get("game_key"))
            if key and integer(game.get("season"), self.season) == self.season:
                self._game_key = key
                return key
        raise YahooConnectionError(
            f"Yahoo does not list an NFL season for {self.season}."
        )

    @property
    def league_key(self) -> str:
        return f"{self.game_key()}.l.{self.league_id}"

    def check_connection(self) -> dict:
        meta = self._league_meta()
        return {
            "connected": True,
            "platform": "yahoo",
            "league_id": self.league_id,
            "season": self.season,
            "league_name": text(meta.get("name")),
            "team_count": integer(meta.get("num_teams"), 0) or 0,
            "private_auth": True,
        }

    # -- league ------------------------------------------------------------

    def _load_settings(self) -> tuple[dict, dict]:
        """(league metadata, settings) for this league, fetched once."""
        if self._meta is not None and self._settings is not None:
            return (self._meta, self._settings)
        content = self._get(f"league/{self.league_key}/settings")
        league = merge(content.get("league"))
        self._meta = league
        self._settings = merge(league.get("settings"))
        return (self._meta, self._settings)

    def _league_meta(self) -> dict:
        return self._load_settings()[0]

    @property
    def current_week(self) -> int:
        meta = self._league_meta()
        return max(integer(meta.get("current_week"), 1) or 1, 1)

    def league_settings(self) -> dict:
        meta, settings = self._load_settings()

        slot_counts = self._parse_slot_counts(settings)
        starters = {k: v for k, v in slot_counts.items() if k not in NON_STARTER_SLOTS}
        bench = slot_counts.get("BE", 0)
        ir = slot_counts.get("IR", 0)

        scoring_rules, mapping_notes = self._parse_scoring_rules(settings)
        ppr = _points_for(scoring_rules, 53)

        playoff_start = integer(settings.get("playoff_start_week"), 15) or 15
        draft_status = text(meta.get("draft_status")).lower()
        draft_picks = self.draft_results()

        return {
            # The column is named for ESPN, but it holds whichever platform's
            # league id this install is pointed at.
            "espn_league_id": self.league_id,
            "season": self.season,
            "name": text(meta.get("name")) or f"League {self.league_id}",
            "team_count": integer(meta.get("num_teams"), 10) or 10,
            "scoring_type": _scoring_type(text(meta.get("scoring_type"))),
            "is_ppr": ppr > 0,
            "ppr_value": ppr,
            "roster_slots": starters,
            "bench_slots": bench,
            "ir_slots": ir,
            "all_slot_counts": slot_counts,
            "draft_type": "AUCTION" if truthy(settings.get("is_auction_draft")) else "SNAKE",
            "draft_order": _draft_order(draft_picks),
            "draft_date": text(meta.get("draft_time")) or text(settings.get("draft_time")),
            "draft_time_per_selection": integer(settings.get("draft_pick_time")),
            "draft_completed": draft_status == "postdraft" or bool(draft_picks),
            "keeper_count": integer(settings.get("num_keepers"), 0) or 0,
            "waiver_type": _waiver_type(settings),
            "uses_faab": truthy(settings.get("uses_faab")),
            "acquisition_budget": _faab_budget(settings),
            "waiver_process_days": [],
            "regular_season_weeks": max(playoff_start - 1, 1),
            "playoff_team_count": integer(settings.get("num_playoff_teams"), 6) or 6,
            # Yahoo does not publish playoff round length; one week is its default.
            "playoff_matchup_length": 1,
            "playoff_seed_tie_rule": "",
            "trade_deadline": _epoch_millis(text(settings.get("trade_end_date"))),
            "veto_votes_required": integer(settings.get("required_votes_to_veto"), 0) or 0,
            "scoring_rules": scoring_rules,
            "source": "yahoo",
            "raw_settings": {
                "yahoo_league_key": self.league_key,
                "yahoo_url": text(meta.get("url")),
                "draft_status": draft_status,
                "waiver_rule": text(settings.get("waiver_rule")),
                "trade_end_date": text(settings.get("trade_end_date")),
                "playoff_start_week": playoff_start,
                "renewed_from": text(meta.get("renew")),
                # How faithfully Yahoo's scoring survived the translation to
                # ESPN stat ids. Shown on the League screen; see
                # `app.yahoo.constants` for why it is never empty by accident.
                "scoring_translation": mapping_notes,
            },
        }

    @staticmethod
    def _parse_slot_counts(settings: dict) -> dict[str, int]:
        counts: dict[str, int] = {}
        for fragment in collection(settings, "roster_positions", "roster_position"):
            position = merge(fragment)
            label = normalise_yahoo_slot(text(position.get("position")))
            count = integer(position.get("count"), 0) or 0
            if not label or count <= 0:
                continue
            counts[label] = counts.get(label, 0) + count
        return counts

    def _parse_scoring_rules(self, settings: dict) -> tuple[list[dict], dict]:
        """Yahoo's scoring, expressed in ESPN stat ids.

        Returns the rules plus a report of what the translation cost: which
        Yahoo categories share an ESPN id, which have no equivalent at all, and
        which map onto a band with different edges.  The report exists because
        a scoring page that quietly omits three of a league's rules is worse
        than one that admits it.
        """
        labels: dict[int, dict] = {}
        for fragment in collection(settings.get("stat_categories") or {}, "stats", "stat"):
            stat = merge(fragment)
            stat_id = integer(stat.get("stat_id"))
            if stat_id is None:
                continue
            labels[stat_id] = {
                "abbrev": text(stat.get("display_name")) or f"YS{stat_id}",
                "label": text(stat.get("name")) or f"Yahoo stat {stat_id}",
            }

        modifiers: list[tuple[int, float]] = []
        for fragment in collection(settings.get("stat_modifiers") or {}, "stats", "stat"):
            stat = merge(fragment)
            stat_id = integer(stat.get("stat_id"))
            if stat_id is None:
                continue
            modifiers.append((stat_id, num(stat.get("value"), 0.0)))

        grouped: dict[int, list[tuple[int, float]]] = {}
        for yahoo_id, points in modifiers:
            grouped.setdefault(espn_stat_id(yahoo_id), []).append((yahoo_id, points))

        rules: list[dict] = []
        combined: list[str] = []
        unmapped: list[str] = []
        approximate: list[str] = []

        for stat_id, entries in sorted(grouped.items()):
            values = [points for _, points in entries]
            points = sum(values) / len(values)
            first_yahoo_id = entries[0][0]
            naming = labels.get(first_yahoo_id, {})
            names = [labels.get(yid, {}).get("label", f"Yahoo stat {yid}") for yid, _ in entries]

            if len(entries) > 1:
                combined.append(
                    f"{', '.join(names)} share one ESPN category"
                    + (
                        f" and score differently ({', '.join(str(v) for v in values)}); "
                        f"{round(points, 3)} was used"
                        if len(set(values)) > 1
                        else ""
                    )
                )
            if is_private_stat(stat_id):
                unmapped.extend(names)
            if any(yid in APPROXIMATE_STAT_IDS for yid, _ in entries):
                approximate.extend(names)

            rules.append(
                {
                    "stat_id": stat_id,
                    "abbrev": naming.get("abbrev") or f"YS{first_yahoo_id}",
                    "label": naming.get("label") or f"Yahoo stat {first_yahoo_id}",
                    "points": round(points, 4),
                    "points_overrides": {},
                }
            )

        notes = {
            "combined": combined,
            # These rules are kept so the scoring page is complete, but no
            # projection source supplies the stats, so they contribute nothing.
            "unmapped": sorted(set(unmapped)),
            "approximate": sorted(set(approximate)),
        }
        if notes["unmapped"]:
            log.info(
                "Yahoo scoring categories with no projection equivalent: %s",
                ", ".join(notes["unmapped"]),
            )
        return (rules, notes)

    # -- teams -------------------------------------------------------------

    def teams(self) -> list[dict]:
        """Every team with its roster, record and manager."""
        content = self._get(f"league/{self.league_key}/teams/roster")
        league = merge(content.get("league"))
        teams = [merge(fragment) for fragment in collection(league, "teams", "team")]

        standings = self._standings()
        draft_slots = self._draft_slots()

        out: list[dict] = []
        for team in teams:
            team_id = integer(team.get("team_id"))
            if team_id is None:
                continue
            managers = [merge(entry) for entry in collection(team, "managers", "manager")]
            record = standings.get(team_id, {})

            out.append(
                {
                    "espn_team_id": team_id,
                    "name": text(team.get("name")) or f"Team {team_id}",
                    "abbrev": _abbrev(text(team.get("name"))),
                    "owners": [
                        text(m.get("nickname")) or text(m.get("manager_id")) for m in managers
                    ],
                    "owner_ids": [text(m.get("guid")).upper() for m in managers if m.get("guid")],
                    # Yahoo tells us outright which team belongs to the signed-in
                    # account, which is a far better answer than matching names.
                    "is_current_login": any(
                        truthy(m.get("is_owned_by_current_login")) for m in managers
                    ),
                    "logo_url": _team_logo(team),
                    "division_name": text(team.get("division_id")),
                    "draft_slot": draft_slots.get(team_id),
                    "wins": record.get("wins", 0),
                    "losses": record.get("losses", 0),
                    "ties": record.get("ties", 0),
                    "roster": self._parse_roster(team),
                }
            )
        return out

    @staticmethod
    def _parse_roster(team: dict) -> list[dict]:
        entries: list[dict] = []
        for fragment in deep_collection(team.get("roster"), "players", "player"):
            player = merge(fragment)
            player_id = integer(player.get("player_id"))
            if player_id is None:
                continue
            selected = merge(player.get("selected_position"))
            entries.append(
                {
                    "espn_player_id": player_id,
                    "name": text(merge(player.get("name")).get("full")),
                    "position": normalise_yahoo_position(text(player.get("display_position"))),
                    "slot": normalise_yahoo_slot(text(selected.get("position"))),
                    "pro_team": text(player.get("editorial_team_abbr")).upper(),
                    "injury_status": _injury_status(player),
                }
            )
        return entries

    def _standings(self) -> dict[int, dict]:
        """Win/loss records, which the teams collection does not carry."""
        try:
            content = self._get(f"league/{self.league_key}/standings")
        except YahooConnectionError as exc:  # pragma: no cover - network dependent
            log.info("Yahoo standings unavailable: %s", exc)
            return {}
        league = merge(content.get("league"))
        out: dict[int, dict] = {}
        for fragment in deep_collection(league.get("standings"), "teams", "team"):
            team = merge(fragment)
            team_id = integer(team.get("team_id"))
            if team_id is None:
                continue
            outcome = merge(merge(team.get("team_standings")).get("outcome_totals"))
            out[team_id] = {
                "wins": integer(outcome.get("wins"), 0) or 0,
                "losses": integer(outcome.get("losses"), 0) or 0,
                "ties": integer(outcome.get("ties"), 0) or 0,
            }
        return out

    def _draft_slots(self) -> dict[int, int]:
        """Which slot each team drafted from, read back from round one."""
        slots: dict[int, int] = {}
        for pick in self.draft_results():
            if pick.get("round_num") != 1:
                continue
            team_id = pick.get("espn_team_id")
            if team_id is not None and team_id not in slots:
                slots[team_id] = pick.get("round_pick") or len(slots) + 1
        return slots

    # -- draft -------------------------------------------------------------

    def draft_results(self, league_key: str | None = None) -> list[dict]:
        """Completed picks. Empty before the draft, which is not an error."""
        if league_key is None and self._draft is not None:
            return self._draft

        key = league_key or self.league_key
        try:
            content = self._get(f"league/{key}/draftresults")
        except YahooConnectionError as exc:
            log.info("Yahoo draft results unavailable for %s: %s", key, exc)
            if league_key is None:
                self._draft = []
            return []

        league = merge(content.get("league"))
        team_count = integer(league.get("num_teams"), 0) or 0

        raw: list[dict] = []
        for fragment in collection(league, "draft_results", "draft_result"):
            result = merge(fragment)
            overall = integer(result.get("pick"), 0) or 0
            round_num = integer(result.get("round"), 0) or 0
            player_key = text(result.get("player_key"))
            if not overall or not player_key:
                continue
            raw.append(
                {
                    "overall_pick": overall,
                    "round_num": round_num,
                    "round_pick": (overall - (round_num - 1) * team_count)
                    if (round_num and team_count)
                    else overall,
                    "espn_team_id": _key_suffix(text(result.get("team_key"))),
                    "espn_player_id": _key_suffix(player_key),
                    "player_key": player_key,
                    "bid_amount": integer(result.get("cost"), 0) or 0,
                }
            )

        names = self._player_names([entry["player_key"] for entry in raw])
        team_names = self._team_names(key)
        picks = [
            {
                "season": self.season,
                "overall_pick": entry["overall_pick"],
                "round_num": entry["round_num"],
                "round_pick": entry["round_pick"],
                "espn_team_id": entry["espn_team_id"],
                "team_name": team_names.get(entry["espn_team_id"], ""),
                "espn_player_id": entry["espn_player_id"],
                "player_name": names.get(entry["player_key"], ""),
                "bid_amount": entry["bid_amount"],
                # Yahoo reports keepers as ordinary picks; there is no flag.
                "keeper": False,
            }
            for entry in raw
        ]
        picks.sort(key=lambda p: p["overall_pick"])
        if league_key is None:
            self._draft = picks
        return picks

    def _team_names(self, league_key: str) -> dict[int, str]:
        try:
            content = self._get(f"league/{league_key}/teams")
        except YahooConnectionError:  # pragma: no cover - network dependent
            return {}
        league = merge(content.get("league"))
        out: dict[int, str] = {}
        for fragment in collection(league, "teams", "team"):
            team = merge(fragment)
            team_id = integer(team.get("team_id"))
            if team_id is not None:
                out[team_id] = text(team.get("name"))
        return out

    def _player_names(self, player_keys: list[str]) -> dict[str, str]:
        """Names for a list of player keys. Draft results carry only the keys."""
        names: dict[str, str] = {}
        unique = [key for key in dict.fromkeys(player_keys) if key]
        for start in range(0, len(unique), PLAYER_PAGE_SIZE):
            batch = unique[start : start + PLAYER_PAGE_SIZE]
            try:
                content = self._get(f"players;player_keys={','.join(batch)}")
            except YahooConnectionError as exc:  # pragma: no cover - network dependent
                log.info("Could not resolve Yahoo player names: %s", exc)
                break
            for fragment in collection(content, "players", "player"):
                player = merge(fragment)
                key = text(player.get("player_key"))
                if key:
                    names[key] = text(merge(player.get("name")).get("full"))
        return names

    def previous_draft_results(self) -> dict[int, list[dict]]:
        """Prior seasons' drafts, following Yahoo's `renew` chain.

        Yahoo links a league to the one it was renewed from as
        ``"{game_key}_{league_id}"``. Best-effort: a league that was not
        renewed, or whose history the account cannot see, simply has none.
        """
        results: dict[int, list[dict]] = {}
        meta, _ = self._load_settings()
        renew = text(meta.get("renew"))
        season = self.season

        for _ in range(2):
            if "_" not in renew:
                break
            game_key, _, league_id = renew.partition("_")
            season -= 1
            picks = self.draft_results(league_key=f"{game_key}.l.{league_id}")
            if picks:
                results[season] = [{**pick, "season": season} for pick in picks]
            try:
                content = self._get(f"league/{game_key}.l.{league_id}/settings")
            except YahooConnectionError:
                break
            renew = text(merge(content.get("league")).get("renew"))
        return results

    # -- player pool -------------------------------------------------------

    def player_pool(
        self,
        limit: int = 600,
        ppr: bool = False,
        week: int | None = None,
        free_agents_only: bool = False,
    ) -> list[PlayerRecord]:
        """The player pool, paged out of Yahoo 25 at a time.

        `ppr` is accepted for interface compatibility and ignored: Yahoo's
        ordering is its own overall rank, which is not scoring-format aware.

        The records carry no `raw_stats`. Yahoo publishes no projections, so
        filling that in would mean inventing them; projections come from a
        projection source instead, matched by name.
        """
        # `out` is how Yahoo asks for several sub-resources at once, which is
        # what keeps ownership, ADP and roster status to the same request as
        # the player rather than three per page.
        modifiers = ["sort=AR", "out=draft_analysis,ownership,percent_owned"]
        if free_agents_only:
            modifiers.append("status=A")
        if week:
            modifiers.append(f"week={int(week)}")

        records: list[PlayerRecord] = []
        seen: set[int] = set()
        start = 0
        while len(records) < limit:
            page = f"{';'.join(modifiers)};start={start};count={PLAYER_PAGE_SIZE}"
            content = self._get(f"league/{self.league_key}/players;{page}")
            league = merge(content.get("league"))
            fragments = collection(league, "players", "player")
            if not fragments:
                break
            for fragment in fragments:
                record = self._parse_player(fragment, rank=len(records) + 1)
                if record is None or record.espn_player_id in seen:
                    continue
                seen.add(record.espn_player_id)
                records.append(record)
                if len(records) >= limit:
                    break
            if len(fragments) < PLAYER_PAGE_SIZE:
                break
            start += PLAYER_PAGE_SIZE

        if not records and not free_agents_only:
            raise YahooConnectionError(
                "Yahoo returned an empty player pool. Check that the league id and season "
                "are right and that your Yahoo account is in this league."
            )
        return records

    # -- live draft --------------------------------------------------------

    def live_draft_picks(self) -> list[dict]:
        """Re-poll Yahoo's draft results. Safe to call repeatedly.

        Yahoo fills its draft-results resource in as picks are made, so this is
        the same call as `draft_results` with the cache dropped. Before the
        draft opens it returns nothing, which is not an error.
        """
        self._draft = None
        return self.draft_results()

    def _parse_player(self, fragment, rank: int) -> PlayerRecord | None:
        player = merge(fragment)
        player_id = integer(player.get("player_id"))
        name = text(merge(player.get("name")).get("full"))
        if player_id is None or not name:
            return None

        position = normalise_yahoo_position(
            text(player.get("display_position")) or text(player.get("primary_position"))
        )
        eligible = [
            normalise_yahoo_slot(text(merge(entry).get("position")))
            for entry in items(player.get("eligible_positions"))
        ]
        eligible = [slot for slot in eligible if slot and slot not in NON_STARTER_SLOTS]

        ownership = sub_resource(fragment, "ownership")
        draft_analysis = sub_resource(fragment, "draft_analysis")
        percent_owned = sub_resource(fragment, "percent_owned")

        availability = OWNERSHIP_TO_AVAILABILITY.get(
            text(ownership.get("ownership_type")).lower(), ""
        )
        bye = integer(merge(player.get("bye_weeks")).get("week"))
        adp = num(draft_analysis.get("average_pick"), 0.0) or None

        return PlayerRecord(
            espn_player_id=player_id,
            name=name,
            position=position,
            pro_team=text(player.get("editorial_team_abbr")).upper() or "FA",
            eligible_slots=eligible,
            bye_week=bye,
            projected_games=17.0,
            injury_status=_injury_status(player),
            injured=_injury_status(player) not in {"ACTIVE", "QUESTIONABLE"},
            percent_owned=round(num(percent_owned.get("value"), 0.0), 2),
            # Yahoo has no start percentage; leaving it at zero is honest, and
            # nothing in the app treats zero as a signal.
            percent_started=0.0,
            adp=adp,
            # Yahoo's collection is returned in its own rank order, so the
            # position in that order *is* the rank.
            espn_rank=rank,
            espn_position_rank=None,
            espn_projected_points=0.0,
            rookie=False,
            raw_stats={},
            weekly_stats={},
            weekly_points={},
            availability=availability,
            on_team_id=_key_suffix(text(ownership.get("owner_team_key"))),
        )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def fetch_user_leagues(oauth: YahooOAuth, season: int | None = None) -> list[dict]:
    """Every NFL league the connected account is in.

    Not tied to a league, so it does not go through `YahooClient` -- it is what
    the League screen calls to let someone pick their league instead of hunting
    for the id in a URL.
    """
    client = YahooClient(league_id=0, season=season or 0, oauth=oauth)
    content = client._get("users;use_login=1/games;game_codes=nfl/leagues")  # noqa: SLF001

    leagues: list[dict] = []
    for user_fragment in collection(content, "users", "user"):
        user = merge(user_fragment)
        for game_fragment in collection(user, "games", "game"):
            game = merge(game_fragment)
            for league_fragment in collection(game, "leagues", "league"):
                league = merge(league_fragment)
                league_id = integer(league.get("league_id"))
                if league_id is None:
                    continue
                leagues.append(
                    {
                        "league_id": league_id,
                        "league_key": text(league.get("league_key")),
                        "name": text(league.get("name")),
                        "season": integer(league.get("season"), 0) or 0,
                        "team_count": integer(league.get("num_teams"), 0) or 0,
                        "scoring_type": _scoring_type(text(league.get("scoring_type"))),
                        "url": text(league.get("url")),
                        "game_key": text(game.get("game_key")),
                    }
                )
    leagues.sort(key=lambda entry: (-entry["season"], entry["name"]))
    return leagues


def _injury_status(player: dict) -> str:
    code = text(player.get("status")).upper()
    return INJURY_CODE_TO_STATUS.get(code, code or "ACTIVE")


def _key_suffix(key: str) -> int | None:
    """The trailing id of a Yahoo key: "461.l.123.t.7" -> 7, "nfl.p.31056" -> 31056."""
    if not key:
        return None
    return integer(key.rsplit(".", 1)[-1])


def _points_for(rules: list[dict], stat_id: int) -> float:
    for rule in rules:
        if rule["stat_id"] == stat_id:
            return float(rule["points"])
    return 0.0


def _scoring_type(raw: str) -> str:
    return {
        "head": "H2H_POINTS",
        "headpoint": "H2H_POINTS",
        "point": "TOTAL_POINTS",
        "roto": "ROTO",
    }.get(raw.lower(), raw.upper())


def _waiver_type(settings: dict) -> str:
    if truthy(settings.get("uses_faab")):
        return "FAAB"
    if text(settings.get("waiver_type")).upper() == "FR":
        return "REVERSE_STANDINGS"
    return "ROLLING_LIST"


def _faab_budget(settings: dict) -> int:
    if not truthy(settings.get("uses_faab")):
        return 0
    # Yahoo only reports the budget on some leagues; 100 is its default, and a
    # budget of zero would make every waiver bid look unaffordable.
    return integer(settings.get("faab_budget"), 100) or 100


def _draft_order(picks: list[dict]) -> list[int]:
    order: list[int] = []
    for pick in sorted(picks, key=lambda p: p.get("overall_pick") or 0):
        if pick.get("round_num") != 1:
            continue
        team_id = pick.get("espn_team_id")
        if team_id is not None and team_id not in order:
            order.append(team_id)
    return order


def _team_logo(team: dict) -> str:
    for fragment in collection(team, "team_logos", "team_logo"):
        url = text(merge(fragment).get("url"))
        if url:
            return url
    return ""


def _abbrev(name: str) -> str:
    """Yahoo has no team abbreviation, so make a stable one from the name."""
    words = [word for word in name.split() if word]
    if not words:
        return ""
    if len(words) == 1:
        return words[0][:4].upper()
    return "".join(word[0] for word in words[:4]).upper()


def _epoch_millis(date_text: str) -> int:
    """Yahoo's dates are "YYYY-MM-DD"; the rest of the app stores epoch millis."""
    if not date_text:
        return 0
    try:
        parsed = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return 0
    return int(parsed.timestamp() * 1000)
