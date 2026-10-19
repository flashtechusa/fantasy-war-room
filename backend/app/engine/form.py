"""Rating a player on what he has actually done, not only on what August said.

Reported from real use, repeatedly and from three directions: a top-four kicker
looked replaceable, an unsigned kicker looked like an upgrade, and a receiver
scoring 17.9, 17.5 and 14.8 in consecutive weeks was never recommended. One
cause underneath all of it -- every valuation in the app came from the preseason
projection, and the app imported no actual results at all. Six weeks of evidence
could not reach it.

What this module does, in the order the inputs earn their place:

1. **Evidence beats the prior, in proportion to the sample.** One game says very
   little; eight say a lot. The weight on actual production is games/(games+K),
   so it climbs from nothing in week 1 to roughly two thirds by midseason and
   keeps climbing. Before any game is played the answer is exactly the preseason
   projection -- this is a basis change, not a rewrite.

2. **Recent games count for more than September.** A player's role moves during
   a season, so the actual side of the blend leans on his last few games rather
   than treating week 1 and week 8 as equal.

3. **Usage leads scoring.** A receiver whose targets jump is a different player
   before the points show it: touchdowns are lumpy, opportunity is not. So the
   blend is nudged by how his recent opportunity rate compares with his own
   season rate -- damped and clamped, because usage is a hint about direction,
   not a multiplier to be trusted at face value.

Everything here is a pure function of the numbers handed to it, so the reasoning
can be tested directly and shown on screen rather than taken on faith.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Games of evidence needed before actual production carries half the weight.
#: At K=4 the blend is 60% actuals by week 6 and 2/3 by week 8 -- fast enough to
#: catch a breakout in the window it matters, slow enough that two good games in
#: September do not erase a preseason consensus.
EVIDENCE_HALF_LIFE_GAMES = 4.0

#: Some positions tell you much less about themselves than others.
#:
#: Reported from real use: a defence with one 29-point game (two picks, a fumble
#: and six sacks) was recommended as a pickup off the back of it. Defensive
#: scoring is dominated by turnovers and return touchdowns, which barely repeat;
#: kickers depend on how often someone else stalls in field-goal range. Six games
#: of a defence is simply weaker evidence than six games of a running back, so
#: production has to clear a higher bar before it outweighs the projection.
POSITION_HALF_LIFE = {"DST": 10.0, "K": 8.0}

#: How many recent games count as "lately".
RECENT_GAMES = 3

#: Within actual production, how much of the weight the recent games take.
RECENT_SHARE = 0.6

#: Usage is a direction, not a multiplier: half of the observed swing is applied,
#: and never more than this far in either direction.
USAGE_DAMPING = 0.5
USAGE_MIN = 0.85
USAGE_MAX = 1.25

#: A usage trend needs a real sample on both sides before it means anything.
USAGE_MIN_OPPORTUNITIES = 6.0

#: ESPN stat ids for the things a player has to be given before he can score:
#: targets, carries and pass attempts.
OPPORTUNITY_STAT_IDS = ("58", "23", "0")


def median(values: list[float]) -> float:
    """The middle value -- the typical game, not the best one."""
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


@dataclass
class SeasonForm:
    """What a player has actually done this season, as far as we know."""

    position: str = ""
    games_played: float = 0.0
    #: Season-to-date fantasy points under this league's rules.
    points: float = 0.0
    #: The same for his most recent games (see RECENT_GAMES).
    recent_games: float = 0.0
    recent_points: float = 0.0
    #: Targets + carries + pass attempts, season and recent.
    opportunities: float = 0.0
    recent_opportunities: float = 0.0
    #: Raw stat lines, kept so a screen can show the usage behind the number.
    raw_stats: dict[str, float] = field(default_factory=dict)
    recent_raw_stats: dict[str, float] = field(default_factory=dict)
    #: Each recent game on its own, so one outlier cannot define "lately".
    recent_week_points: list[float] = field(default_factory=list)

    @property
    def has_evidence(self) -> bool:
        return self.games_played > 0

    @property
    def points_per_game(self) -> float:
        return self.points / self.games_played if self.games_played else 0.0

    @property
    def recent_points_per_game(self) -> float:
        """What he scores in a typical recent game.

        The median, not the mean, once there are three to look at. A defence with
        5, 5 and 29 did not become a 13-point defence; it had one week where the
        turnovers fell its way. The median says 5, which is what you should
        expect next Sunday. For a player whose recent games are all good it
        barely moves the number at all -- consistency survives, spikes do not.
        """
        if len(self.recent_week_points) >= 3:
            return median(self.recent_week_points)
        return self.recent_points / self.recent_games if self.recent_games else 0.0


def opportunities_in(raw_stats: dict[str, float]) -> float:
    """Chances to score: targets, carries and pass attempts in one number.

    Crude on purpose. The point is not to model a player's role but to notice
    when it changes, and all three of these move when it does.
    """
    return sum(float(raw_stats.get(stat, 0) or 0) for stat in OPPORTUNITY_STAT_IDS)


def evidence_weight(
    games_played: float,
    half_life: float = EVIDENCE_HALF_LIFE_GAMES,
    position: str = "",
) -> float:
    """How much of the rating actual production should carry, 0-1.

    Longer half-life for the positions that tell you least about themselves: see
    POSITION_HALF_LIFE.
    """
    if games_played <= 0:
        return 0.0
    if position:
        half_life = POSITION_HALF_LIFE.get(position.upper(), half_life)
    return round(games_played / (games_played + half_life), 4)


def actual_rate(form: SeasonForm) -> float:
    """Points per game from production, leaning on the recent games."""
    if not form.has_evidence:
        return 0.0
    season = form.points_per_game
    if form.recent_games <= 0:
        return season
    return round(RECENT_SHARE * form.recent_points_per_game + (1 - RECENT_SHARE) * season, 4)


def usage_multiplier(form: SeasonForm) -> float:
    """How recent opportunity compares with his own season rate, damped.

    Returns 1.0 whenever there is not enough of a sample on both sides to mean
    anything -- a quiet no-opinion rather than a guess.
    """
    if form.games_played <= 0 or form.recent_games <= 0:
        return 1.0
    if form.opportunities < USAGE_MIN_OPPORTUNITIES:
        return 1.0
    if form.recent_opportunities < USAGE_MIN_OPPORTUNITIES / 2:
        return 1.0

    season_rate = form.opportunities / form.games_played
    recent_rate = form.recent_opportunities / form.recent_games
    if season_rate <= 0:
        return 1.0

    swing = recent_rate / season_rate
    damped = 1.0 + USAGE_DAMPING * (swing - 1.0)
    return round(min(max(damped, USAGE_MIN), USAGE_MAX), 4)


def blended_rate(projection_rate: float, form: SeasonForm | None) -> float:
    """Points per game: the preseason projection, corrected by the season so far.

    With no games played this returns the projection unchanged, which is what
    makes the change safe to switch on mid-season: a player nobody has seen yet
    is still valued exactly as he was.
    """
    if form is None or not form.has_evidence:
        return projection_rate

    weight = evidence_weight(form.games_played, position=form.position)
    base = (1 - weight) * projection_rate + weight * actual_rate(form)
    return round(max(base * usage_multiplier(form), 0.0), 4)


def explain(projection_rate: float, form: SeasonForm | None) -> dict:
    """The arithmetic behind a rating, for the screen that shows it."""
    if form is None or not form.has_evidence:
        return {
            "basis": "projection",
            "projection_per_game": round(projection_rate, 2),
            "games_played": 0,
        }
    weight = evidence_weight(form.games_played, position=form.position)
    usage = usage_multiplier(form)
    return {
        "basis": "blended",
        "projection_per_game": round(projection_rate, 2),
        "actual_per_game": round(form.points_per_game, 2),
        "recent_per_game": round(form.recent_points_per_game, 2),
        "games_played": round(form.games_played, 1),
        "evidence_weight": round(weight, 3),
        "usage_multiplier": usage,
        "opportunities_per_game": round(
            form.opportunities / form.games_played if form.games_played else 0.0, 1
        ),
        "recent_opportunities_per_game": round(
            form.recent_opportunities / form.recent_games if form.recent_games else 0.0, 1
        ),
        "blended_per_game": round(blended_rate(projection_rate, form), 2),
    }
