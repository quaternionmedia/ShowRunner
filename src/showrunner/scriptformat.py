"""Block classification and styling for the script viewer.

``classify_lines`` labels every script line with a block type (Fountain rules);
``load_styles`` merges user overrides from ``[plugins.showscripter.format.<block>]``
into the defaults; ``block_css`` turns one style into an inline CSS string.
"""

import re

from loguru import logger

BLOCK_TYPES = (
    'heading',
    'action',
    'character',
    'parenthetical',
    'dialogue',
    'lyric',
    'transition',
    'section',
    'synopsis',
    'comment',
)

# indent -> margin-left, width -> max-width; every value is a CSS length/colour string.
DEFAULT_STYLES: dict[str, dict] = {
    'heading': {'bold': True, 'uppercase': True, 'color': '#ffd54f'},
    'action': {},
    'character': {'bold': True, 'uppercase': True, 'indent': '22%'},
    'parenthetical': {'italic': True, 'indent': '17%', 'color': '#bdbdbd'},
    'dialogue': {'indent': '10%', 'width': '65%'},
    'lyric': {'italic': True, 'indent': '10%', 'width': '65%', 'color': '#81d4fa'},
    'transition': {'uppercase': True, 'align': 'right'},
    'section': {'bold': True, 'size': '1.1rem'},
    'synopsis': {'italic': True, 'color': '#9e9e9e'},
    'comment': {'italic': True, 'color': '#9e9e9e', 'background': 'rgba(255,255,255,0.05)'},
}
DEFAULT_LINE_HEIGHT = '2'

_CSS_PROPS = {
    'indent': 'margin-left',
    'width': 'max-width',
    'align': 'text-align',
    'size': 'font-size',
    'color': 'color',
    'background': 'background',
    'font': 'font-family',
}
_FLAGS = {
    'bold': 'font-weight: bold',
    'italic': 'font-style: italic',
    'uppercase': 'text-transform: uppercase',
}
_SAFE_VALUE = re.compile(r"[\w\s.,%#()+\-/']+")  # config text lands in a style attribute

_PAGE_MARKER = re.compile(r'\[\[Page\s+(.+?)\]\]')
_HEADING = re.compile(r'^(INT|EXT|EST|INT\.?/EXT|I/E)[. ]', re.IGNORECASE)


def classify_lines(lines: list[str]) -> list[str]:
    """Return one block type per line. Blank lines are typed ``action``."""
    types = ['action'] * len(lines)
    boneyard = dialogue = False
    prev_blank = True

    def blank(i: int) -> bool:
        return i >= len(lines) or not _PAGE_MARKER.sub('', lines[i]).strip()

    for i, raw in enumerate(lines):
        text = _PAGE_MARKER.sub('', raw).strip()
        if not text:
            if _PAGE_MARKER.search(raw):
                continue  # a page marker alone must not break a dialogue block
            dialogue, prev_blank = False, True
            continue
        was_blank, prev_blank = prev_blank, False
        if boneyard or '/*' in text:
            types[i] = 'comment'
            boneyard = '*/' not in (text if boneyard else text.split('/*', 1)[1])
            dialogue = False
        elif text.startswith('[[') and text.endswith(']]'):
            types[i] = 'comment'
        elif dialogue:
            types[i] = 'parenthetical' if text[0] == '(' and text[-1] == ')' else 'dialogue'
            if text[0] == '~':
                types[i] = 'lyric'
        elif text[0] == '#':
            types[i] = 'section'
        elif text[0] == '=' and not text.startswith('==='):
            types[i] = 'synopsis'
        elif text[0] == '~':
            types[i] = 'lyric'
        elif text[0] != '!' and (_HEADING.match(text) or re.match(r'\.[^.]', text)):
            types[i] = 'heading'
        elif (text[0] == '>' and text[-1] != '<') or (was_blank and re.search(r'[A-Z ]TO:$', text)):
            types[i] = 'transition'
        elif was_blank and not blank(i + 1) and _is_character(text):
            types[i] = 'character'
            dialogue = True
    return types


def _is_character(text: str) -> bool:
    if text[0] == '@':
        return True
    name = re.sub(r'\(.*?\)', '', text).strip()  # drop extensions such as (V.O.)
    return text[0].isalpha() and name == name.upper() and any(ch.isalpha() for ch in name)


def load_styles(overrides: dict | None = None) -> dict[str, dict]:
    """Defaults merged with user overrides; unknown blocks/keys and unsafe values are skipped."""
    styles = {name: dict(style) for name, style in DEFAULT_STYLES.items()}
    for block, custom in (overrides or {}).items():
        if block not in styles or not isinstance(custom, dict):
            logger.warning('showscripter format: unknown block {!r}, ignored', block)
            continue
        for key, value in custom.items():
            if key not in _CSS_PROPS and key not in _FLAGS:
                logger.warning('showscripter format.{}: unknown key {!r}, ignored', block, key)
            elif key in _FLAGS and isinstance(value, bool):
                styles[block][key] = value
            elif key in _CSS_PROPS and isinstance(value, str) and _SAFE_VALUE.fullmatch(value):
                styles[block][key] = value
            else:
                logger.warning('showscripter format.{}.{}: bad value {!r}', block, key, value)
    return styles


def block_css(style: dict) -> str:
    """Inline CSS for one block style."""
    parts = [f'{prop}: {style[key]}' for key, prop in _CSS_PROPS.items() if key in style]
    parts += [css for key, css in _FLAGS.items() if style.get(key)]
    return '; '.join(parts)
