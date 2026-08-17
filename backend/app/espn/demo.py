"""Demo league + player pool, so the app runs with no ESPN credentials.

Everything here is **synthetic**.  The player names are generated, not real NFL
players, and the projections are drawn from position-shaped curves -- they are
there to exercise the valuation engine, the UI and the tests, not to advise a
real draft.  The UI shows a DEMO badge whenever this data is in use.

Output shapes match `EspnClient` exactly, so `services.importer` treats demo and
live ESPN data through the same code path.
"""

from __future__ import annotations

import random

from .client import PlayerRecord

DEMO_LEAGUE_ID = 999999
DEMO_TEAM_COUNT = 12
#: The demo league is mid-season so the weekly screens have something to show.
DEMO_CURRENT_WEEK = 5
#: Weeks of per-game projections the demo generates.
DEMO_WEEKS = range(1, 18)

_FIRST_NAMES = [
    "Marcus", "Jalen", "Trevon", "Deshaun", "Cameron", "Elijah", "Damari", "Isaiah",
    "Rashad", "Kenyon", "Brayden", "Tyrell", "Amari", "Donovan", "Malik", "Xavier",
    "Corbin", "Jaxon", "Rowan", "Silas", "Terrence", "Landon", "Dante", "Miles",
    "Beau", "Kason", "Zion", "Emmett", "Devonte", "Quinton", "Asher", "Kai",
    "Bryce", "Nolan", "Camden", "Rhett", "Jermaine", "Tobias", "Colby", "Dax",
]
_LAST_NAMES = [
    "Whitfield", "Carrington", "Boone", "Vasquez", "Okonkwo", "Delgado", "Ramsey",
    "Fontaine", "Alvarado", "Kimball", "Sandoval", "Prescott", "Ashby", "Mensah",
    "Calloway", "Draper", "Ellison", "Fairchild", "Guthrie", "Halloran", "Ibarra",
    "Jennings", "Kowalski", "Larkin", "Mercado", "Novak", "Ortega", "Pemberton",
    "Quintero", "Rutherford", "Sinclair", "Thorne", "Underwood", "Valentine",
    "Wexler", "Yearwood", "Zamora", "Barnett", "Castellano", "Devereaux",
]

_PRO_TEAMS = [
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET",
    "GB", "HOU", "IND", "JAX", "KC", "LAC", "LAR", "LV", "MIA", "MIN", "NE", "NO",
    "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WSH",
]

#: position -> (count of players to generate, top-of-position raw stat line)
_POSITION_COUNTS = {
    "QB": 36,
    "RB": 80,
    "WR": 110,
    "TE": 40,
    "K": 32,
    "DST": 32,
}


def _name_for(index: int, rng: random.Random, used: set[str]) -> str:
    """A distinct synthetic name. Duplicates would be confusing on the board."""
    for attempt in range(len(_FIRST_NAMES) * len(_LAST_NAMES)):
        first = _FIRST_NAMES[(index + attempt) % len(_FIRST_NAMES)]
        last = _LAST_NAMES[(index * 7 + attempt * 3 + rng.randint(0, 2)) % len(_LAST_NAMES)]
        name = f"{first} {last}"
        if name not in used:
            used.add(name)
            return name
    name = f"{_FIRST_NAMES[index % len(_FIRST_NAMES)]} Player{index}"
    used.add(name)
    return name


#: Stat line for the best player at each position, keyed by ESPN stat id.
_TOP_STAT_LINE: dict[str, dict[str, float]] = {
    "QB": {"3": 4900, "4": 38, "23": 105, "24": 480, "25": 5, "20": 11, "72": 3},
    "RB": {"23": 300, "24": 1420, "25": 13, "53": 60, "42": 470, "43": 3, "72": 3},
    "WR": {"53": 115, "42": 1620, "43": 13, "23": 6, "24": 45, "72": 2},
    "TE": {"53": 96, "42": 1180, "43": 9, "72": 2},
    "K": {"80": 20, "77": 10, "74": 5, "86": 46},
    "DST": {"99": 52, "95": 19, "96": 12, "98": 3, "94": 4, "89": 1, "90": 4, "91": 6},
}

#: Stats that don't scale with a player being good -- turnovers stay roughly
#: flat, so scaling the whole line down doesn't accidentally reward bad players.
_FLAT_STATS = {"20", "72"}

#: Realistic half-PPR season totals: (position rank, fantasy points).  Everything
#: between control points is linearly interpolated.  These shapes are what make
#: the demo board behave like a real one -- elite RB/WR at the top, QB value
#: compressed by a deep replacement level, one or two special TEs.
_POINTS_CURVE: dict[str, list[tuple[int, float]]] = {
    "QB": [(1, 400), (6, 345), (12, 305), (18, 272), (24, 244), (36, 178)],
    "RB": [(1, 332), (3, 292), (6, 256), (12, 224), (18, 196),
           (24, 174), (36, 140), (48, 110), (64, 78), (80, 52)],
    "WR": [(1, 314), (3, 286), (6, 258), (12, 234), (18, 210), (24, 191),
           (36, 159), (48, 131), (72, 88), (110, 42)],
    "TE": [(1, 246), (2, 206), (4, 170), (8, 141), (12, 121), (18, 103), (24, 89), (40, 57)],
    "K": [(1, 152), (8, 135), (16, 122), (24, 111), (32, 97)],
    "DST": [(1, 148), (6, 128), (12, 113), (20, 99), (32, 79)],
}

#: The demo league's scoring, as {stat_id: points}, so the generator can invert
#: a target point total into a stat line exactly.
_DEMO_POINTS: dict[str, float] = {
    "3": 0.04, "4": 4.0, "20": -2.0, "24": 0.1, "25": 6.0, "42": 0.1, "43": 6.0,
    "53": 0.5, "72": -2.0, "74": 5.0, "77": 4.0, "80": 3.0, "86": 1.0,
    "89": 5.0, "90": 4.0, "91": 3.0, "94": 6.0, "95": 2.0, "96": 2.0,
    "98": 2.0, "99": 1.0,
}


def _score(stats: dict[str, float]) -> float:
    return sum(value * _DEMO_POINTS.get(stat_id, 0.0) for stat_id, value in stats.items())


def _target_points(position: str, rank: int) -> float:
    """Interpolate the position's points curve at a given positional rank."""
    curve = _POINTS_CURVE[position]
    if rank <= curve[0][0]:
        return curve[0][1]
    for (r0, p0), (r1, p1) in zip(curve, curve[1:]):
        if rank <= r1:
            span = r1 - r0
            weight = (rank - r0) / span if span else 0.0
            return p0 + (p1 - p0) * weight
    return curve[-1][1]


def _raw_stats_for(position: str, rank: int, total: int, rng: random.Random) -> dict[str, float]:
    """Raw season stat totals that score to the position's target point curve.

    Scoring is linear in the stats, so we scale the top player's stat line by
    exactly the factor that lands on the target -- holding turnovers flat so a
    worse player doesn't get credit for fewer fumbles.
    """
    top_line = _TOP_STAT_LINE[position]
    flat = {k: v for k, v in top_line.items() if k in _FLAT_STATS}
    scalable = {k: v for k, v in top_line.items() if k not in _FLAT_STATS}

    scalable_points = _score(scalable)
    target = _target_points(position, rank) * rng.uniform(0.965, 1.035)
    factor = max((target - _score(flat)) / scalable_points, 0.05) if scalable_points else 1.0

    stats = {k: float(round(v * factor, 1)) for k, v in scalable.items()}
    stats.update({k: float(v) for k, v in flat.items()})

    if position == "K":
        # Kickers only attempt what their offense gives them; keep PATs sane.
        stats["86"] = float(round(min(stats.get("86", 0.0), 55)))
    return stats


#: Roughly where each position starts coming off the board, and how fast a
#: position's players get spread through the draft. (first_pick, spacing)
_ADP_SHAPE = {
    "RB": (1.5, 2.6),
    "WR": (2.0, 2.3),
    "TE": (14.0, 8.5),
    "QB": (28.0, 6.5),
    "DST": (135.0, 6.0),
    "K": (150.0, 6.0),
}


def demo_player_pool(season: int = 2026, seed: int = 20260101) -> list[PlayerRecord]:
    """A deterministic synthetic player pool."""
    rng = random.Random(seed)
    records: list[PlayerRecord] = []
    used_names: set[str] = set()
    player_id = 100000

    for position, count in _POSITION_COUNTS.items():
        first_pick, spacing = _ADP_SHAPE[position]
        for rank in range(1, count + 1):
            player_id += 1
            raw = _raw_stats_for(position, rank, count, rng)

            adp = first_pick + (rank - 1) * spacing * rng.uniform(0.85, 1.15)
            adp = round(min(adp, 400.0), 1)

            if position == "DST":
                team = _PRO_TEAMS[(rank - 1) % len(_PRO_TEAMS)]
                name = f"{team} D/ST"
            else:
                team = _PRO_TEAMS[(player_id + rank) % len(_PRO_TEAMS)]
                name = _name_for(player_id - 100000, rng, used_names)

            bye = 5 + ((player_id + rank) % 10)

            # Per-week projections: the season line spread over the games he
            # plays, with week-to-week noise and a zero on the bye.
            weekly_stats: dict[int, dict[str, float]] = {}
            weekly_points: dict[int, float] = {}
            games = len([w for w in DEMO_WEEKS if w != bye])
            for week in DEMO_WEEKS:
                if week == bye:
                    weekly_stats[week] = {}
                    weekly_points[week] = 0.0
                    continue
                noise = rng.uniform(0.72, 1.31)
                weekly_stats[week] = {
                    k: round(v / games * noise, 2) for k, v in raw.items()
                }
                weekly_points[week] = round(_score(weekly_stats[week]), 2)

            injury_roll = rng.random()
            if injury_roll > 0.965:
                injury, injured = "OUT", True
            elif injury_roll > 0.91:
                injury, injured = "QUESTIONABLE", False
            else:
                injury, injured = "ACTIVE", False

            projected_games = 17.0 - (1.0 if injury == "ACTIVE" else rng.choice([2.0, 3.0, 5.0]))

            records.append(
                PlayerRecord(
                    espn_player_id=player_id,
                    name=name,
                    position=position,
                    pro_team=team,
                    eligible_slots=(
                        [position, "FLEX"] if position in {"RB", "WR", "TE"} else [position]
                    ),
                    bye_week=bye,
                    projected_games=projected_games,
                    injury_status=injury,
                    injured=injured,
                    percent_owned=round(max(0.2, 100 * (1 - (adp / 260))), 1),
                    percent_started=round(max(0.1, 90 * (1 - (adp / 200))), 1),
                    adp=adp,
                    espn_rank=None,  # filled in below, once the pool is ordered
                    espn_position_rank=rank,
                    espn_projected_points=round(_score(raw), 2),
                    rookie=rng.random() > 0.88,
                    raw_stats={k: float(v) for k, v in raw.items()},
                    weekly_stats=weekly_stats,
                    weekly_points=weekly_points,
                )
            )

    records.sort(key=lambda r: r.adp or 999)
    for idx, record in enumerate(records, start=1):
        record.espn_rank = idx

    # Mark roughly the top N as rostered so the rest form a realistic waiver
    # wire -- otherwise every star would show up as a free agent.
    rostered = DEMO_TEAM_COUNT * 15
    for idx, record in enumerate(records):
        if idx < rostered:
            record.availability = "ONTEAM"
            record.on_team_id = (idx % DEMO_TEAM_COUNT) + 1
        else:
            record.availability = "FREEAGENT"
    return records


def demo_league_settings(season: int = 2026) -> dict:
    """A 12-team half-PPR league with a conventional roster."""
    scoring_rules = [
        {"stat_id": 3, "abbrev": "PY", "label": "Passing Yards", "points": 0.04},
        {"stat_id": 4, "abbrev": "PTD", "label": "TD Pass", "points": 4.0},
        {"stat_id": 20, "abbrev": "INTT", "label": "Interceptions Thrown", "points": -2.0},
        {"stat_id": 24, "abbrev": "RY", "label": "Rushing Yards", "points": 0.1},
        {"stat_id": 25, "abbrev": "RTD", "label": "TD Rush", "points": 6.0},
        {"stat_id": 42, "abbrev": "REY", "label": "Receiving Yards", "points": 0.1},
        {"stat_id": 43, "abbrev": "RETD", "label": "TD Reception", "points": 6.0},
        {"stat_id": 53, "abbrev": "REC", "label": "Each reception", "points": 0.5},
        {"stat_id": 72, "abbrev": "FUML", "label": "Total Fumbles Lost", "points": -2.0},
        {"stat_id": 74, "abbrev": "FG50P", "label": "FG Made (50+ yards)", "points": 5.0},
        {"stat_id": 77, "abbrev": "FG40", "label": "FG Made (40-49 yards)", "points": 4.0},
        {"stat_id": 80, "abbrev": "FG0", "label": "FG Made (0-39 yards)", "points": 3.0},
        {"stat_id": 86, "abbrev": "PAT", "label": "Each PAT Made", "points": 1.0},
        {"stat_id": 89, "abbrev": "PA0", "label": "0 points allowed", "points": 5.0},
        {"stat_id": 90, "abbrev": "PA1", "label": "1-6 points allowed", "points": 4.0},
        {"stat_id": 91, "abbrev": "PA7", "label": "7-13 points allowed", "points": 3.0},
        {"stat_id": 94, "abbrev": "DEFRETTD", "label": "Fumble or INT Return for TD", "points": 6.0},
        {"stat_id": 95, "abbrev": "INT", "label": "Each Interception", "points": 2.0},
        {"stat_id": 96, "abbrev": "FR", "label": "Each Fumble Recovered", "points": 2.0},
        {"stat_id": 98, "abbrev": "SF", "label": "Each Safety", "points": 2.0},
        {"stat_id": 99, "abbrev": "SK", "label": "Each Sack", "points": 1.0},
    ]
    for rule in scoring_rules:
        rule.setdefault("points_overrides", {})

    return {
        "espn_league_id": DEMO_LEAGUE_ID,
        "season": season,
        "name": "War Room Demo League",
        "team_count": DEMO_TEAM_COUNT,
        "scoring_type": "H2H_POINTS",
        "is_ppr": True,
        "ppr_value": 0.5,
        "roster_slots": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "DST": 1, "K": 1},
        "bench_slots": 6,
        "ir_slots": 1,
        "all_slot_counts": {
            "QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "DST": 1, "K": 1,
            "BE": 6, "IR": 1,
        },
        "draft_type": "SNAKE",
        "draft_order": list(range(1, DEMO_TEAM_COUNT + 1)),
        "draft_date": None,
        "draft_time_per_selection": 90,
        "draft_completed": False,
        "keeper_count": 0,
        "waiver_type": "FAAB",
        "uses_faab": True,
        "acquisition_budget": 100,
        "waiver_process_days": ["WED"],
        "waiver_hours": 3,
        "acquisition_limit": -1,
        "regular_season_weeks": 14,
        "playoff_team_count": 6,
        "playoff_matchup_length": 1,
        "playoff_seed_tie_rule": "TOTAL_POINTS_SCORED",
        "trade_deadline": 0,
        "veto_votes_required": 4,
        "scoring_rules": scoring_rules,
        "source": "demo",
        "raw_settings": {"demo": True},
    }


_DEMO_TEAM_NAMES = [
    ("Gridiron Goblins", "Alex Whitfield"),
    ("Fourth and Chaos", "Jordan Reyes"),
    ("Play Action Pirates", "Sam Okafor"),
    ("The Hail Marys", "Casey Lindqvist"),
    ("Zone Blitz Bandits", "Riley Nakamura"),
    ("Red Zone Renegades", "Morgan Ellis"),
    ("Pylon Pushers", "Taylor Brennan"),
    ("Screen Pass Syndicate", "Devon Marsh"),
    ("Two Minute Warning", "Quinn Alvarez"),
    ("Backfield Bullies", "Avery Sandoval"),
    ("Tight End Tyranny", "Reese Coleman"),
    ("Waiver Wire Wolves", "Parker Nguyen"),
]


def demo_teams(my_slot: int = 5) -> list[dict]:
    teams = []
    for idx, (name, owner) in enumerate(_DEMO_TEAM_NAMES[:DEMO_TEAM_COUNT], start=1):
        teams.append(
            {
                "espn_team_id": idx,
                "name": name,
                "abbrev": "".join(w[0] for w in name.split())[:4].upper(),
                "owners": [owner],
                "logo_url": "",
                "division_name": "East" if idx <= DEMO_TEAM_COUNT // 2 else "West",
                "draft_slot": idx,
                "wins": 0,
                "losses": 0,
                "ties": 0,
                "roster": [],
            }
        )
    return teams


def demo_previous_draft(season: int, pool: list[PlayerRecord]) -> list[dict]:
    """A plausible prior-season draft, so the History panel has something real."""
    teams = demo_teams()
    picks: list[dict] = []
    ordered = sorted(pool, key=lambda p: p.adp or 999)[: DEMO_TEAM_COUNT * 15]
    for idx, player in enumerate(ordered):
        round_num = idx // DEMO_TEAM_COUNT + 1
        slot_in_round = idx % DEMO_TEAM_COUNT
        slot = slot_in_round if round_num % 2 == 1 else DEMO_TEAM_COUNT - 1 - slot_in_round
        team = teams[slot]
        picks.append(
            {
                "season": season,
                "overall_pick": idx + 1,
                "round_num": round_num,
                "round_pick": slot_in_round + 1,
                "espn_team_id": team["espn_team_id"],
                "team_name": team["name"],
                "espn_player_id": player.espn_player_id,
                "player_name": player.name,
                "bid_amount": 0,
                "keeper": False,
            }
        )
    return picks
