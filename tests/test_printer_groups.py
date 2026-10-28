"""PDF printer: group boxes drawn row by row, from each group's first to last member cue."""

import re

import pytest

pytest.importorskip('fpdf')

from showrunner.models import Cue, Script  # noqa: E402
from showrunner.plugins.printer import (  # noqa: E402
    GROUP_CHIP_W,
    _group_extent,
    _group_label,
    _load_layout,
    _minus,
    _place_cues,
    _Row,
    generate_pdf,
)


def _script(lines: int) -> Script:
    text = '\n'.join(f'Line of action number {i}.' for i in range(lines))
    return Script(id=1, show_id=1, title='T', format='fountain', content=text)


def _cues(n: int, lines: int) -> list[Cue]:
    return [
        Cue(
            id=i + 1, cue_list_id=1, number=i + 1, name=f'c{i}', layer='Lights',
            script_line=(i * 2) % lines + 1,
        )
        for i in range(n)
    ]


def _pages(pdf: bytes) -> int:
    return len(re.findall(rb'/Type\s*/Page[^s]', pdf))


def test_group_across_a_page_break_prints_on_both_pages():
    # 80 lines do not fit one page, so a group over the first and last cue spans a break.
    script = _script(80)
    cues = _cues(2, 80)
    group = {'id': 1, 'name': 'Act', 'color': 'orange', 'parent_id': None,
             'cue_ids': [1, 2], 'member_ids': [1, 2]}
    layout = _load_layout()
    plain = generate_pdf(script, cues, layout, {})
    boxed = generate_pdf(script, cues, layout, {}, groups=[group])

    assert boxed.startswith(b'%PDF')
    assert _pages(boxed) == _pages(plain) >= 2
    assert boxed != plain  # outlines were drawn


def test_nested_groups_print_without_error():
    script = _script(10)
    cues = _cues(3, 10)
    outer = {'id': 1, 'name': 'Outer', 'color': 'blue', 'parent_id': None,
             'cue_ids': [], 'member_ids': [1, 2, 3]}
    inner = {'id': 2, 'name': 'Inner', 'color': 'red', 'parent_id': 1,
             'cue_ids': [1], 'member_ids': [1]}
    out = generate_pdf(script, cues, _load_layout(), {}, groups=[outer, inner])
    assert out.startswith(b'%PDF')


def test_group_label_shows_number_before_name():
    assert _group_label({'name': 'Act', 'number': '5', 'layer': 'Q'}) == '5 Act'
    assert _group_label({'name': '', 'number': '', 'layer': 'Q'}) == 'Group'


def _row(cue_x: dict[int, float]) -> _Row:
    return _Row(start=0, text='', x=10, width=0, cell_x=10, cell_w=150, cue_x=cue_x)


def test_sibling_groups_on_one_row_sit_side_by_side():
    row = _row({1: 20.0, 2: 90.0})
    a_left, a_right = _group_extent(row, {1}, pad=1.5)
    b_left, b_right = _group_extent(row, {2}, pad=1.5)
    assert a_right < b_left  # neither encloses the other


def test_parent_group_encloses_its_child_on_one_row():
    row = _row({1: 20.0, 2: 90.0})
    child = _group_extent(row, {1}, pad=1.5)
    parent = _group_extent(row, {1, 2}, pad=3.0)  # parent's members include the child's
    assert parent[0] < child[0] and parent[1] > child[1]


def test_member_less_row_takes_the_full_text_width():
    row = _row({})
    assert _group_extent(row, {1}, pad=0) == (10, 160)
    assert GROUP_CHIP_W > 0


def test_minus_returns_the_parts_outside_another_interval():
    assert _minus(0, 10, 20, 30) == [(0, 10)]
    assert _minus(0, 10, 4, 6) == [(0, 4), (6, 10)]
    assert _minus(0, 10, -5, 5) == [(5, 10)]
    assert _minus(0, 10, 0, 10) == []


def test_cue_badge_lands_on_its_own_row_of_a_multi_line_element():
    # Regression: a Fountain element spans many lines; markers were drawn once per
    # element, so a cue on a later row lost its badge.
    from fpdf import FPDF
    from types import SimpleNamespace

    pdf = FPDF(unit='mm', format='A4')
    pdf.add_page()
    pdf.set_font('Helvetica', '', 10)
    rows = [_Row(0, 'Line one', 10, 0, 10, 150), _Row(9, 'Line two', 10, 0, 10, 150)]
    cue = SimpleNamespace(id=7)
    _place_cues(pdf, rows, [(cue, 12)], lambda c: ('L1', (1, 2, 3), 6.0))
    assert rows[0].cues == []
    assert rows[1].cues == [cue]
    assert rows[1].badges == [(3, 'L1', (1, 2, 3), 6.0)]
    assert rows[1].cue_x[7] > rows[1].x
