"""`sr serve` and `sr check`: the performing family's demo contract.

Hermetic. `serve` is stopped short of binding anything, and `check` is shown
refusing to call a missing server a failure.
"""

import tomllib
from pathlib import Path

from typer.testing import CliRunner

from showrunner import cli as sr

runner = CliRunner()


def test_serve_puts_the_database_log_and_config_under_state(tmp_path, monkeypatch):
    started = {}
    monkeypatch.setattr(sr, 'start', lambda **kwargs: started.update(kwargs))
    monkeypatch.chdir(tmp_path)
    state = tmp_path / 'state'

    result = runner.invoke(
        sr.cli, ['serve', '--host', '127.0.0.1', '--port', '18400', '--state', str(state)]
    )

    assert result.exit_code == 0, result.output
    config = tomllib.loads((state / 'show.toml').read_text(encoding='utf-8'))
    assert config['server'] == {'host': '127.0.0.1', 'port': 18400}
    assert Path(config['database']['path']) == state.resolve() / 'show.db'
    assert Path(config['plugins']['showlogger']['file']) == state.resolve() / 'show.log'
    assert started == {
        'host': '127.0.0.1', 'port': 18400, 'config': state.resolve() / 'show.toml', 'reload': False
    }
    assert Path.cwd() == state.resolve()


def test_check_with_nothing_answering_cannot_say_and_does_not_fail(tmp_path):
    # Port 9 is discard: nothing serves HTTP there, and nothing is bound here.
    result = runner.invoke(
        sr.cli, ['check', '--url', 'http://127.0.0.1:9/', '--shots', str(tmp_path / 'shots')]
    )

    assert result.exit_code == sr.CANNOT_SAY == 3
    assert 'nothing answers at http://127.0.0.1:9/' in result.output
    assert not (tmp_path / 'shots').exists()


def test_check_with_a_broken_pytest_cannot_say_and_does_not_fail(tmp_path, monkeypatch):
    # Python exits 1 for a module it cannot run: a failed assertion's status.
    monkeypatch.setattr(sr, '_answers', lambda url: True)
    broken = sr.subprocess.CompletedProcess([], 1, '', "No module named pytest.__main__")
    monkeypatch.setattr(sr.subprocess, 'run', lambda *args, **kwargs: broken)

    result = runner.invoke(
        sr.cli, ['check', '--url', 'http://127.0.0.1:18400/', '--shots', str(tmp_path / 'shots')]
    )

    assert result.exit_code == sr.CANNOT_SAY
    assert 'pytest does not run here' in result.output


def test_serve_writes_the_family_table_from_its_flags(tmp_path, monkeypatch):
    from showrunner.config import load_config
    from showrunner.plugins.family import build, settings_of

    monkeypatch.setattr(sr, 'start', lambda **kwargs: None)
    monkeypatch.chdir(tmp_path)
    state = tmp_path / 'state'

    result = runner.invoke(sr.cli, [
        'serve', '--port', '18400', '--state', str(state),
        '--family', 'showstopper=http://127.0.0.1:18411',
        '--family', 'joe=http://127.0.0.1:18431', '--family-pull', 'leo',
    ])

    assert result.exit_code == 0, result.output
    config = tomllib.loads((state / 'show.toml').read_text(encoding='utf-8'))
    assert config['plugins']['family'] == {
        'pull': ['leo'],
        'members': {'showstopper': 'http://127.0.0.1:18411', 'joe': 'http://127.0.0.1:18431'},
    }
    family = build(settings_of(load_config(state / 'show.toml')))
    assert (family.members, family.pull) == (config['plugins']['family']['members'], ['leo'])


def test_serve_refuses_a_family_member_with_no_address(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, 'start', lambda **kwargs: None)
    monkeypatch.chdir(tmp_path)
    state = tmp_path / 'state'

    result = runner.invoke(sr.cli, ['serve', '--port', '18400', '--state', str(state),
                                    '--family', 'showstopper'])

    assert result.exit_code == 2
    assert 'NAME=http://host:port' in result.output
    assert not (state / 'show.toml').exists()
