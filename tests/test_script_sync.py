"""Cues follow their script text when the fountain script is edited."""

from sqlmodel import select

from showrunner.models import Cue, CueList, Script, Show
from showrunner.scriptformat import map_script_lines

OLD = 'ACT ONE\n\nINT. HALL - NIGHT\n\nHAMLET\nTo be.\n\nEXIT'


def test_map_identity():
    assert map_script_lines(OLD, OLD) == list(range(OLD.count('\n') + 1))


def test_map_insert_above_shifts_down():
    new = 'PROLOGUE\n' + OLD
    assert map_script_lines(OLD, new) == [i + 1 for i in range(len(OLD.split('\n')))]


def test_map_delete_line_above_shifts_up():
    new = OLD.replace('ACT ONE\n', '')
    assert map_script_lines(OLD, new)[2] == 1  # 'INT. HALL' was line 2, now line 1


def test_map_deleted_line_goes_to_following_line():
    new = OLD.replace('INT. HALL - NIGHT\n', '')
    assert map_script_lines(OLD, new)[2] == 2  # the blank line that followed now sits at 2


def test_map_edited_line_keeps_its_line():
    new = OLD.replace('To be.', 'To be or not.')
    assert map_script_lines(OLD, new)[5] == 5


def test_map_line_ending_change_is_not_an_edit():
    assert map_script_lines(OLD, OLD.replace('\n', '\r\n')) == list(range(OLD.count('\n') + 1))


def test_map_removed_tail_clamps_to_last_line():
    new = 'ACT ONE'
    assert map_script_lines(OLD, new) == [0] * len(OLD.split('\n'))


def _seed(db, content):
    with db.session() as s:
        show = Show(name='S')
        s.add(show)
        s.commit()
        cl = CueList(show_id=show.id, name='Main')
        s.add(cl)
        s.commit()
        script = Script(show_id=show.id, title='T', content=content)
        s.add(script)
        s.commit()
        s.add(Cue(cue_list_id=cl.id, name='hall', script_line=2, script_char=4))
        s.add(Cue(cue_list_id=cl.id, name='exit', script_line=7, script_char=0))
        s.add(Cue(cue_list_id=cl.id, name='loose', script_line=None))
        s.commit()
        return script.id


def _positions(db):
    with db.session() as s:
        return {c.name: (c.script_line, c.script_char) for c in s.exec(select(Cue))}


def test_editing_script_moves_cues_with_text(db):
    script_id = _seed(db, OLD)
    with db.session() as s:
        script = s.get(Script, script_id)
        script.content = 'PROLOGUE\n' + OLD
        s.commit()
    assert _positions(db) == {'hall': (3, 4), 'exit': (8, 0), 'loose': (None, None)}


def test_unchanged_script_leaves_cues_alone(db):
    script_id = _seed(db, OLD)
    with db.session() as s:
        script = s.get(Script, script_id)
        script.title = 'Renamed'
        s.commit()
    assert _positions(db) == {'hall': (2, 4), 'exit': (7, 0), 'loose': (None, None)}
