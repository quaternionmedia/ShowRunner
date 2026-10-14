"""Script viewer preview helpers."""

from showrunner.plugins.scripter import _carried_cue, _get_previews
from showrunner import ui as sr_ui


def cue(id, line, char=0):
    return {'id': id, 'script_line': line, 'script_char': char}


def test_carried_cue_is_last_preview_cue_before_page():
    cues = [cue(1, 5), cue(2, 20, 3), cue(3, 20, 9), cue(4, 40), cue(5, None), cue(6, 12)]
    previews = {1: [], 2: [], 3: [], 4: [], 5: []}  # 6 has no preview
    assert _carried_cue(cues, previews, 30)['id'] == 3
    assert _carried_cue(cues, previews, 5) is None  # line 5 itself is on the page, not before it


def test_get_previews_merges_plugins_and_survives_errors(monkeypatch):
    class Hook:
        def __init__(self, results):
            self.results = results

        def showrunner_script_previews(self, cues):
            if isinstance(self.results, Exception):
                raise self.results
            return self.results

    class PM:
        def __init__(self, results):
            self.hook = Hook(results)

    monkeypatch.setattr(sr_ui, '_pm', PM([{1: [{'text': 'a'}]}, None, {2: [{'text': 'b'}]}]))
    assert set(_get_previews([cue(1, 1)])) == {1, 2}
    monkeypatch.setattr(sr_ui, '_pm', PM(RuntimeError('boom')))
    assert _get_previews([cue(1, 1)]) == {}
