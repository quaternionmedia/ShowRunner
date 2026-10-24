"""Script viewer preview helpers."""

import pluggy

import showrunner
from showrunner import ui as sr_ui
from showrunner.hookspecs import ShowRunnerSpec
from showrunner.plugins.scripter import _carried_cue, _get_previews


def cue(id, line, char=0):
    return {'id': id, 'script_line': line, 'script_char': char}


def test_carried_cue_is_last_preview_cue_before_page():
    cues = [cue(1, 5), cue(2, 20, 3), cue(3, 20, 9), cue(4, 40), cue(5, None), cue(6, 12)]
    previews = {1: [], 2: [], 3: [], 4: [], 5: []}  # 6 has no preview
    assert _carried_cue(cues, previews, 30)['id'] == 3
    assert _carried_cue(cues, previews, 5) is None  # line 5 itself is on the page, not before it


def test_get_previews_merges_plugins_and_survives_errors(monkeypatch):
    class Good:
        @showrunner.hookimpl
        def showrunner_script_previews(self, cues):
            return {1: [{'text': 'a'}]}

    class Broken:
        @showrunner.hookimpl
        def showrunner_script_previews(self, cues):
            raise RuntimeError('boom')

    class Other:
        @showrunner.hookimpl
        def showrunner_script_previews(self, cues):
            return {1: [{'text': 'b'}], 2: [{'text': 'c'}]}

    pm = pluggy.PluginManager('showrunner')
    pm.add_hookspecs(ShowRunnerSpec)
    for plugin in (Good(), Broken(), Other()):
        pm.register(plugin)
    monkeypatch.setattr(sr_ui, '_pm', pm)

    previews = _get_previews([cue(1, 1), cue(2, 2)])
    assert [c['text'] for c in previews[1]] == ['a', 'b']  # same cue from two plugins: merged
    assert [c['text'] for c in previews[2]] == ['c']  # broken plugin skipped, others still run
    assert _get_previews([]) == {}


def test_load_cues_scopes_to_one_list_or_all_and_survives_deleted_list(db, monkeypatch):
    import showrunner.plugins.db as db_module
    from showrunner.models import Cue, CueList, Show
    from showrunner.plugins.scripter import _load_cues

    monkeypatch.setattr(db_module, '_db', db)
    with db.session() as s:
        s.add(Show(name='Hamlet'))
        s.commit()
        s.add(CueList(show_id=1, name='Main'))
        s.add(CueList(show_id=1, name='TheatreMix'))
        s.commit()
        s.add(Cue(cue_list_id=1, number=1, name='a', layer='Lights'))
        s.add(Cue(cue_list_id=2, number=2, name='b', layer='Mix'))
        s.commit()

    assert _load_cues(None) == []
    assert _load_cues(999) == []  # list deleted after it was chosen: no crash
    assert [c['name'] for c in _load_cues(1)] == ['a']
    assert [c['name'] for c in _load_cues(1, all_lists=True)] == ['a', 'b']


def test_preview_chip_colour_must_be_a_plain_colour_name():
    from showrunner.layers import is_color

    assert is_color('amber') and is_color('deep-purple')
    assert not is_color('red" onmouseover="x') and not is_color(None)


def test_get_previews_drops_a_plugin_s_malformed_answer_and_keeps_the_others(monkeypatch):
    """A plugin that returns the wrong shape loses its own chips, never the page."""

    class Good:
        @showrunner.hookimpl
        def showrunner_script_previews(self, cues):
            return {1: [{'text': 'a', 'color': 'teal'}]}

    class ReturnsList:
        @showrunner.hookimpl
        def showrunner_script_previews(self, cues):
            return [{'text': 'x'}]

    class ChipsNotAList:
        @showrunner.hookimpl
        def showrunner_script_previews(self, cues):
            return {1: 'x'}

    class ChipWithoutText:
        @showrunner.hookimpl
        def showrunner_script_previews(self, cues):
            return {1: [{'color': 'red'}, {'text': 'kept'}, 'not a chip']}

    pm = pluggy.PluginManager('showrunner')
    pm.add_hookspecs(ShowRunnerSpec)
    for plugin in (Good(), ReturnsList(), ChipsNotAList(), ChipWithoutText()):
        pm.register(plugin)
    monkeypatch.setattr(sr_ui, '_pm', pm)

    previews = _get_previews([cue(1, 1)])
    assert [c['text'] for c in previews[1]] == ['a', 'kept']


def test_load_groups_and_depths_for_nested_groups(db, monkeypatch):
    import showrunner.plugins.db as db_module
    from showrunner.groups import create_group
    from showrunner.models import Cue, CueList, Show
    from showrunner.groups import group_depths
    from showrunner.plugins.scripter import _load_cues, _load_groups

    monkeypatch.setattr(db_module, '_db', db)
    with db.session() as s:
        s.add(Show(name='Hamlet'))
        s.commit()
        s.add(CueList(show_id=1, name='Main'))
        s.commit()
        s.add_all([Cue(cue_list_id=1, number=i, layer='Lights') for i in (1, 2)])
        s.commit()
        outer = create_group(s, 1, [1, 2], name='outer')
        s.flush()
        create_group(s, 1, [1], name='inner', parent_id=outer.id)
        s.commit()

    groups = _load_groups(1)
    depths = group_depths(groups)
    by_name = {g['name']: g for g in groups}
    assert depths[by_name['outer']['id']] == 0
    assert depths[by_name['inner']['id']] == 1
    assert sorted(by_name['outer']['member_ids']) == [1, 2]
    assert _load_cues(1)[0]['group_id'] is not None
