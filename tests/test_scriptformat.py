from showrunner.scriptformat import block_css, classify_lines, load_styles, outline

SCRIPT = """# ACT ONE

INT. THEATER - NIGHT

PHANTOMS
(Enter from audience)
Welcome, we're glad
you could come.
[[Page 2]]
More dialogue.

USHERETTE enters with tray.
/* cut
this */

CUT TO:

- NOT A NAME
Plain.
"""


def test_classify_blocks():
    lines = SCRIPT.split('\n')
    kinds = dict(zip(lines, classify_lines(lines)))
    assert kinds['# ACT ONE'] == 'section'
    assert kinds['INT. THEATER - NIGHT'] == 'heading'
    assert kinds['PHANTOMS'] == 'character'
    assert kinds['(Enter from audience)'] == 'parenthetical'
    assert kinds['you could come.'] == 'dialogue'
    assert kinds['More dialogue.'] == 'dialogue'  # a page marker does not end the speech
    assert kinds['USHERETTE enters with tray.'] == 'action'
    assert kinds['this */'] == 'comment' and kinds['/* cut'] == 'comment'
    assert kinds['CUT TO:'] == 'transition'
    assert kinds['- NOT A NAME'] == 'action'


def test_style_overrides_are_merged_and_unsafe_values_ignored():
    styles = load_styles(
        {
            'character': {'indent': '30%', 'bold': False, 'color': 'red;}', 'nope': 1},
            'bogus': {'indent': '1px'},
        }
    )
    css = block_css(styles['character'])
    assert 'margin-left: 30%' in css and 'text-transform: uppercase' in css
    assert 'font-weight' not in css and 'red' not in css
    assert 'bogus' not in styles


def test_outline_lists_sections_and_scene_headings():
    lines = ['# ACT ONE', '', 'INT. THEATER - NIGHT', '', 'PHANTOMS', 'Hi.', '', '## Scene 2',
             '', '.FLASHBACK', '', 'EXT. STREET - DAY [[Page 3]]']
    items = outline(lines, classify_lines(lines))
    assert items == [
        {'line': 1, 'level': 1, 'text': 'ACT ONE'},
        {'line': 3, 'level': 2, 'text': 'INT. THEATER - NIGHT'},
        {'line': 8, 'level': 2, 'text': 'Scene 2'},
        {'line': 10, 'level': 3, 'text': 'FLASHBACK'},
        {'line': 12, 'level': 3, 'text': 'EXT. STREET - DAY'},
    ]
