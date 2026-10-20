"""Waiver wire recommendations.

The question a waiver claim answers is not "is this guy good?" but "does adding
him, and dropping somebody to make room, score me more points?". So every
candidate is valued the same way: rebuild the optimal lineup with him on the
roster and the worst droppable player gone, and measure the difference.

Two horizons are reported, because they disagree often and for good reasons:

* **This week**   -- a streaming DST against a bad offense can win you Sunday.
* **Rest of season** -- a backup RB who just inherited a starting job is worth
  far more over fourteen weeks than he is this Sunday.

FAAB guidance is derived from your league's actual budget and the size of the
upgrade, not from a table someone published for a different league.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .league_shape import LeagueShape
from .roster import build_optimal_lineup
from .weekly import WeeklyPlayer

#: Never suggest dropping someone this far above replacement for a streamer.
PROTECTED_VOR = 25.0

#: What insurance is worth next to a starting upgrade.
#:
#: Asked for directly: "sometimes I am not looking to replace a starter, it is to
#: improve my bench in case of a bye or if someone gets hurt." A backup only pays
#: out when something goes wrong, so his value is discounted -- but not to zero,
#: which is what it was. Byes are certain and injuries are near-certain across a
#: season, so a bench that cannot cover either is a real weakness the wire can fix.
INSURANCE_DISCOUNT = 0.35

#: A bye you can actually cover is worth more than vague depth: it is a known
#: hole in a known week, so it is not discounted as hard.
BYE_COVER_DISCOUNT = 0.6

#: What a 100 means.
#:
#: Priority used to be normalised against the best candidate on the list, so the
#: top row scored 100 whatever it was worth -- the least-bad option on a quiet
#: wire looked exactly like a league-winner. A user read "100 PRIORITY" on a
#: third quarterback and reasonably took it as an instruction.
#:
#: It is measured against a fixed yardstick now: the rest-of-season gain that
#: makes something a must-add. A marginal pickup reads as marginal, the number
#: means the same thing from one week to the next, and a wire with nothing on it
#: says so instead of promoting its best scrap.
PRIORITY_REFERENCE_POINTS = 25.0

#: How much of the starter a backup has to be worth before he counts as cover.
#:
#: Without a floor, "insurance" means "better than an empty slot", and every
#: warm body on the wire qualifies -- the list becomes the wire itself and stops
#: being a recommendation. If a man cannot give you a fifth of what the starter
#: gives, he is not insurance: when that starter goes down you would stream
#: somebody off the wire that week instead of having carried him all season.
INSURANCE_FLOOR_SHARE = 0.2

#: FAAB: the share of your remaining budget a full-point upgrade is worth.
#: Tuned so a genuine league-winner lands near a third of the budget and a
#: streamer lands in low single digits.
FAAB_POINTS_PER_PERCENT = 1.6

#: What ESPN's proTeamId 0 looks like by the time it reaches us: a positive
#: statement that the player is on no NFL roster. An EMPTY value is deliberately
#: not in here -- see is_signed.
NO_PRO_TEAM = {"FA", "NONE", "NULL", "-"}


def is_signed(player: WeeklyPlayer) -> bool:
    """Whether this player is actually on an NFL roster.

    Reported from real use: an unsigned kicker was the number one waiver target,
    with a suggested bid and a real running back named as the drop. He still
    carried a projection -- ESPN publishes one from before he was released -- and
    nothing checked whether anybody employs him. A player with no NFL team takes
    no snaps and scores nothing, so he cannot be an upgrade at any price.

    Unknown is not the same as unemployed. A blank pro team means we were never
    told, which is true of demo data and of any import path that does not fill
    the field; hiding those players would quietly empty the wire. Only ESPN
    actually saying "no team" (proTeamId 0, which reaches us as FA) excludes
    anybody.
    """
    team = (getattr(player, "pro_team", "") or "").strip().upper()
    return team not in NO_PRO_TEAM


@dataclass
class DropCandidate:
    player: WeeklyPlayer
    reason: str


@dataclass
class WaiverTarget:
    player: WeeklyPlayer
    #: Points added to this week's optimal lineup.
    week_gain: float
    #: Points added to the season-long optimal lineup.
    season_gain: float
    drop: WeeklyPlayer | None
    #: 0-100, comparable across candidates.
    priority: float
    faab_bid: int
    faab_pct: float
    reasons: list[str] = field(default_factory=list)
    verdict: str = "depth"      # must-add | starter | streamer | depth | stash | insurance
    #: What he adds when the starter in front of him is out -- the bench case.
    insurance_gain: float = 0.0
    #: The starter he would be covering for.
    insures: str = ""
    #: Upcoming weeks where a starter at his position is on bye and he is not.
    covers_byes: list[int] = field(default_factory=list)
    #: Rest-of-season value he has over the player he would replace. The
    #: straight question a bench add actually poses -- "is he better than the guy
    #: I would cut?" -- which the screen never answered out loud.
    depth_gain: float = 0.0


def _lineup_points(
    roster: list[WeeklyPlayer], shape: LeagueShape, use_week: bool
) -> float:
    return build_optimal_lineup(
        [p.as_roster_player(use_week=use_week) for p in roster], shape
    ).total_points


def upgrade_ids(targets: list["WaiverTarget"]) -> set[int]:
    """Just the targets who beat what you already start.

    The per-position summary answers "is anybody out there better than my guy",
    so it has to be told about upgrades only. Insurance belongs on the list -- a
    bench that cannot cover a bye is a real weakness -- but it is not an answer
    to that question, and letting it count would have the summary say a position
    is covered when nothing on the wire beats the incumbent.
    """
    return {
        t.player.espn_player_id for t in targets
        if t.season_gain > 0 or t.week_gain > 0
    }


def _starters(roster: list[WeeklyPlayer], shape: LeagueShape) -> list[WeeklyPlayer]:
    """Who is currently holding a starting slot, on rest-of-season value."""
    lineup = build_optimal_lineup(
        [p.as_roster_player(use_week=False) for p in roster], shape
    )
    by_id = {p.espn_player_id: p for p in roster}
    return [
        by_id[a.player.espn_player_id]
        for a in lineup.starters
        if a.player and a.player.espn_player_id in by_id
    ]


def best_streamer(
    free_agents: list[WeeklyPlayer],
    position: str,
    exclude: int,
    unavailable_week: int | None = None,
) -> WeeklyPlayer | None:
    """Who you would pick up at this position if you needed one TODAY.

    The alternative to carrying a backup all season is not fielding nobody -- it
    is spending a waiver claim the week it happens. This is that player.

    `unavailable_week` is for a bye you are covering: a man who is off that same
    week is no fallback for it, however good he is the rest of the time.
    """
    options = [
        p for p in free_agents
        if p.position == position and p.espn_player_id != exclude
        and not (unavailable_week and p.bye_week == unavailable_week)
    ]
    return max(options, key=lambda p: p.season_points, default=None)


def insurance_value(
    roster: list[WeeklyPlayer],
    shape: LeagueShape,
    candidate: WeeklyPlayer,
    drop: WeeklyPlayer | None,
    streamer: WeeklyPlayer | None = None,
) -> tuple[float, str]:
    """What he is worth if the man in front of him goes down.

    The lineup gain alone says nothing about a backup: a receiver who does not
    crack your starting three scores a flat zero and is dropped from the list,
    however good he is. But a bench exists precisely for the week a starter is
    out, so the question worth asking is what your lineup looks like THEN -- with
    him on the roster versus without.

    The baseline is the part that has to be right, and was not. It assumed that
    with your starter gone you would field NOBODY, so a backup quarterback in a
    one-quarterback league was credited with his entire season -- 148 points --
    and the wire filled up with backup quarterbacks. You would not sit a slot
    empty; you would claim the best man available that week. So the comparison
    is against him: what this candidate is worth OVER the player you could get
    anyway. Where the wire has nobody at the position, the old reading is the
    right one -- then he really is worth his whole value.

    Returns the best case across your starters at his position, and who it covers.
    """
    kept = [
        p for p in roster
        if drop is None or p.espn_player_id != drop.espn_player_id
    ]
    fallback = [streamer] if streamer is not None else []
    best_gain, covering = 0.0, ""
    for starter in _starters(kept, shape):
        if starter.position != candidate.position:
            continue
        without = [p for p in kept if p.espn_player_id != starter.espn_player_id]
        base = _lineup_points(without + fallback, shape, use_week=False)
        with_him = _lineup_points(without + [candidate], shape, use_week=False)
        gain = with_him - base
        if gain <= 0:
            continue
        if candidate.vor <= 0 and gain < starter.season_points * INSURANCE_FLOOR_SHARE:
            # Two tiers, because the floor exists to keep warm bodies out, not
            # to reject a useful backup. A player above replacement level is by
            # definition worth a roster spot if he has to play, so any gain
            # counts. One below it still has to clear the old bar -- otherwise
            # "better than an empty slot" makes every free agent insurance.
            continue
        if gain > best_gain:
            best_gain, covering = gain, starter.name
    return (round(best_gain, 2), covering)


def bye_weeks_covered(
    roster: list[WeeklyPlayer],
    shape: LeagueShape,
    candidate: WeeklyPlayer,
    week: int,
) -> list[int]:
    """Upcoming byes at his position that he would be available for.

    A bye is a hole you can see coming, which makes it the easiest thing on the
    wire to plan for -- and the thing a bench is most obviously for.
    """
    out: list[int] = []
    for starter in _starters(roster, shape):
        if starter.position != candidate.position:
            continue
        bye = starter.bye_week
        if not bye or bye <= week:
            continue
        if candidate.bye_week == bye:
            continue      # he is off that week too; no help at all
        if bye not in out:
            out.append(bye)
    return sorted(out)


def _droppable(
    roster: list[WeeklyPlayer], shape: LeagueShape
) -> list[DropCandidate]:
    """Who can go. Never the players holding your lineup together."""
    lineup = build_optimal_lineup([p.as_roster_player(use_week=False) for p in roster], shape)
    starters = {a.player.espn_player_id for a in lineup.starters if a.player}

    out: list[DropCandidate] = []
    for player in roster:
        if player.espn_player_id in starters:
            continue
        if player.vor >= PROTECTED_VOR:
            continue
        reason = "bench depth"
        if (player.injury_status or "").upper() in {"OUT", "INJURY_RESERVE", "SUSPENSION"}:
            reason = f"listed {player.injury_status.title()}"
        elif player.vor <= 0:
            reason = "below replacement level"
        out.append(DropCandidate(player=player, reason=reason))

    out.sort(key=lambda c: c.player.season_points)
    return out


@dataclass
class PositionVerdict:
    """Why a position produced no recommendation -- or produced one."""

    position: str
    considered: int
    #: Best free agent at this position, by rest-of-season points.
    best_name: str = ""
    best_points: float = 0.0
    #: What you already start there, which is the bar he had to clear.
    incumbent_name: str = ""
    incumbent_points: float = 0.0
    helps: bool = False
    note: str = ""


def explain_by_position(
    roster: list[WeeklyPlayer],
    free_agents: list[WeeklyPlayer],
    shape: LeagueShape,
    recommended: set[int],
) -> list[PositionVerdict]:
    """Per position: the best man available, and why he did or didn't make it.

    A wire that returns eight defences and nothing else looks broken. Usually
    it isn't -- it means every other position on the roster already beats what
    is out there. That is a useful answer, and it was being thrown away: a
    candidate who does not improve the lineup is dropped silently, so the
    screen could only ever show the one position where the wire wins.
    """
    # "The best out there" has to mean someone who can actually play.
    free_agents = [p for p in free_agents if is_signed(p)]
    lineup = build_optimal_lineup(
        [p.as_roster_player(use_week=False) for p in roster], shape
    )
    weakest_starter: dict[str, WeeklyPlayer] = {}
    by_id = {p.espn_player_id: p for p in roster}
    for assignment in lineup.starters:
        if assignment.player is None:
            continue
        player = by_id.get(assignment.player.espn_player_id)
        if player is None:
            continue
        current = weakest_starter.get(player.position)
        if current is None or player.season_points < current.season_points:
            weakest_starter[player.position] = player

    out: list[PositionVerdict] = []
    for position in sorted({p.position for p in free_agents}):
        group = sorted(
            [p for p in free_agents if p.position == position],
            key=lambda p: p.season_points,
            reverse=True,
        )
        if not group:
            continue
        best = group[0]
        incumbent = weakest_starter.get(position)
        helps = any(p.espn_player_id in recommended for p in group)

        if helps:
            note = "on the list below"
        elif incumbent is None:
            note = f"you start nobody at {position}"
        else:
            margin = incumbent.season_points - best.season_points
            note = (
                f"{best.name} is the best out there and projects "
                f"{margin:.0f} pts less than {incumbent.name}, "
                f"who you already start"
            )

        out.append(
            PositionVerdict(
                position=position,
                considered=len(group),
                best_name=best.name,
                best_points=best.season_points,
                incumbent_name=incumbent.name if incumbent else "",
                incumbent_points=incumbent.season_points if incumbent else 0.0,
                helps=helps,
                note=note,
            )
        )
    return out


def recommend_waivers(
    roster: list[WeeklyPlayer],
    free_agents: list[WeeklyPlayer],
    shape: LeagueShape,
    week: int,
    faab_budget: int = 0,
    faab_remaining: int | None = None,
    roster_is_full: bool = True,
    limit: int = 15,
) -> list[WaiverTarget]:
    """Rank the wire by how much each addition actually improves your lineup."""
    # Enforced here rather than at the caller so every path -- the Waivers screen,
    # Auto Mode's claims -- is covered by one rule.
    free_agents = [p for p in free_agents if is_signed(p)]
    if not free_agents:
        return []

    base_week = _lineup_points(roster, shape, use_week=True)
    base_season = _lineup_points(roster, shape, use_week=False)
    drops = _droppable(roster, shape)
    budget = faab_remaining if faab_remaining is not None else faab_budget

    targets: list[WaiverTarget] = []
    for candidate in free_agents:
        # Adding someone means dropping someone, unless there's an open spot.
        drop = drops[0].player if (roster_is_full and drops) else None
        trial = [p for p in roster if drop is None or p.espn_player_id != drop.espn_player_id]
        trial = trial + [candidate]

        week_gain = round(_lineup_points(trial, shape, use_week=True) - base_week, 2)
        season_gain = round(_lineup_points(trial, shape, use_week=False) - base_season, 2)
        # Not every useful addition changes today's lineup. A backup is bought
        # for the week a starter is out, so ask what the lineup looks like then.
        byes = bye_weeks_covered(roster, shape, candidate, week)
        # Two different fallbacks. For an injury, whoever is best on the wire.
        # For a bye, whoever is best on the wire AND playing that week -- a
        # replacement who is off the same Sunday replaces nothing.
        insurance, insures = insurance_value(
            roster, shape, candidate, drop,
            streamer=best_streamer(
                free_agents, candidate.position, candidate.espn_player_id,
                unavailable_week=byes[0] if byes else None,
            ),
        )
        depth = round(
            candidate.season_points - (drop.season_points if drop else 0.0), 2
        )

        if week_gain <= 0 and season_gain <= 0 and insurance <= 0:
            continue     # helps neither the lineup nor the bench behind it

        targets.append(
            WaiverTarget(
                player=candidate,
                week_gain=week_gain,
                season_gain=season_gain,
                drop=drop,
                priority=0.0,
                faab_bid=0,
                faab_pct=0.0,
                insurance_gain=insurance,
                insures=insures,
                covers_byes=byes,
                depth_gain=depth,
            )
        )

    if not targets:
        return []

    # Priority blends the immediate and the durable; season value dominates
    # because a wrong week costs you once and a wrong season costs you all year.

    for target in targets:
        # Insurance counts, at a discount: it only pays out when something goes
        # wrong. A bye is a certainty rather than a risk, so it counts for more.
        cover = INSURANCE_DISCOUNT
        if target.covers_byes:
            cover = BYE_COVER_DISCOUNT
        blended = (
            target.season_gain * 0.7
            + target.week_gain * 0.3
            + target.insurance_gain * cover
        )
        target.priority = round(
            max(0.0, min(100.0, 100.0 * blended / PRIORITY_REFERENCE_POINTS)), 1
        )

        if budget > 0:
            # Bid on what he adds to the lineup you are actually fielding.
            # Insurance deliberately does NOT count here: a backup is worth
            # rostering and worth about a dollar, and deriving the bid from the
            # blended figure had the app asking for 37 of 100 FAAB for a third
            # quarterback. What you would pay to cover a maybe is not what you
            # would pay to win a week.
            upgrade = target.season_gain * 0.7 + target.week_gain * 0.3
            pct = min(max(upgrade, 0.0) / FAAB_POINTS_PER_PERCENT, 60.0)
            target.faab_pct = round(max(pct, 0.0), 1)
            target.faab_bid = max(1, int(round(budget * target.faab_pct / 100)))

        target.verdict = _verdict(target, shape)
        target.reasons = _reasons(target, week, budget)

    targets.sort(key=lambda t: t.priority, reverse=True)
    return targets[:limit]


def _verdict(target: WaiverTarget, shape: LeagueShape) -> str:
    # A player who changes nothing today but covers a hole behind your starters
    # is a different kind of add, and saying so is the whole point of listing him.
    if target.season_gain <= 0 and target.week_gain <= 0 and target.insurance_gain > 0:
        return "bye cover" if target.covers_byes else "insurance"
    if target.season_gain >= 25:
        return "must-add"
    if target.season_gain >= 10:
        return "starter"
    if target.week_gain >= 3 and target.season_gain < 5:
        return "streamer"
    if target.season_gain > 0 and target.week_gain <= 0:
        return "stash"
    return "depth"


def _form_reason(player: WeeklyPlayer) -> str | None:
    """What he has actually been doing, when that is what moved his rating.

    Said out loud because it is the answer to the question the screen kept
    provoking: a receiver scoring 17.9, 17.5 and 14.8 looked no different from
    his August projection, and there was no way to tell why he was or was not
    recommended.
    """
    form = getattr(player, "form", None)
    if form is None or not form.has_evidence:
        return None
    text = (
        f"Averaging {form.points_per_game:.1f} pts a game over "
        f"{form.games_played:.0f} game{'' if form.games_played == 1 else 's'}"
    )
    if form.recent_games:
        text += f", {form.recent_points_per_game:.1f} over his last {form.recent_games:.0f}"
    return text


def _reasons(target: WaiverTarget, week: int, budget: int) -> list[str]:
    player = target.player
    out: list[str] = []

    if target.week_gain > 0:
        out.append(
            f"Adds {target.week_gain:.1f} pts to your week {week} lineup"
        )
    else:
        out.append("Doesn't crack this week's lineup, but helps later")

    if target.drop is not None and target.depth_gain > 0 and target.season_gain <= 0:
        out.append(
            f"Straight bench upgrade on {target.drop.name}: "
            f"{target.depth_gain:.0f} more points the rest of the way"
        )

    if target.insurance_gain > 0:
        if target.covers_byes:
            weeks = ", ".join(str(w) for w in target.covers_byes)
            out.append(
                f"Covers your week {weeks} bye at {player.position}"
                + (f" ({target.insures})" if target.insures else "")
            )
        if target.insures:
            out.append(
                f"Worth {target.insurance_gain:.1f} pts to your lineup if "
                f"{target.insures} misses time"
            )

    form_line = _form_reason(player)
    if form_line:
        out.append(form_line)

    if target.season_gain > 0:
        out.append(f"Worth {target.season_gain:.1f} pts to your lineup rest-of-season")

    if target.drop is not None:
        out.append(
            f"Drop {target.drop.name} ({target.drop.position}) to make room"
        )
    else:
        out.append("You have an open roster spot")

    if player.percent_owned:
        if player.percent_owned < 15:
            out.append(f"Only {player.percent_owned:.0f}% rostered -- likely unclaimed")
        elif player.percent_owned > 55:
            out.append(f"{player.percent_owned:.0f}% rostered -- expect competition")

    if target.verdict == "streamer":
        out.append("Short-term play; drop him next week without regret")
    elif target.verdict == "stash":
        out.append("Stash: the value is in the weeks ahead, not this Sunday")

    if budget > 0 and target.faab_bid:
        out.append(f"Suggested bid {target.faab_bid} of your {budget} FAAB")

    return out
