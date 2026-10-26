"""ShowPrinter - PDF export of scripts with cue annotations.

Provides a FastAPI route at ``/export/script/{script_id}/pdf`` that renders a
Fountain script to PDF with cue markers drawn in the margin beside their
associated lines.

Layout is driven by a TOML template (``pdf_layout.toml``) that ships alongside
this module and can be overridden via ``[plugins.showprinter]`` settings in
``show.toml``.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import tomllib
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from screenplay_tools.fountain.parser import Parser as FountainParser
from screenplay_tools.screenplay import ElementType
from sqlmodel import select

import showrunner
from showrunner import ui as _sr_ui
from showrunner.groups import group_depths, group_tree
from showrunner.layers import COLOR_RGB, cue_number_label, get_layers, hook_results
from showrunner.models import CUE_ORDER, Cue, CueList, Script
from showrunner.plugins.db import get_db

try:
    from fpdf import FPDF
except ImportError:
    print("Warning: fpdf2 is not installed. ShowPrinterPlugin will be unavailable.")
    FPDF = None

router = APIRouter(prefix='/export', tags=['ShowPrinter'])

_DEFAULT_LAYOUT = Path(__file__).resolve().parent.parent / 'pdf_layout.toml'

# Map Fountain ElementType values to layout section keys.
_ELEMENT_KEY: dict[ElementType, str] = {
    ElementType.HEADING: 'heading',
    ElementType.ACTION: 'action',
    ElementType.CHARACTER: 'character',
    ElementType.DIALOGUE: 'dialogue',
    ElementType.PARENTHETICAL: 'parenthetical',
    ElementType.LYRIC: 'lyric',
    ElementType.TRANSITION: 'transition',
    ElementType.SECTION: 'section',
    ElementType.SYNOPSIS: 'synopsis',
}


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------


def _load_layout(override_path: str | None = None) -> dict[str, Any]:
    """Load and return the layout configuration dict from TOML.

    Falls back to the built-in ``pdf_layout.toml`` when *override_path* is
    ``None`` or the file does not exist.
    """
    safe_root = _DEFAULT_LAYOUT.parent.resolve()
    path = _DEFAULT_LAYOUT

    if override_path:
        # Accept only a simple TOML filename (no directory components).
        # This prevents traversal, absolute paths, and path expression abuse.
        name = Path(override_path).name
        if (
            name
            and name == override_path
            and name not in {'.', '..'}
            and re.fullmatch(r'[A-Za-z0-9_.-]+\.toml', name)
        ):
            allowed_layouts = {p.name: p for p in safe_root.glob('*.toml') if p.is_file()}
            path = allowed_layouts.get(name, _DEFAULT_LAYOUT)

    # Canonicalize and enforce containment within safe_root before file access.
    try:
        candidate = path.resolve()
    except OSError:
        candidate = _DEFAULT_LAYOUT

    if candidate != safe_root and safe_root not in candidate.parents:
        candidate = _DEFAULT_LAYOUT

    if not candidate.is_file():
        candidate = _DEFAULT_LAYOUT
    with open(candidate, 'rb') as f:
        return tomllib.load(f)


def _element_style(layout: dict, element_type: ElementType) -> dict[str, Any]:
    """Return the style dict for a Fountain element type, with defaults."""
    key = _ELEMENT_KEY.get(element_type)
    defaults = {
        'font': 'Courier',
        'size': 12,
        'align': 'left',
        'uppercase': False,
        'space-before': 0,
        'space-after': 0,
        'margin-left': 0,
        'margin-right': 0,
    }
    if key is None:
        return defaults
    style = layout.get('elements', {}).get(key, {})
    return {**defaults, **style}


def _cue_color(layout: dict, layer: str, layer_colors: dict[str, str]) -> tuple[int, int, int]:
    """(R, G, B) for a cue layer: the layout's ``[cues.colors]`` override, else the layer colour."""
    override = layout.get('cues', {}).get('colors', {}).get(layer)
    if override is not None:
        return (int(override[0]), int(override[1]), int(override[2]))
    return _colour_rgb(layer_colors.get(layer, 'grey'))


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------


# fpdf2's built-in core fonts (Helvetica, Courier, ...) only cover Latin-1.
_PDF_PUNCT = str.maketrans({
    '‘': "'", '’': "'", '‚': "'",
    '“': '"', '”': '"', '„': '"',
    '–': '-', '—': '-', '…': '...',
})


# Quasar's 500-shade palette, for preview chip colours beyond the layer colours.
_QUASAR_RGB = {
    'red': (244, 67, 54), 'pink': (233, 30, 99), 'purple': (156, 39, 176),
    'deep-purple': (103, 58, 183), 'indigo': (63, 81, 181), 'blue': (33, 150, 243),
    'light-blue': (3, 169, 244), 'cyan': (0, 188, 212), 'teal': (0, 150, 136),
    'green': (76, 175, 80), 'light-green': (139, 195, 74), 'lime': (205, 220, 57),
    'yellow': (255, 235, 59), 'amber': (255, 193, 7), 'orange': (255, 152, 0),
    'deep-orange': (255, 87, 34), 'brown': (121, 85, 72), 'grey': (158, 158, 158),
    'blue-grey': (96, 125, 139), 'black': (0, 0, 0), 'white': (255, 255, 255),
}


def _colour_rgb(colour: str | None) -> tuple[int, int, int]:
    """(R, G, B) for a Quasar colour name, shade suffix ignored (``grey-9`` is grey).

    The seven app layer colours keep their own values; everything else comes from the
    Quasar palette. Unknown or missing names fall back to grey.
    """
    if not isinstance(colour, str):
        return _QUASAR_RGB['grey']
    base = re.sub(r'-\d+$', '', colour)
    return COLOR_RGB.get(base) or _QUASAR_RGB.get(base) or _QUASAR_RGB['grey']


def _latin1(text: str) -> str:
    """Make *text* safe for core fonts: map typographic punctuation, replace the rest."""
    return text.translate(_PDF_PUNCT).encode('latin-1', 'replace').decode('latin-1')


# Group outline geometry, in mm
GROUP_LINE = 0.3
GROUP_PAD = 1.5  # outline distance from the text column, for the outermost group
GROUP_STEP = 1.2  # extra distance per nesting level
GROUP_TOP_GAP = 0.4
GROUP_LABEL_SIZE = 7  # pt
GROUP_LABEL_H = 3.6  # room reserved above a group's top edge for its name
GROUP_CHIP_W = 2.0  # width (mm) a cue's position takes inside a group box


@dataclass
class _Row:
    """One printed text row of an element, with the cues whose script position falls on it."""

    start: int  # offset of the row's first character in the element text
    text: str
    x: float  # left edge of the printed text
    width: float
    cell_x: float
    cell_w: float
    align: str = 'L'
    cue_x: dict[int, float] = field(default_factory=dict)  # cue id -> left edge of its slot
    cue_w: dict[int, float] = field(default_factory=dict)  # cue id -> width of its badge slot
    badges: list[tuple[int, str, tuple[int, int, int], float]] = field(default_factory=list)
    cues: list[Cue] = field(default_factory=list)  # cues whose position falls on this row
    gidx: int = 0  # index among all rows of the script


@dataclass
class _Plan:
    """An element's font, cell and printed rows, laid out before any drawing."""

    font_name: str
    font_style: str
    style: dict
    cell_x: float
    cell_w: float
    rows: list[_Row]
    lines: list[int]  # original 1-based script lines the element covers


def _element_text(elem, style: dict) -> str:
    """The text an element prints: character names with extension, bracketed parentheticals,
    and the layout's uppercase setting."""
    text = elem.text or ''
    if elem.type == ElementType.CHARACTER:
        text = elem.name or ''
        if getattr(elem, 'extension', None):
            text += f' ({elem.extension})'
    elif elem.type == ElementType.PARENTHETICAL:
        text = f'({text})'
    if style.get('uppercase', False):
        text = text.upper()
    return _latin1(text)


def _split_rows(pdf: FPDF, text: str, cell_x: float, cell_w: float, align: str) -> list[_Row]:
    """Wrap *text* the way multi_cell does, and place each row as the cell would."""
    cm = pdf.c_margin
    rows: list[_Row] = []
    cursor = 0
    for line in pdf.multi_cell(cell_w, 1, text, dry_run=True, output='LINES'):
        start = text.find(line, cursor)
        if start < 0:
            start = cursor
        cursor = start + len(line)
        width = pdf.get_string_width(line)
        if align == 'R':
            x = cell_x + cell_w - cm - width
        elif align == 'C':
            x = cell_x + (cell_w - width) / 2
        else:
            x = cell_x + cm
        rows.append(_Row(start, line, x, width, cell_x, cell_w, align))
    return rows


def _cue_char_offsets(
    text: str, line_nos: list[int], cues_by_line: dict[int, list[Cue]], content_lines: list[str]
) -> list[tuple[Cue, int]]:
    """Where each cue sits in the element text: its script_char, shifted past the markup
    the parser strips from the start of its line (for example '#' or '@')."""
    pieces = text.split('\n')
    aligned = len(pieces) == len(line_nos)  # one printed piece per script line
    out: list[tuple[Cue, int]] = []
    base = 0
    for k, ln in enumerate(line_nos):
        piece = pieces[k] if aligned else ''
        raw = content_lines[ln - 1].lower() if ln - 1 < len(content_lines) else ''
        core = piece.strip().lower()
        off = max(raw.find(core), 0) if core else 0
        for cue in cues_by_line.get(ln, []):
            pos = min(max((cue.script_char or 0) - off, 0), len(piece))
            out.append((cue, base + pos if aligned else 0))
        if aligned:
            base += len(piece) + 1
    return out


def _place_cues(pdf: FPDF, rows: list[_Row], offsets: list[tuple[Cue, int]], badge_of) -> None:
    """Give each cue a slot on the row that holds its character offset. A badge takes its
    slot inline, pushing the text after it right; ``badge_of(cue)`` returns
    ``(label, rgb, slot_width)`` or None for a cue with no badge."""
    if not rows:
        return
    starts = [row.start for row in rows]
    placed: dict[int, list[tuple[int, Cue]]] = {}
    for cue, i in offsets:
        ri = max(bisect_right(starts, i) - 1, 0)
        k = min(max(i - rows[ri].start, 0), len(rows[ri].text))
        placed.setdefault(ri, []).append((k, cue))
    body = (pdf.font_family, pdf.font_style, pdf.font_size_pt)
    for ri, items in placed.items():
        row = rows[ri]
        items.sort(key=lambda item: item[0])
        badges = [(k, cue, badge_of(cue)) for k, cue in items]
        total = sum(b[2] for _, _, b in badges if b)
        row.x -= total * {'L': 0.0, 'C': 0.5, 'R': 1.0}.get(row.align, 0.0)
        row.width += total
        pdf.set_font(*body)  # badge_of sets its own font
        acc = 0.0
        for k, cue, b in badges:
            x = row.x + pdf.get_string_width(row.text[:k]) + acc
            row.cue_x[cue.id] = x
            row.cues.append(cue)
            if b:
                row.cue_w[cue.id] = b[2]
                row.badges.append((k, b[0], b[1], b[2]))
                acc += b[2]
    for row in rows:
        row.badges.sort(key=lambda t: t[0])


def _draw_row(
    pdf: FPDF, row: _Row, y: float, h: float, font: tuple[str, str, float],
    badge: tuple[str, str, float, float],
) -> None:
    """Print one row: its text with each badge inline at its cue's slot."""
    x, pos = row.x, 0

    def text(seg: str) -> None:
        nonlocal x
        if seg:
            pdf.set_xy(x - pdf.c_margin, y)
            pdf.cell(pdf.get_string_width(seg) + 2 * pdf.c_margin, h, seg)
            x += pdf.get_string_width(seg)

    pdf.set_font(*font)
    pdf.set_text_color(0, 0, 0)
    for k, label, rgb, w in row.badges:
        text(row.text[pos:k])
        pdf.set_font(badge[0], badge[1], badge[2])
        bh = badge[3]
        by = y + (h - bh) / 2
        pdf.set_fill_color(*rgb)
        pdf.set_text_color(255, 255, 255)
        pdf.rect(x, by, w - 1, bh, style='F')
        pdf.set_xy(x, by)
        pdf.cell(w - 1, bh, label, align='C')
        x += w
        pos = k
        pdf.set_font(*font)
        pdf.set_text_color(0, 0, 0)
    text(row.text[pos:])


def _group_extent(row: _Row, member_ids: set[int], pad: float) -> tuple[float, float]:
    """A group's horizontal edges on one row: its member cues' span, or the full text
    width where the row has none (a line inside the group's run)."""
    spans = [
        (x, x + max(row.cue_w.get(cue_id, 0.0), GROUP_CHIP_W))
        for cue_id, x in row.cue_x.items() if cue_id in member_ids
    ]
    if spans:
        left = min(a for a, _ in spans)
        right = max(b for _, b in spans)
    else:
        left, right = row.cell_x, row.cell_x + row.cell_w
    return left - pad, right + pad


def _minus(a0: float, a1: float, b0: float, b1: float) -> list[tuple[float, float]]:
    """The parts of [a0, a1] that lie outside [b0, b1]."""
    if b1 <= a0 or b0 >= a1:
        return [(a0, a1)]
    pieces: list[tuple[float, float]] = []
    if b0 > a0:
        pieces.append((a0, b0))
    if b1 < a1:
        pieces.append((b1, a1))
    return pieces


def _group_label(g: dict) -> str:
    """The group's number then name, as the viewer shows it."""
    number = ''
    if g.get('number'):
        number = cue_number_label(g['number'], 0, g.get('layer'), _sr_ui._pm)
    return ' '.join(filter(None, [number, g.get('name') or 'Group']))


@dataclass(frozen=True)
class CuePrintOptions:
    """Which cue details to print: badges and names in the margin, notes under the name,
    and preview chips under their script line."""

    markers: bool = True
    names: bool = True
    notes: bool = False
    previews: bool = False


def _collect_previews(cues: list[Cue]) -> dict[int, list[dict]]:
    """Preview chips per cue id, from the ``showrunner_script_previews`` hook.

    Same contract as the script viewer: wrong-shaped answers are skipped.
    """
    pm = _sr_ui._pm
    if pm is None or not cues:
        return {}
    cue_dicts = [
        {
            'id': c.id,
            'number': c.number,
            'point': c.point,
            'name': c.name,
            'layer': c.layer,
            'script_line': c.script_line,
            'script_char': c.script_char,
        }
        for c in cues
    ]
    previews: dict[int, list[dict]] = {}
    for result in hook_results(pm, 'showrunner_script_previews', cues=cue_dicts):
        if not isinstance(result, dict):
            continue
        for cue_id, chips in result.items():
            if not isinstance(chips, list):
                continue
            drawable = [
                chip for chip in chips if isinstance(chip, dict) and isinstance(chip.get('text'), str)
            ]
            if drawable:
                previews.setdefault(cue_id, []).extend(drawable)
    return previews


def _align_flag(align: str) -> str:
    """Convert a layout align value to an fpdf2 align character."""
    return {'left': 'L', 'center': 'C', 'right': 'R', 'justify': 'J'}.get(align, 'L')


def generate_pdf(
    script: Script,
    cues: list[Cue],
    layout: dict[str, Any],
    layer_colors: dict[str, str],
    options: CuePrintOptions | None = None,
    groups: list[dict] | None = None,
) -> bytes:
    """Render *script* with *cues* to PDF bytes using *layout* settings.

    *groups* are ``group_tree`` dicts; each one is outlined on the page segments it spans.
    """

    options = options or CuePrintOptions()
    previews = _collect_previews(cues) if options.previews else {}

    page_cfg = layout.get('page', {})
    pw = page_cfg.get('width', 215.9)
    ph = page_cfg.get('height', 279.4)
    mt = page_cfg.get('margin-top', 25.4)
    mb = page_cfg.get('margin-bottom', 25.4)
    ml = page_cfg.get('margin-left', 38.1)
    mr = page_cfg.get('margin-right', 25.4)

    cue_cfg = layout.get('cues', {})
    cue_font = cue_cfg.get('font', 'Helvetica-Bold')
    cue_size = cue_cfg.get('size', 8)
    cue_margin_right = cue_cfg.get('margin-right', 2)
    cue_space = cue_cfg.get('space-between', 2)

    header_cfg = page_cfg.get('header', {})
    footer_cfg = page_cfg.get('footer', {})

    # Index cues by script_line for quick lookup.
    cues_by_line: dict[int, list[Cue]] = {}
    for c in cues:
        if c.script_line is not None:
            cues_by_line.setdefault(c.script_line, []).append(c)
    for line_cues in cues_by_line.values():
        line_cues.sort(key=lambda c: c.script_char or 0)

    # Parse the Fountain script.
    parser = FountainParser()
    parser.add_text(script.content or '')
    elements = parser.script.elements
    title = script.title or 'Script'

    # Build PDF.
    pdf = FPDF(unit='mm', format=(pw, ph))
    pdf.set_auto_page_break(auto=True, margin=mb)
    pdf.set_margins(ml, mt, mr)

    # Track which original lines have been consumed to place cue annotations.
    # Fountain elements don't carry original line numbers, so we map them back
    # by tracking a running line counter over the original content.
    content_lines = (script.content or '').split('\n')

    # Build a mapping: element index → list of original 1-based line numbers
    # that fall within that element.  We walk through the original lines and
    # match them greedily to elements.
    elem_lines: list[list[int]] = [[] for _ in elements]
    _assign_lines_to_elements(content_lines, elements, elem_lines)

    now = datetime.now()
    date_str = now.strftime('%Y-%m-%d')
    time_str = now.strftime('%H:%M')

    def _add_page():
        pdf.add_page()
        if header_cfg.get('enabled', False):
            _font, _style = _split_font(header_cfg.get('font', 'Helvetica'))
            pdf.set_font(_font, _style, header_cfg.get('size', 8))
            pdf.set_text_color(160, 160, 160)
            text = (
                header_cfg.get('text', '{title}')
                .replace('{title}', title)
                .replace('{page}', str(pdf.page_no()))
                .replace('{date}', date_str)
                .replace('{time}', time_str)
            )
            text = _latin1(text)
            pdf.set_xy(ml, mt / 2)
            pdf.cell(pw - ml - mr, 5, text, align='L')
        pdf.set_y(pdf.t_margin)  # the header moved the cursor up into its band

    _add_page()

    usable_width = pw - ml - mr
    badge_font = _split_font(cue_font)
    badge_h = cue_size * 0.352778 * 1.3
    name_only = CuePrintOptions(
        markers=False, names=options.names, notes=options.notes, previews=options.previews
    )

    def badge_of(cue: Cue):
        """(label, rgb, slot width) for a cue's inline badge, or None when it has none."""
        if not options.markers:
            return None
        layer = cue.layer or ''
        label = _latin1(f'{layer[:1]}{cue_number_label(cue.number, cue.point, layer, _sr_ui._pm)}')
        if not label:
            return None
        pdf.set_font(badge_font[0], badge_font[1], cue_size)
        # The slot is the badge plus a 1 mm gap; _draw_row prints the badge 1 mm narrower.
        return label, _cue_color(layout, layer, layer_colors), pdf.get_string_width(label) + 4

    # Pass 1: lay out every element's printed rows and where each cue sits on them. A
    # group box is then drawn row by row, from its first to its last member cue on each
    # row, so groups sharing a line sit side by side as they do in the viewer.
    plans: list[_Plan | None] = []
    all_rows: list[_Row] = []
    for idx, elem in enumerate(elements):
        if elem.type in (ElementType.BONEYARD, ElementType.NOTE, ElementType.TITLEENTRY):
            plans.append(None)
            continue
        style = _element_style(layout, elem.type)
        font_name, font_style = _split_font(style['font'])
        cell_x = ml + style['margin-left']
        cell_w = max(usable_width - style['margin-left'] - style['margin-right'], 10)
        text = _element_text(elem, style)
        pdf.set_font(font_name, font_style, style['size'])
        rows = (
            _split_rows(pdf, text, cell_x, cell_w, _align_flag(style['align']))
            if text.strip() else []
        )
        lines = elem_lines[idx]
        _place_cues(
            pdf, rows, _cue_char_offsets(text, lines, cues_by_line, content_lines), badge_of
        )
        all_rows.extend(rows)
        plans.append(_Plan(font_name, font_style, style, cell_x, cell_w, rows, lines))
    for gi, row in enumerate(all_rows):
        row.gidx = gi

    # A group's run is the rows from its first to its last member row (across elements).
    group_depth = group_depths(groups or [])
    deepest = max(group_depth.values(), default=0)
    members = {g['id']: set(g['member_ids']) for g in groups or []}
    order = sorted(groups or [], key=lambda g: group_depth[g['id']])
    first: dict[int, int] = {}
    last: dict[int, int] = {}
    for row in all_rows:
        for g in order:
            if any(cue_id in members[g['id']] for cue_id in row.cue_x):
                first.setdefault(g['id'], row.gidx)
                last[g['id']] = row.gidx

    def _pad(g: dict) -> float:
        """Outer groups lie further out, so each one encloses the groups nested in it."""
        return GROUP_PAD + (deepest - group_depth[g['id']]) * GROUP_STEP

    def _rise(g: dict) -> float:
        """How far a group's top and bottom edges lie beyond its text rows."""
        return GROUP_TOP_GAP + (deepest - group_depth[g['id']]) * GROUP_STEP

    def _rule(g: dict, left: float, right: float, y: float) -> None:
        pdf.set_draw_color(*_colour_rgb(g.get('color')))
        pdf.set_line_width(GROUP_LINE)
        pdf.line(left, y, right, y)

    def _label(g: dict, x: float, y: float) -> float:
        """Draw a group's name at *x* on baseline *y*; returns the next x."""
        rgb = _colour_rgb(g.get('color'))
        name = _latin1(_group_label(g))
        pdf.set_font('Helvetica', '', GROUP_LABEL_SIZE)
        pdf.set_text_color(*rgb)
        pdf.text(x, y, name)  # text() leaves the cursor where it is
        return x + pdf.get_string_width(name) + 2

    # Pass 2: draw. Each row is one line of text; group edges are drawn around it.
    prev_page: int | None = None
    prev_ext: dict[int, tuple[float, float]] = {}
    prev_bottom = 0.0  # bottom of the row above, before any gap for a group's bottom edge
    for idx, elem in enumerate(elements):
        plan = plans[idx]
        if plan is None:
            continue
        if elem.type == ElementType.PAGEBREAK:
            _add_page()
            continue

        style = plan.style
        font_name, font_style, font_size = plan.font_name, plan.font_style, style['size']
        cell_x, cell_w = plan.cell_x, plan.cell_w
        line_h = font_size * 0.352778 * 1.4  # pt→mm × leading

        space_before = style.get('space-before', 0)
        if space_before:
            pdf.set_y(pdf.get_y() + space_before * 0.352778)  # pt → mm
        empty_y = pdf.get_y()
        if not plan.rows:
            # Empty elements (e.g. a blank lyric line) only take up a line of space.
            pdf.set_y(pdf.get_y() + line_h)

        for i, row in enumerate(plan.rows):
            r = row.gidx
            active = [g for g in order if g['id'] in first and first[g['id']] <= r <= last[g['id']]]
            starts = [g for g in active if first[g['id']] == r]
            need = line_h + (GROUP_LABEL_H + max(_rise(g) for g in starts) if starts else 0)
            if pdf.get_y() + need > pdf.page_break_trigger:
                _add_page()
            # A group that continues onto a new page repeats its top edge and name there.
            broke = pdf.page != prev_page
            if broke:
                prev_ext = {}
            tops = active if broke else starts
            top_ids = {g['id'] for g in tops}
            if tops:
                pdf.set_y(pdf.get_y() + GROUP_LABEL_H + max(_rise(g) for g in tops))
            row_top = pdf.get_y()
            bottom = row_top + line_h

            ext: dict[int, tuple[float, float]] = {}
            for g in active:
                gid = g['id']
                rise = _rise(g)
                left, right = _group_extent(row, members[gid], _pad(g))
                ext[gid] = (left, right)
                is_top, is_last = gid in top_ids, last[gid] == r
                pdf.set_draw_color(*_colour_rgb(g.get('color')))
                pdf.set_line_width(GROUP_LINE)
                if is_top:
                    pdf.line(left, row_top - rise, right, row_top - rise)
                elif gid in prev_ext:
                    # A gap above this row (a group ended there) belongs to the groups that
                    # continue, so their sides run down through it.
                    p_left, p_right = prev_ext[gid]
                    if prev_bottom < row_top:
                        pdf.line(p_left, prev_bottom, p_left, row_top)
                        pdf.line(p_right, prev_bottom, p_right, row_top)
                    # Where this row is wider or narrower than the row above, the edge steps.
                    for a, b in _minus(left, right, p_left, p_right) + _minus(
                        p_left, p_right, left, right
                    ):
                        pdf.line(a, row_top, b, row_top)
                if is_last:
                    pdf.line(left, bottom + rise, right, bottom + rise)
                y0 = row_top - rise if is_top else row_top
                y1 = bottom + rise if is_last else bottom
                pdf.line(left, y0, left, y1)
                pdf.line(right, y0, right, y1)

            # Names of groups starting here, left to right; the next name never overlaps.
            label_x = float('-inf')
            for g in sorted(tops, key=lambda g: ext[g['id']][0]):
                label_x = _label(g, max(ext[g['id']][0], label_x), row_top - _rise(g) - 0.8)

            _draw_row(
                pdf, row, row_top, line_h, (font_name, font_style, font_size),
                (badge_font[0], badge_font[1], cue_size, badge_h),
            )
            if row.cues:
                # Names sit in the margin beside their own row, so each one lines up with its cue.
                saved_y = pdf.get_y()
                pdf.set_auto_page_break(auto=False, margin=mb)
                _draw_cue_annotations(
                    pdf, row.cues, row_top, pw, cue_margin_right, cue_font, cue_size,
                    cue_space, layout, layer_colors, name_only,
                )
                pdf.set_auto_page_break(auto=True, margin=mb)
                pdf.set_y(saved_y)

            pdf.set_y(bottom)
            ending = [g for g in active if last[g['id']] == r]
            if ending:
                # The bottom edge hangs below the text; keep the next line clear of it.
                pdf.set_y(bottom + max(_rise(g) for g in ending) + GROUP_TOP_GAP)
            prev_ext, prev_page, prev_bottom = ext, pdf.page, bottom

        # Preview chips sit in the flow, under the element they belong to.
        if previews:
            for ln in plan.lines:
                for c in cues_by_line.get(ln, []):
                    if c.id in previews:
                        _draw_preview_chips(
                            pdf, c, previews[c.id], cell_x, cell_w, layout, layer_colors
                        )
        if not plan.rows:
            # No printed row to anchor on: the cues of these lines go beside the blank space.
            empty_cues = [c for ln in plan.lines for c in cues_by_line.get(ln, [])]
            saved_y = pdf.get_y()
            pdf.set_auto_page_break(auto=False, margin=mb)
            _draw_cue_annotations(
                pdf, empty_cues, empty_y, pw, cue_margin_right, cue_font, cue_size,
                cue_space, layout, layer_colors, options,
            )
            pdf.set_auto_page_break(auto=True, margin=mb)
            pdf.set_y(saved_y)

        # Space after
        space_after = style.get('space-after', 0)
        if space_after:
            pdf.set_y(pdf.get_y() + space_after * 0.352778)


    # Footer on every page (drawn after content).
    if footer_cfg.get('enabled', False):
        _font, _style = _split_font(footer_cfg.get('font', 'Helvetica'))
        footer_size = footer_cfg.get('size', 8)
        footer_text_tmpl = footer_cfg.get('text', 'Page {page}')
        # The footer sits below the bottom margin; without this, each footer cell
        # would start a new page.
        pdf.set_auto_page_break(auto=False, margin=mb)
        for page_num in range(1, pdf.pages_count + 1):
            pdf.page = page_num
            pdf.set_font(_font, _style, footer_size)
            pdf.set_text_color(160, 160, 160)
            text = _latin1(
                footer_text_tmpl.replace('{title}', title).replace('{page}', str(page_num))
            )
            pdf.set_xy(ml, ph - mb + 2)
            pdf.cell(pw - ml - mr, 5, text, align='C')

    return pdf.output()


def _split_font(spec: str) -> tuple[str, str]:
    """Split a font spec like ``Courier-Bold`` into (family, style).

    fpdf2 expects the style as a separate string: ``''``, ``'B'``, ``'I'``,
    or ``'BI'``.
    """
    parts = spec.split('-', 1)
    family = parts[0]
    style = ''
    if len(parts) == 2:
        modifier = parts[1].lower()
        if 'bold' in modifier:
            style += 'B'
        if 'oblique' in modifier or 'italic' in modifier:
            style += 'I'
    return family, style


def _is_text_line(raw: str) -> bool:
    """True for a raw line that belongs to element text (not blank, not a [[note]])."""
    stripped = raw.strip()
    return bool(stripped) and not (stripped.startswith('[[') and stripped.endswith(']]'))


def _line_matches(elem, first: str, raw: str) -> bool:
    """Whether raw line *raw* is where *elem* starts.

    A character must match its whole name line (``MEG`` or ``MEG (V.O.)``), so a name
    inside an action sentence can't match. Other elements match on their first line.
    """
    if elem.type == ElementType.CHARACTER:
        base = raw.strip().lstrip('@').rstrip('^ ').rstrip()  # '^' marks dual dialogue
        return base == first or base.startswith(first + ' (')
    return first in raw


def _assign_lines_to_elements(
    content_lines: list[str],
    elements: list,
    elem_lines: list[list[int]],
) -> None:
    """Best-effort mapping of original content lines to parsed elements.

    Each element is located by searching forward from the last line it used for
    its first text line, then it takes as many non-blank lines as its text has.
    Lines the parser drops (title page, boneyards, notes, page breaks) are simply
    skipped by the search, so they cannot push later elements out of step.
    Elements whose first line cannot be found are left unmapped.
    """
    skip_types = {
        ElementType.BONEYARD,
        ElementType.NOTE,
        ElementType.TITLEENTRY,
        ElementType.PAGEBREAK,
    }
    cursor = 0  # 0-based index into content_lines

    for idx, elem in enumerate(elements):
        if elem.type in skip_types:
            continue
        # Character elements keep their name outside .text.
        text_or_name = elem.text or getattr(elem, 'name', None) or ''
        first = next((t.strip() for t in text_or_name.split('\n') if t.strip()), '')
        if not first:
            continue
        start = next(
            (j for j in range(cursor, len(content_lines)) if _line_matches(elem, first, content_lines[j])),
            None,
        )
        if start is None:
            continue
        # Blank lines and [[notes]] have no counterpart in the element's text. Character
        # elements keep their name outside .text, so they always take one line.
        need = max(1, sum(1 for t in (elem.text or '').split('\n') if t.strip()))
        k = start
        while k < len(content_lines) and len(elem_lines[idx]) < need:
            if _is_text_line(content_lines[k]):
                elem_lines[idx].append(k + 1)
            k += 1
        cursor = k


def _draw_preview_chips(
    pdf: FPDF,
    cue: Cue,
    chips: list[dict],
    x: float,
    width: float,
    layout: dict,
    layer_colors: dict[str, str],
) -> None:
    """Print a cue's preview chips under its script element.

    The cue's label comes first in the cue's colour, then each chip is a box with
    a border in the chip's own colour. Boxes wrap onto a new row within *width*.
    The whole group is drawn on one page, inside an outline in the cue's colour.
    """
    cue_cfg = layout.get('cues', {})
    font_name, font_style = _split_font(cue_cfg.get('name-font', 'Helvetica'))
    size = cue_cfg.get('name-size', 10) - 1
    layer = cue.layer or ''
    label = _latin1(f'{layer[:1]}{cue_number_label(cue.number, cue.point, layer, _sr_ui._pm)}:')

    box_h = size * 0.352778 * 1.9
    gap = 1.0  # between boxes, and between rows
    pad = 1.2  # text inset inside each box
    frame = 1.0  # space between the chips and the outline around the group
    right = x + width

    pdf.set_font(font_name, font_style, size)
    label_w = pdf.get_string_width(label) + 1

    # Lay out every box first, so the group's height is known before anything is drawn.
    placed = []  # (left, row, width, text, RGB border colour)
    row = 0
    cx = x + label_w + gap
    for chip in chips:
        text = _latin1(chip['text'])
        chip_w = pdf.get_string_width(text) + 2 * pad
        if cx > x + label_w + gap and cx + chip_w > right:  # wrap to a new row
            row += 1
            cx = x
        placed.append((cx, row, chip_w, text, _colour_rgb(chip.get('color'))))
        cx += chip_w + gap
    rows = row + 1
    group_h = 2 * frame + rows * box_h + (rows - 1) * gap

    if pdf.get_y() + gap + group_h > pdf.page_break_trigger:
        pdf.add_page()
    outline_top = pdf.get_y() + gap
    row0 = outline_top + frame

    pdf.set_text_color(*_cue_color(layout, layer, layer_colors))
    pdf.set_xy(x, row0)
    pdf.cell(label_w, box_h, label)

    for left, row_idx, chip_w, text, border in placed:
        row_y = row0 + row_idx * (box_h + gap)
        pdf.set_draw_color(*border)
        pdf.set_line_width(0.3)
        pdf.rect(left, row_y, chip_w, box_h, style='D')
        pdf.set_text_color(0, 0, 0)
        pdf.set_xy(left + pad, row_y)
        pdf.cell(chip_w - 2 * pad, box_h, text)

    # Outline around the whole group (label and chips) in the cue's layer colour.
    used_right = max([x + label_w] + [left + chip_w for left, _, chip_w, _, _ in placed])
    pdf.set_draw_color(*_cue_color(layout, layer, layer_colors))
    pdf.set_line_width(0.4)
    pdf.rect(x, outline_top, min(used_right - x + frame, width), group_h, style='D')
    pdf.set_xy(x, outline_top + group_h + gap)


def _draw_cue_annotations(
    pdf: FPDF,
    relevant: list[Cue],
    y_top: float,
    page_width: float,
    margin_right: float,
    font: str,
    size: float,
    space: float,
    layout: dict,
    layer_colors: dict[str, str],
    options: CuePrintOptions,
) -> None:
    """Draw cue annotations in the right margin beside the rows of a cue's line.

    Each cue gets a box: its name and notes (left, notes in italics) and its
    badge (right). *options* decides which parts are drawn.
    """
    if not relevant or not (options.markers or options.names or options.notes):
        return

    cue_cfg = layout.get('cues', {})

    badge_font_name, badge_font_style = _split_font(font)
    badge_h = size * 0.352778 * 1.3

    name_font_spec = cue_cfg.get('name-font', 'Helvetica')
    name_size = cue_cfg.get('name-size', size - 1)
    max_name_w = cue_cfg.get('max-width', 40)
    name_font_name, name_font_style = _split_font(name_font_spec)
    name_line_h = name_size * 0.352778 * 1.3

    border_w = cue_cfg.get('border-width', 0.4)
    pad = cue_cfg.get('padding', 1.5)
    bg_rgb = cue_cfg.get('background', [255, 255, 255])
    bg_opacity = cue_cfg.get('background-opacity', 0.5)

    x = page_width - margin_right
    y = y_top

    for cue in relevant:
        layer = cue.layer or ''
        r, g, b = _cue_color(layout, layer, layer_colors)
        label = (
            _latin1(f'{layer[:1]}{cue_number_label(cue.number, cue.point, layer, _sr_ui._pm)}')
            if options.markers
            else ''
        )

        # Text for the name column: the cue name, then its notes in italics.
        # Each entry is (text, font, style, size, line height, measured height).
        text_lines: list[tuple[str, str, str, float, float, float]] = []
        for enabled, text, f_style in (
            (options.names, cue.name, name_font_style),
            (options.notes, cue.notes, 'I'),
        ):
            if not enabled or not text:
                continue
            text = _latin1(text)
            pdf.set_font(name_font_name, f_style, name_size)
            # multi_cell dry-run measures the wrapped height.
            h = pdf.multi_cell(
                max_name_w, name_line_h, text, align='R', dry_run=True, output='HEIGHT'
            )
            text_lines.append((text, name_font_name, f_style, name_size, name_line_h, h))
        text_h = sum(entry[5] for entry in text_lines)

        if not label and not text_lines:
            continue

        # Measure the badge.
        label_w = 0.0
        if label:
            pdf.set_font(badge_font_name, badge_font_style, size)
            label_w = pdf.get_string_width(label) + 3

        content_h = max(badge_h if label else 0.0, text_h)
        # Total box width = name column + gap + badge.
        name_col_w = (max_name_w + 1) if text_lines else 0
        box_w = name_col_w + label_w + 2 * pad
        box_h = content_h + 2 * pad
        box_x = x - box_w
        # Keep the box on this page; a box that would run off the bottom moves up.
        box_y = min(y, pdf.page_break_trigger - box_h)

        # Draw background with opacity (blend bg_rgb toward white).
        blended = (
            int(bg_rgb[0] * bg_opacity + 255 * (1 - bg_opacity)),
            int(bg_rgb[1] * bg_opacity + 255 * (1 - bg_opacity)),
            int(bg_rgb[2] * bg_opacity + 255 * (1 - bg_opacity)),
        )
        pdf.set_fill_color(*blended)
        pdf.rect(box_x, box_y, box_w, box_h, style='F')

        # Draw coloured border.
        if border_w > 0:
            pdf.set_draw_color(r, g, b)
            pdf.set_line_width(border_w)
            pdf.rect(box_x, box_y, box_w, box_h, style='D')

        # Draw badge inside the box (right-aligned).
        text_right = x - pad
        if label:
            bx = x - pad - label_w
            by = box_y + pad
            pdf.set_fill_color(r, g, b)
            pdf.set_text_color(255, 255, 255)
            pdf.rect(bx, by, label_w, badge_h, style='F')
            pdf.set_xy(bx, by)
            pdf.cell(label_w, badge_h, label, align='C')
            text_right = bx - 1

        # Draw the name and notes stacked to the left of the badge (or the box edge).
        ty = box_y + pad
        for text, f_name, f_style, f_size, f_line_h, h in text_lines:
            pdf.set_font(f_name, f_style, f_size)
            pdf.set_text_color(r, g, b)
            pdf.set_xy(text_right - max_name_w, ty)
            pdf.multi_cell(max_name_w, f_line_h, text, align='R')
            ty += h

        y = box_y + box_h + space


    # Restore defaults.
    pdf.set_text_color(0, 0, 0)
    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.2)


# ---------------------------------------------------------------------------
# API route
# ---------------------------------------------------------------------------


@router.get('/script/{script_id}/pdf')
async def export_script_pdf(
    script_id: int,
    cue_list_id: int | None = None,
    layout_path: str | None = None,
    markers: bool = True,
    names: bool = True,
    notes: bool = False,
    previews: bool = False,
):
    """Export a script with cue annotations as a PDF.

    Parameters
    ----------
    script_id:
        Database ID of the script to render.
    cue_list_id:
        ID of the cue list whose cues should be annotated. When ``None``,
        the first cue list for the script's show is used.
    layout_path:
        Optional filesystem path to a TOML layout override. Falls back to
        the built-in ``pdf_layout.toml``.
    markers, names, notes, previews:
        Which cue details to print: the cue-number badge, the cue name,
        the cue notes, and the plugin preview chips under their script line.
    """
    db = get_db()

    with db.session() as s:
        script = s.get(Script, script_id)
        if script is None:
            raise HTTPException(status_code=404, detail='Script not found')

        if cue_list_id is None:
            cl = s.exec(
                select(CueList).where(CueList.show_id == script.show_id)
            ).first()
            if cl is None:
                raise HTTPException(
                    status_code=404, detail='No cue list found for this show'
                )
            cue_list_id = cl.id
        else:
            cl = s.get(CueList, cue_list_id)
            if cl is None:
                raise HTTPException(status_code=404, detail='Cue list not found')

        cues = s.exec(
            select(Cue)
            .where(Cue.cue_list_id == cue_list_id)
            .order_by(*CUE_ORDER)
        ).all()
        layer_colors = {r['name']: r['color'] for r in get_layers(s, script.show_id)}
        groups = group_tree(s, cue_list_id)

        # Detach data before closing the session.
        script_title = script.title or 'Script'
        script_content = script.content
        script_obj = Script(
            id=script.id,
            show_id=script.show_id,
            title=script_title,
            format=script.format,
            content=script_content,
        )
        cue_list: list[Cue] = [
            Cue(
                id=c.id,
                cue_list_id=c.cue_list_id,
                number=c.number,
                point=c.point,
                name=c.name,
                layer=c.layer,
                cue_type=c.cue_type,
                notes=c.notes,
                color=c.color,
                sequence=c.sequence,
                script_line=c.script_line,
                script_char=c.script_char,
            )
            for c in cues
        ]

    layout = _load_layout(layout_path)
    options = CuePrintOptions(markers=markers, names=names, notes=notes, previews=previews)
    pdf_bytes = generate_pdf(script_obj, cue_list, layout, layer_colors, options, groups)

    timestamp = datetime.now().strftime('%Y-%m-%d_%H%M')
    filename = f'{script_title} {timestamp}.pdf'
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------


class ShowPrinterPlugin:
    """PDF export of scripts with cue annotations."""

    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            'name': 'ShowPrinter',
            'description': 'PDF export of annotated scripts',
            'version': '0.1.0',
        }

    @showrunner.hookimpl
    def showrunner_startup(self, app):
        pass

    @showrunner.hookimpl
    def showrunner_shutdown(self, app):
        pass

    @showrunner.hookimpl
    def showrunner_get_routes(self):
        return router

    @showrunner.hookimpl
    def showrunner_get_commands(self):
        return []

    @showrunner.hookimpl
    def showrunner_get_nav(self):
        return None

    @showrunner.hookimpl
    def showrunner_get_status(self):
        return None
