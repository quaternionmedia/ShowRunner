"""Renumber the cues of one layer within a numeric cue range.

Cues are grouped by their main number, so ``5``, ``5.1`` and ``5.2`` move together
and keep their points. Distinct main numbers in the range are renumbered in order:
the lowest becomes ``start``, the next ``start + increment``, and so on.

With ``include_unnumbered``, unnumbered cues whose script position falls within the
range's span are numbered too. Each one goes after the last unit above it in the
script, and takes its own main number.
"""

from sqlmodel import Session, select

from .models import Cue


def _as_int(number: str | None) -> int | None:
    """The main number as an int, or None when unnumbered or not numeric (e.g. ``q42``)."""
    try:
        return int(number)
    except (TypeError, ValueError):
        return None


def _position(cue: Cue) -> tuple[int, int] | None:
    """Where the cue sits in the script as ``(line, char)``, or None when unpositioned."""
    if cue.script_line is None:
        return None
    return (cue.script_line, cue.script_char or 0)


def _unit_position(unit: list[Cue]) -> tuple[int, int] | None:
    """The earliest script position of a unit's cues, or None when none are positioned."""
    positions = [p for p in map(_position, unit) if p is not None]
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
) -> list[tuple[int, str, str]]:
    """Renumber the cues of *layer* whose main number falls in the inclusive *cue_range*.

    *start* is the new number for the lowest main number in range (default: that
    number itself, so the range keeps its first number). *increment* is the step
    between consecutive main numbers and must be at least 1.

    With *include_unnumbered*, unnumbered cues on the layer whose script position lies
    between the first and last positioned cue in range are numbered as well, in script
    order. Unnumbered cues without a script position are left alone. If the range has
    no positioned cues, there is no span and no unnumbered cues are included.

    Flushes but does not commit; the caller commits. Raises ``ValueError`` before
    changing anything if the new numbers would collide with a cue on the same
    layer that is outside the range.

    Returns ``(cue_id, old_label, new_label)`` for each cue changed, in new-number order.
    """
    low, high = cue_range
    if low > high:
        raise ValueError(f'cue range {low}-{high} is backwards')
    if increment < 1:
        raise ValueError(f'increment must be at least 1, not {increment}')

    cues = session.exec(
        select(Cue).where(Cue.cue_list_id == cue_list_id, Cue.layer == layer)
    ).all()
    numbered = [c for c in cues if (n := _as_int(c.number)) is not None and low <= n <= high]
    if not numbered:
        return []

    numbered.sort(key=lambda c: (_as_int(c.number), c.point))
    groups: dict[int, list[Cue]] = {}
    for cue in numbered:
        groups.setdefault(_as_int(cue.number), []).append(cue)
    units = list(groups.values())  # one unit per main number, in number order

    if include_unnumbered:
        positions = [p for p in map(_position, numbered) if p is not None]
        if positions:
            span = (min(positions), max(positions))
            loose = [
                c
                for c in cues
                if not c.number and (p := _position(c)) is not None and span[0] <= p <= span[1]
            ]
            for cue in sorted(loose, key=_position):
                # After the last unit that starts at or before this cue in the script.
                at = 0
                for i, unit in enumerate(units):
                    unit_pos = _unit_position(unit)
                    if unit_pos is not None and unit_pos <= _position(cue):
                        at = i + 1
                units.insert(at, [cue])

    first = _as_int(numbered[0].number) if start is None else start
    assigned = [
        (cue, str(first + i * increment)) for i, unit in enumerate(units) for cue in unit
    ]

    ids_in_range = {c.id for c, _ in assigned}
    taken = {(c.number, c.point) for c in cues if c.id not in ids_in_range}
    targets = [(number, cue.point) for cue, number in assigned]
    clashes = sorted({t for t in targets if t in taken or targets.count(t) > 1})
    if clashes:
        labels = ', '.join(f'{num}.{pt}' if pt else num for num, pt in clashes)
        raise ValueError(f'renumbering would clash with existing cues: {labels}')

    changes = []
    for cue, number in assigned:
        old_label = cue.number_label
        cue.number = number
        session.add(cue)
        changes.append((cue.id, old_label, cue.number_label))
    session.flush()
    return changes
