"""ShowPrinter - PDF export of scripts with cue annotations.

Provides a FastAPI route at ``/export/script/{script_id}/pdf`` that renders a
Fountain script to PDF with cue markers drawn in the margin beside their
associated lines.

Layout is driven by a TOML template (``pdf_layout.toml``) that ships alongside
this module and can be overridden via ``[plugins.showprinter]`` settings in
``show.toml``.
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import tomllib
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from fpdf import FPDF
from screenplay_tools.fountain.parser import Parser as FountainParser
from screenplay_tools.screenplay import ElementType
from sqlmodel import select

import showrunner
from showrunner.models import Cue, CueList, Script
from showrunner.plugins.db import get_db

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
        # Treat override as a filename only (no directory components).
        # This prevents traversal/absolute path usage from untrusted input.
        name = Path(override_path).name
        if name and name not in {'.', '..'} and name.endswith('.toml'):
            candidate = safe_root / name
            try:
                resolved = candidate.resolve(strict=False)
                resolved.relative_to(safe_root)
                path = resolved
            except ValueError:
                path = _DEFAULT_LAYOUT

    if not path.is_file():
        path = _DEFAULT_LAYOUT
    with open(path, 'rb') as f:
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


def _cue_color(layout: dict, layer: str) -> tuple[int, int, int]:
    """Return (R, G, B) for a cue layer from the layout config."""
    colors = layout.get('cues', {}).get('colors', {})
    rgb = colors.get(layer, [128, 128, 128])
    return (int(rgb[0]), int(rgb[1]), int(rgb[2]))


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------


def _align_flag(align: str) -> str:
    """Convert a layout align value to an fpdf2 align character."""
    return {'left': 'L', 'center': 'C', 'right': 'R', 'justify': 'J'}.get(align, 'L')


def generate_pdf(
    script: Script,
    cues: list[Cue],
    layout: dict[str, Any],
) -> bytes:
    """Render *script* with *cues* to PDF bytes using *layout* settings."""

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
            pdf.set_xy(ml, mt / 2)
            pdf.cell(pw - ml - mr, 5, text, align='L')

    _add_page()

    usable_width = pw - ml - mr

    for idx, elem in enumerate(elements):
        if elem.type in (
            ElementType.BONEYARD,
            ElementType.NOTE,
            ElementType.TITLEENTRY,
        ):
            continue
        if elem.type == ElementType.PAGEBREAK:
            _add_page()
            continue

        style = _element_style(layout, elem.type)
        font_name, font_style = _split_font(style['font'])
        font_size = style['size']
        elem_ml = style['margin-left']
        elem_mr = style['margin-right']

        text = elem.text or ''
        # Character elements store the name separately from .text.
        if elem.type == ElementType.CHARACTER:
            text = elem.name or ''
            if hasattr(elem, 'extension') and elem.extension:
                text += f' ({elem.extension})'
        # The Fountain parser strips parentheses from parentheticals.
        elif elem.type == ElementType.PARENTHETICAL:
            text = f'({text})'
        if style.get('uppercase', False):
            text = text.upper()

        # Space before
        space_before = style.get('space-before', 0)
        if space_before:
            pdf.set_y(pdf.get_y() + space_before * 0.352778)  # pt → mm

        pdf.set_font(font_name, font_style, font_size)
        pdf.set_text_color(0, 0, 0)

        cell_x = ml + elem_ml
        cell_w = usable_width - elem_ml - elem_mr
        if cell_w < 10:
            cell_w = 10

        y_before = pdf.get_y()
        pdf.set_x(cell_x)
        line_h = font_size * 0.352778 * 1.4  # pt→mm × leading
        pdf.multi_cell(cell_w, line_h, text, align=_align_flag(style['align']))
        y_after = pdf.get_y()

        # Draw cue annotations for original lines covered by this element.
        # Save/restore Y so margin annotations don't shift the text cursor.
        lines_for_elem = elem_lines[idx] if idx < len(elem_lines) else []
        saved_y = pdf.get_y()
        _draw_cue_annotations(
            pdf,
            lines_for_elem,
            cues_by_line,
            y_before,
            y_after,
            pw,
            cue_margin_right,
            cue_font,
            cue_size,
            cue_space,
            layout,
        )
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
        for page_num in range(1, pdf.pages_count + 1):
            pdf.page = page_num
            pdf.set_font(_font, _style, footer_size)
            pdf.set_text_color(160, 160, 160)
            text = footer_text_tmpl.replace('{title}', title).replace(
                '{page}', str(page_num)
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


def _assign_lines_to_elements(
    content_lines: list[str],
    elements: list,
    elem_lines: list[list[int]],
) -> None:
    """Best-effort mapping of original content lines to parsed elements.

    Walks through the raw lines and assigns each non-blank line to the next
    element whose text hasn't been fully covered.  This is heuristic — it
    works well for standard Fountain scripts but edge cases (e.g., boneyards
    spanning many lines) may slip through.
    """
    elem_idx = 0
    skip_types = {
        ElementType.BONEYARD,
        ElementType.NOTE,
        ElementType.TITLEENTRY,
        ElementType.PAGEBREAK,
    }

    for line_no, raw in enumerate(content_lines, start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        # Advance past elements we want to skip.
        while elem_idx < len(elements) and elements[elem_idx].type in skip_types:
            elem_idx += 1
        if elem_idx >= len(elements):
            break
        elem_lines[elem_idx].append(line_no)
        # Heuristic: once we've accumulated enough lines, move forward.
        elem_text_lines = len((elements[elem_idx].text or '').split('\n'))
        if len(elem_lines[elem_idx]) >= elem_text_lines:
            elem_idx += 1


def _draw_cue_annotations(
    pdf: FPDF,
    lines_for_elem: list[int],
    cues_by_line: dict[int, list[Cue]],
    y_top: float,
    y_bottom: float,
    page_width: float,
    margin_right: float,
    font: str,
    size: float,
    space: float,
    layout: dict,
) -> None:
    """Draw cue badge annotations in the right margin beside an element."""
    relevant: list[Cue] = []
    for ln in lines_for_elem:
        relevant.extend(cues_by_line.get(ln, []))
    if not relevant:
        return

    cue_cfg = layout.get('cues', {})

    badge_font_name, badge_font_style = _split_font(font)
    pdf.set_font(badge_font_name, badge_font_style, size)
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
        layer = cue.layer or 'Lights'
        r, g, b = _cue_color(layout, layer)
        label = f'{layer[0]}{cue.number}'

        # Measure the badge.
        pdf.set_font(badge_font_name, badge_font_style, size)
        label_w = pdf.get_string_width(label) + 3

        # Measure the cue name height so we can size the border box.
        name_text = cue.name or ''
        name_h = 0.0
        if name_text:
            pdf.set_font(name_font_name, name_font_style, name_size)
            # Use multi_cell dry-run to measure wrapped height.
            name_h = pdf.multi_cell(
                max_name_w,
                name_line_h,
                name_text,
                align='R',
                dry_run=True,
                output='HEIGHT',
            )

        content_h = max(badge_h, name_h)
        # Total box width = name column + gap + badge.
        name_col_w = (max_name_w + 1) if name_text else 0
        box_w = name_col_w + label_w + 2 * pad
        box_h = content_h + 2 * pad
        box_x = x - box_w
        box_y = y

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
        bx = x - pad - label_w
        by = box_y + pad
        pdf.set_fill_color(r, g, b)
        pdf.set_text_color(255, 255, 255)
        pdf.rect(bx, by, label_w, badge_h, style='F')
        pdf.set_xy(bx, by)
        pdf.cell(label_w, badge_h, label, align='C')

        # Draw cue name to the left of the badge.
        if name_text:
            pdf.set_font(name_font_name, name_font_style, name_size)
            pdf.set_text_color(r, g, b)
            name_x = bx - max_name_w - 1
            pdf.set_xy(name_x, by)
            pdf.multi_cell(max_name_w, name_line_h, name_text, align='R')

        y = box_y + box_h + space

        if y > y_bottom:
            break

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
            .order_by(Cue.number, Cue.point)
        ).all()

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
    pdf_bytes = generate_pdf(script_obj, cue_list, layout)

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
