"""Phase 8 -- deciding what is worth a notification.

The analyses already exist: `weekly.optimise_lineup` knows your best lineup,
`waivers.recommend_waivers` knows who to add. This module answers a different
question -- *which of those findings justifies buzzing someone's phone?*

Two rules shape everything here:

**A notification has a cost.** An alert that turns out not to matter does not
merely waste a second, it teaches the reader to ignore the next one. So each
detector carries an explicit threshold, and anything below it stays in the app
where the user can find it on their own terms.

**The same problem must not alert twice.** Every alert has a stable `key`
derived from the thing it is about, so a caller can remember what it has
already sent. A starter being OUT on Sunday is the same fact at 11am and at
11:30am; only the first one is news.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .waivers import WaiverTarget
from .weekly import StartSitResult, WeeklyPlayer

#: Statuses that mean a player will not take the field. Starting one of these
#: is a guaranteed zero, which is why it outranks every other alert.
OUT_STATUSES = {"OUT", "IR", "SUSPENSION", "INJURY_RESERVE", "NOT_ACTIVE"}

#: Statuses worth mentioning but which do not by themselves break a lineup.
QUESTIONABLE_STATUSES = {"DOUBTFUL", "QUESTIONABLE"}

#: A bench player must beat a starter by this much before it is worth an
#: interruption. `weekly.CLOSE_CALL_POINTS` (1.5) is the bar for *showing* a
#: close call in the UI; waking someone needs a wider margin than that, because
#: weekly projections are not precise enough for a 2-point edge to be real.
START_SIT_ALERT_POINTS = 3.0

#: Minimum priority for a waiver target to be worth a notification on its own.
WAIVER_PRIORITY_FLOOR = 55.0

#: Verdicts that always justify an alert regardless of priority.
URGENT_VERDICTS = {"must-add", "starter"}

SEVERITY_ORDER = {"critical": 0, "important": 1, "fyi": 2}


@dataclass
class Alert:
    """One thing worth telling the user about."""

    #: Stable identity, so the same finding is not delivered twice.
    key: str
    kind: str
    severity: str
    #: Push notification title -- short enough for a lock screen.
    title: str
    #: One line of body text.
    body: str
    week: int
    #: The reasoning, shown when the alert is opened.
    detail: list[str] = field(default_factory=list)
    #: Which screen answers this alert.
    screen: str = "week"

    @property
    def rank(self) -> int:
        return SEVERITY_ORDER.get(self.severity, 99)


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


def detect_lineup_problems(result: StartSitResult) -> list[Alert]:
    """Starters who will score zero, and slots with nobody in them.

    This is the alert that justifies the whole feature. Losing a week because
    a starter was on bye is the most common unforced error in fantasy, it is
    entirely preventable, and it is invisible unless someone looks.
    """
    alerts: list[Alert] = []
    broken: list[tuple[str, WeeklyPlayer, str]] = []

    for decision in result.starters:
        player = decision.player
        if player is None:
            continue
        status = (player.injury_status or "").upper()
        if status in OUT_STATUSES:
            broken.append((decision.slot, player, status.replace("_", " ").title()))
        elif player.on_bye:
            broken.append((decision.slot, player, "on bye"))

    if broken:
        names = ", ".join(f"{p.name} ({why})" for _, p, why in broken)
        # The key names the players, so a lineup that changes re-alerts but an
        # unchanged one does not.
        ids = "-".join(str(p.espn_player_id) for _, p, _ in sorted(broken, key=lambda b: b[1].espn_player_id))
        count = len(broken)
        alerts.append(
            Alert(
                key=f"w{result.week}:lineup_broken:{ids}",
                kind="lineup_broken",
                severity="critical",
                title=f"{count} starter{'s' if count > 1 else ''} will score 0",
                body=names,
                week=result.week,
                detail=[
                    f"{slot}: {p.name} — {why}. Replace before kickoff."
                    for slot, p, why in broken
                ],
            )
        )

    if result.unfilled_slots:
        slots = ", ".join(sorted(set(result.unfilled_slots)))
        alerts.append(
            Alert(
                key=f"w{result.week}:lineup_empty:{slots}",
                kind="lineup_empty",
                severity="critical",
                title="Empty starting slot",
                body=f"Nobody is filling: {slots}",
                week=result.week,
                detail=[f"{slots} has no eligible player. That is a guaranteed zero."],
            )
        )

    return alerts


def detect_start_sit(result: StartSitResult) -> list[Alert]:
    """A bench player clearly beating a starter this week.

    Only fires past `START_SIT_ALERT_POINTS`, because a sub-3-point edge is
    inside the noise of a weekly projection and not worth acting on.
    """
    swaps = [
        d
        for d in result.starters
        if d.player is not None and d.next_best is not None and d.margin >= START_SIT_ALERT_POINTS
    ]
    if not swaps:
        return []

    best = max(swaps, key=lambda d: d.margin)
    ids = "-".join(str(d.next_best.espn_player_id) for d in sorted(swaps, key=lambda d: d.slot))
    return [
        Alert(
            key=f"w{result.week}:start_sit:{ids}",
            kind="start_sit",
            severity="important",
            title="Better lineup available",
            body=f"{best.next_best.name} over {best.player.name} (+{best.margin:.1f} pts)",
            week=result.week,
            detail=[
                f"{d.slot}: start {d.next_best.name} over {d.player.name} "
                f"(+{d.margin:.1f} projected)"
                for d in sorted(swaps, key=lambda d: d.margin, reverse=True)
            ],
        )
    ]


def detect_waiver_targets(targets: list[WaiverTarget], week: int) -> list[Alert]:
    """Adds worth spending on, batched into one notification.

    One alert rather than one per player: the decision is "who do I put a claim
    in for tonight", which is a single sitting, not five interruptions.
    """
    worth_it = [
        t
        for t in targets
        if t.verdict in URGENT_VERDICTS or t.priority >= WAIVER_PRIORITY_FLOOR
    ]
    if not worth_it:
        return []

    worth_it.sort(key=lambda t: t.priority, reverse=True)
    top = worth_it[0]
    ids = "-".join(str(t.player.espn_player_id) for t in sorted(worth_it, key=lambda t: t.player.espn_player_id))

    severity = "important" if any(t.verdict in URGENT_VERDICTS for t in worth_it) else "fyi"
    others = len(worth_it) - 1
    body = f"{top.player.name} ({top.player.position})"
    if top.faab_bid:
        body += f" — bid ${top.faab_bid}"
    if others:
        body += f", +{others} more"

    return [
        Alert(
            key=f"w{week}:waivers:{ids}",
            kind="waivers",
            severity=severity,
            title="Waiver targets ready",
            body=body,
            week=week,
            screen="waivers",
            detail=[
                f"{t.player.name} ({t.player.position}) — {t.verdict}, "
                f"bid ${t.faab_bid}"
                + (f", drop {t.drop.name}" if t.drop else "")
                for t in worth_it[:6]
            ],
        )
    ]


def detect_injury_changes(
    players: list[WeeklyPlayer],
    previous_statuses: dict[int, str],
) -> list[Alert]:
    """Roster players whose injury status got worse since the last check.

    Improvements are deliberately not alerted -- good news is not urgent, and
    it shows up on the Week screen anyway.
    """
    worsened: list[tuple[WeeklyPlayer, str, str]] = []
    for player in players:
        was = (previous_statuses.get(player.espn_player_id) or "").upper()
        now = (player.injury_status or "").upper()
        if not was or was == now:
            continue
        if _severity_of(now) > _severity_of(was):
            worsened.append((player, was, now))

    if not worsened:
        return []

    ids = "-".join(str(p.espn_player_id) for p, _, _ in sorted(worsened, key=lambda w: w[0].espn_player_id))
    headline = worsened[0][0]
    return [
        Alert(
            key=f"injury:{ids}:" + "-".join(now for _, _, now in worsened),
            kind="injury",
            severity="important" if any(n in OUT_STATUSES for _, _, n in worsened) else "fyi",
            title="Injury update on your roster",
            body=f"{headline.name} is now {worsened[0][2].title()}"
            + (f", +{len(worsened) - 1} more" if len(worsened) > 1 else ""),
            week=0,
            detail=[
                f"{p.name} ({p.position}): {was.title()} → {now.title()}"
                for p, was, now in worsened
            ],
        )
    ]


def _severity_of(status: str) -> int:
    status = (status or "").upper()
    if status in OUT_STATUSES:
        return 3
    if status == "DOUBTFUL":
        return 2
    if status == "QUESTIONABLE":
        return 1
    return 0


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def collect(
    *,
    start_sit: StartSitResult | None = None,
    waiver_targets: list[WaiverTarget] | None = None,
    roster: list[WeeklyPlayer] | None = None,
    previous_statuses: dict[int, str] | None = None,
    already_sent: set[str] | None = None,
) -> list[Alert]:
    """Every alert worth sending right now, most urgent first.

    `already_sent` carries the keys a caller has previously delivered; those
    are filtered out so a persistent problem is reported once rather than on
    every scheduled run.
    """
    alerts: list[Alert] = []

    if start_sit is not None:
        alerts.extend(detect_lineup_problems(start_sit))
        alerts.extend(detect_start_sit(start_sit))
    if waiver_targets:
        week = start_sit.week if start_sit else 0
        alerts.extend(detect_waiver_targets(waiver_targets, week))
    if roster and previous_statuses:
        alerts.extend(detect_injury_changes(roster, previous_statuses))

    if already_sent:
        alerts = [a for a in alerts if a.key not in already_sent]

    alerts.sort(key=lambda a: (a.rank, a.key))
    return alerts
