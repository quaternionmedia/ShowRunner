"""Renumber the cues of one layer within a numeric cue range.

Cues are grouped by their main number, so ``5``, ``5.1`` and ``5.2`` move together
and keep their points. Distinct main numbers in the range are renumbered in order:
the lowest becomes ``start``, the next ``start + increment``, and so on.

With ``include_unnumbered``, every unnumbered cue on the layer with a script position is
numbered too. Each one goes after the last unit above it in the script, and takes its
own main number.

Groups on the layer are numbered the same way, as points-free units. A group's script
position is that of its earliest own cue, so a group with no own cues has none.
"""

from sqlmodel import Session, select

from .models import Cue, CueGroup


def _as_int(number: str | None) -> int | None:
    """The main number as an int, or None when unnumbered or not numeric (e.g. ``q42``)."""
    try:
        return int(number)
    except (TypeError, ValueError):
        return None


def _label(item: Cue | CueGroup) -> str:
    return item.number_label if isinstance(item, Cue) else (item.number or '')


def _position(item: Cue | CueGroup, group_pos: dict[int, tuple[int, int]]):
    """Where the item sits in the script as ``(line, char)``, or None when unpositioned."""
    if isinstance(item, CueGroup):
        return group_pos.get(item.id)
    if item.script_line is None:
        return None
    return (item.script_line, item.script_char or 0)


def _unit_position(unit: list, group_pos: dict) -> tuple[int, int] | None:
    """The earliest script position of a unit's items, or None when none are positioned."""
    positions = [p for p in (_position(i, group_pos) for i in unit) if p is not None]
    return min(positions) if positions else None


def renumber_cues(
    session: Session,
    cue_list_id: int,
    layer: str | None,
    cue_range: tuple[int, int],
    *,
    start: int | None = None,
    increment: int = 1,
    include_unnumbered: bool = False,
) -> list[tuple[Cue | CueGroup, str, str]]:
    """Renumber the cues and groups of *layer* whose main number falls in the inclusive *cue_range*.

    *start* is the new number for the lowest main number in range (default: that
    number itself, so the range keeps its first number). *increment* is the step
    between consecutive main numbers and must be at least 1.

    With *include_unnumbered*, every unnumbered cue on the layer with a script position is
    numbered as well, in script order, each after the last unit above it in the script.
    Unnumbered cues without a script position are left alone. If the range has no
    positioned cues, no unnumbered cues are included; if it has no numbered cues at all,
    the layer's positioned cues are numbered from *start* (default: the range's first number).

    Flushes but does not commit; the caller commits. Raises ``ValueError`` before
    changing anything if the new numbers would collide with a cue on the same
    layer that is outside the range.

    Returns ``(item, old_label, new_label)`` for each cue or group changed, in new-number order.
    """
    low, high = cue_range
    if low > high:
        raise ValueError(f'cue range {low}-{high} is backwards')
    if increment < 1:
        raise ValueError(f'increment must be at least 1, not {increment}')

    cues = session.exec(
        select(Cue).where(Cue.cue_list_id == cue_list_id, Cue.layer == layer)
    ).all()
    groups = session.exec(
        select(CueGroup).where(CueGroup.cue_list_id == cue_list_id, CueGroup.layer == layer)
    ).all()
    items = [*cues, *groups]
    numbered = [c for c in items if (n := _as_int(c.number)) is not None and low <= n <= high]
    if not numbered and not include_unnumbered:
        return []

    # Only needed for groups: where each group's earliest own cue sits in the script
    group_pos: dict[int, tuple[int, int]] = {}
    grouped = select(Cue).where(Cue.cue_list_id == cue_list_id, Cue.group_id.is_not(None))
    for cue in session.exec(grouped):
        p = _position(cue, {})
        if p is not None and (cue.group_id not in group_pos or p < group_pos[cue.group_id]):
            group_pos[cue.group_id] = p

    numbered.sort(key=lambda c: (_as_int(c.number), c.point if isinstance(c, Cue) else 0))
    by_number: dict[int, list] = {}
    for item in numbered:
        by_number.setdefault(_as_int(item.number), []).append(item)
    units = list(by_number.values())  # one unit per main number, in number order

    if include_unnumbered:
        # Needs a positioned numbered cue in range to anchor on; with none in range (a fresh
        # list) it anchors on the layer's positioned cues instead
        if any(_position(i, group_pos) is not None for i in (numbered or items)):
            loose = [c for c in items if not c.number and _position(c, group_pos) is not None]
            for item in sorted(loose, key=lambda c: _position(c, group_pos)):
                # After the last unit that starts at or before this item in the script.
                at = 0
                for i, unit in enumerate(units):
                    unit_pos = _unit_position(unit, group_pos)
                    if unit_pos is not None and unit_pos <= _position(item, group_pos):
                        at = i + 1
                units.insert(at, [item])

    if start is not None:
        first = start
    else:
        first = _as_int(numbered[0].number) if numbered else low
    assigned = [
        (item, str(first + i * increment)) for i, unit in enumerate(units) for item in unit
    ]

    ids_in_range = {id(c) for c, _ in assigned}
    taken = {
        (c.number, c.point if isinstance(c, Cue) else 0) for c in items if id(c) not in ids_in_range
    }
    targets = [(number, item.point if isinstance(item, Cue) else 0) for item, number in assigned]
    clashes = sorted({t for t in targets if t in taken or targets.count(t) > 1})
    if clashes:
        labels = ', '.join(f'{num}.{pt}' if pt else num for num, pt in clashes)
        raise ValueError(f'renumbering would clash with existing cues: {labels}')

    changes = []
    for item, number in assigned:
        old_label = _label(item)
        item.number = number
        session.add(item)
        changes.append((item, old_label, _label(item)))
    session.flush()
    return changes
