"""Reading Yahoo's JSON, which is XML wearing a JSON hat.

Yahoo's Fantasy API is XML-first; `?format=json` mechanically transliterates
it, and the result has two habits that nothing else does:

1. **Collections are numbered objects.**  A list of teams arrives as
   ``{"0": {"team": ...}, "1": {"team": ...}, "count": 2}`` -- an object whose
   keys are stringified indices, with a stray ``count`` sitting among them.
2. **One object is split across many fragments.**  A team is
   ``[[{"team_key": ...}, {"team_id": ...}, {"name": ...}], {"roster": ...}]``:
   a list of single-key dicts, sometimes nested one level deeper, with the
   sub-resources you asked for appended after it.

Everything in this module exists to turn those two shapes back into ordinary
dicts and lists, defensively -- a payload we cannot read yields nothing rather
than raising, because Yahoo returning an unexpected shape mid-draft should
cost a number, not the app.
"""

from __future__ import annotations

from typing import Any


def items(node: Any) -> list[Any]:
    """The members of a Yahoo collection, in order.

    Accepts either the numbered-object form or a plain list, so callers do not
    have to care which one a particular endpoint used.
    """
    if isinstance(node, list):
        return [entry for entry in node if entry not in (None, "", [], {})]
    if isinstance(node, dict):
        numbered = [(int(key), value) for key, value in node.items() if str(key).isdigit()]
        return [value for _, value in sorted(numbered)]
    return []


def collection(parent: dict, plural: str, singular: str) -> list[Any]:
    """Members of ``parent[plural]``, unwrapped from their ``singular`` key.

    ``collection(league, "teams", "team")`` -> the raw fragment list for each
    team, ready for `merge`.
    """
    out: list[Any] = []
    for entry in items((parent or {}).get(plural)):
        if isinstance(entry, dict) and singular in entry:
            out.append(entry[singular])
        elif entry not in (None, [], {}):
            out.append(entry)
    return out


def deep_collection(parent: Any, plural: str, singular: str) -> list[Any]:
    """`collection`, but tolerant of one extra numbered wrapper.

    A roster arrives as ``{"0": {"players": {...}}, "coverage_type": "date"}``:
    the collection you want sits one level below a numbered key rather than
    beside it, and which of the two Yahoo uses varies by endpoint.
    """
    direct = collection(merge(parent), plural, singular)
    if direct:
        return direct
    for member in items(parent):
        found = collection(merge(member), plural, singular)
        if found:
            return found
    return []


def merge(node: Any) -> dict:
    """Collapse Yahoo's fragment list for one object into a single dict.

    Nested lists are flattened; numbered collection keys are left alone (they
    are handled by `collection`, which needs the container intact).  Later
    fragments win, which matches Yahoo appending sub-resources at the end.

    ``count`` is dropped only alongside numbered keys, where it is Yahoo's
    collection size. Elsewhere it is a real field -- a roster position's
    ``count`` is how many of that slot the league starts, and dropping it
    silently emptied every roster slot.
    """
    out: dict = {}
    if isinstance(node, dict):
        numbered = any(str(key).isdigit() for key in node)
        for key, value in node.items():
            if str(key).isdigit() or (key == "count" and numbered):
                continue
            out[key] = value
    elif isinstance(node, list):
        for entry in node:
            out.update(merge(entry))
    return out


def sub_resource(fragment: Any, name: str) -> dict:
    """A named sub-resource (``draft_analysis``, ``ownership``, ...) as a dict.

    Yahoo returns these as either a dict or a one-element fragment list, and
    occasionally wraps them again under their own name.
    """
    merged = merge(fragment)
    node = merged.get(name)
    if node is None:
        return {}
    inner = merge(node) if not isinstance(node, dict) else node
    if isinstance(inner, dict) and name in inner and isinstance(inner[name], (dict, list)):
        inner = merge(inner[name])
    return inner if isinstance(inner, dict) else {}


def num(value: Any, default: float = 0.0) -> float:
    """Yahoo sends numbers as strings, and empty fields as ``""`` or ``"-"``."""
    if value is None:
        return default
    if isinstance(value, bool):
        return float(value)
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "--", "N/A"}:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def integer(value: Any, default: int | None = None) -> int | None:
    result = num(value, float("nan"))
    if result != result:  # NaN -- unparseable
        return default
    return int(result)


def text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return default
    return str(value).strip() or default


def truthy(value: Any) -> bool:
    """Yahoo's booleans are 1/0, "1"/"0", or occasionally true/false."""
    if isinstance(value, bool):
        return value
    return text(value).lower() in {"1", "true", "yes"}
