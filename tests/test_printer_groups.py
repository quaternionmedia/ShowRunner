"""PDF printer: group outlines per page segment."""

import re

import pytest

pytest.importorskip('fpdf')

from showrunner.models import Cue, Script  # noqa: E402
from showrunner.plugins.printer import _load_layout, generate_pdf  # noqa: E402


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
