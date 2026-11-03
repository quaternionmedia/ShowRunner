"""Tests for renumbering cues within a layer and numeric range."""

import pytest
from sqlmodel import select

from showrunner.models import Cue, CueList, Show
from showrunner.renumber import renumber_cues


def _setup(db, cues):
    """Create a show with one cue list holding *cues* given as (number, point, layer)."""
    with db.session() as s:
        show = Show(name='Test')
        s.add(show)
        s.commit()
        cl = CueList(show_id=show.id, name='Main')
        s.add(cl)
        s.commit()
        ids = []
        for number, point, layer in cues:
            cue = Cue(cue_list_id=cl.id, number=number, point=point, layer=layer)
            s.add(cue)
            s.commit()
            ids.append(cue.id)
        return cl.id, ids


def _labels(db, cue_list_id, layer):
    with db.session() as s:
        cues = s.exec(select(Cue).where(Cue.cue_list_id == cue_list_id, Cue.layer == layer)).all()
        return sorted(
            (int(c.number), c.point) for c in cues if c.number is not None and c.number.isdigit()
        )


def _renumber(db, cue_list_id, layer, cue_range, **opts):
    with db.session() as s:
        changes = renumber_cues(s, cue_list_id, layer, cue_range, **opts)
        s.commit()
        return changes


def test_increment_renumbers_range_and_keeps_points(db):
    cl, _ = _setup(
        db, [('5', 0, 'Lights'), ('5', 1, 'Lights'), ('6', 0, 'Lights'), ('7', 0, 'Lights')]
    )
    changes = _renumber(db, cl, 'Lights', (5, 7), start=10, increment=2)
    assert _labels(db, cl, 'Lights') == [(10, 0), (10, 1), (12, 0), (14, 0)]
    assert [(old, new) for _, old, new in changes] == [
        ('5', '10'),
        ('5.1', '10.1'),
        ('6', '12'),
        ('7', '14'),
    ]


def test_start_defaults_to_lowest_number_in_range(db):
    cl, _ = _setup(db, [('20', 0, 'Sound'), ('22', 0, 'Sound')])
    _renumber(db, cl, 'Sound', (20, 25), increment=5)
    assert _labels(db, cl, 'Sound') == [(20, 0), (25, 0)]


def test_only_the_named_layer_and_range_change(db):
    cl, _ = _setup(
        db,
        [
            ('5', 0, 'Lights'),
            ('5', 0, 'Sound'),
            ('9', 0, 'Lights'),
            ('q42', 0, 'Lights'),
            (None, 0, 'Lights'),
        ],
    )
    changes = _renumber(db, cl, 'Lights', (1, 6), start=100)
    assert len(changes) == 1
    assert _labels(db, cl, 'Lights') == [(9, 0), (100, 0)]
    assert _labels(db, cl, 'Sound') == [(5, 0)]


def test_collision_with_cue_outside_range_raises_and_changes_nothing(db):
    cl, _ = _setup(db, [('5', 0, 'Lights'), ('6', 0, 'Lights'), ('10', 0, 'Lights')])
    with db.session() as s:
        with pytest.raises(ValueError, match='clash'):
            renumber_cues(s, cl, 'Lights', (5, 6), start=9, increment=1)
    assert _labels(db, cl, 'Lights') == [(5, 0), (6, 0), (10, 0)]


def test_same_layer_cue_on_other_layer_is_not_a_clash(db):
    cl, _ = _setup(db, [('5', 0, 'Lights'), ('10', 0, 'Sound')])
    _renumber(db, cl, 'Lights', (5, 5), start=10)
    assert _labels(db, cl, 'Lights') == [(10, 0)]


def test_empty_range_returns_no_changes(db):
    cl, _ = _setup(db, [('5', 0, 'Lights')])
    assert _renumber(db, cl, 'Lights', (50, 60)) == []


def test_backwards_range_and_bad_increment_raise(db):
    cl, _ = _setup(db, [('5', 0, 'Lights')])
    with db.session() as s:
        with pytest.raises(ValueError, match='backwards'):
            renumber_cues(s, cl, 'Lights', (7, 3))
        with pytest.raises(ValueError, match='increment'):
            renumber_cues(s, cl, 'Lights', (1, 9), increment=0)


def _setup_positioned(db, cues):
    """Like ``_setup`` but each cue is ``(number, point, layer, script_line)``."""
    with db.session() as s:
        show = Show(name='Test')
        s.add(show)
        s.commit()
        cl = CueList(show_id=show.id, name='Main')
        s.add(cl)
        s.commit()
        for number, point, layer, line in cues:
            s.add(
                Cue(cue_list_id=cl.id, number=number, point=point, layer=layer, script_line=line)
            )
        s.commit()
        return cl.id


def _numbers(db, cue_list_id, layer):
    """``[(script_line, number_label)]`` for every cue on the layer, in script order."""
    with db.session() as s:
        cues = s.exec(select(Cue).where(Cue.cue_list_id == cue_list_id, Cue.layer == layer)).all()
        return sorted((c.script_line or 0, c.number_label) for c in cues)


def test_unnumbered_inside_span_is_numbered_in_script_order(db):
    cl = _setup_positioned(
        db,
        [
            ('5', 0, 'Lights', 5),
            (None, 0, 'Lights', 6),
            ('7', 0, 'Lights', 8),
            (None, 0, 'Lights', 9),
        ],
    )
    with db.session() as s:
        renumber_cues(s, cl, 'Lights', (5, 7), start=10, include_unnumbered=True)
        s.commit()
    assert _numbers(db, cl, 'Lights') == [(5, '10'), (6, '11'), (8, '12'), (9, '')]


def test_unnumbered_outside_span_is_left_alone(db):
    cl = _setup_positioned(
        db,
        [
            (None, 0, 'Lights', 1),
            ('5', 0, 'Lights', 5),
            ('7', 0, 'Lights', 8),
            (None, 0, 'Lights', 20),
        ],
    )
    with db.session() as s:
        changes = renumber_cues(s, cl, 'Lights', (5, 7), start=10, include_unnumbered=True)
        s.commit()
    assert len(changes) == 2
    assert _numbers(db, cl, 'Lights') == [(1, ''), (5, '10'), (8, '11'), (20, '')]


def test_unnumbered_without_script_position_is_skipped(db):
    cl = _setup_positioned(
        db, [('5', 0, 'Lights', 5), (None, 0, 'Lights', None), ('7', 0, 'Lights', 8)]
    )
    with db.session() as s:
        renumber_cues(s, cl, 'Lights', (5, 7), include_unnumbered=True)
        s.commit()
    assert _numbers(db, cl, 'Lights') == [(0, ''), (5, '5'), (8, '6')]


def test_unnumbered_left_alone_by_default(db):
    cl = _setup_positioned(
        db, [('5', 0, 'Lights', 5), (None, 0, 'Lights', 6), ('7', 0, 'Lights', 8)]
    )
    with db.session() as s:
        renumber_cues(s, cl, 'Lights', (5, 7), start=10)
        s.commit()
    assert _numbers(db, cl, 'Lights') == [(5, '10'), (6, ''), (8, '11')]


def test_include_unnumbered_with_no_positioned_cues_in_range_adds_nothing(db):
    cl = _setup_positioned(db, [('5', 0, 'Lights', None), (None, 0, 'Lights', 6)])
    with db.session() as s:
        changes = renumber_cues(s, cl, 'Lights', (5, 5), include_unnumbered=True)
        s.commit()
    assert len(changes) == 1
    assert _numbers(db, cl, 'Lights') == [(0, '5'), (6, '')]
