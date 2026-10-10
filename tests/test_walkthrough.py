"""The walkthrough as a set: what running its pages can't check about them.

The pages' examples are checked by running them. This checks the rest: page
names and order, that each page says what it needs to run, that nothing skips,
that every picture a page shows was recorded and every recording is shown, and
that the pages ride the command people already run.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WALKTHROUGH = ROOT / 'walkthrough'
MEDIA = WALKTHROUGH / 'media'
PAGE = re.compile(r'^(\d{2})-[a-z0-9-]+\.md$')
REGENERATE = 'uv run pytest walkthrough/03-in-the-browser.md'


def pages() -> list[Path]:
    return sorted(WALKTHROUGH.glob('*.md'))


def opening(page: Path) -> str:
    return '\n'.join(page.read_text(encoding='utf-8').splitlines()[:4])


def test_pages_are_numbered_in_order_without_gaps():
    names = [p.name for p in pages()]
    assert names and all(PAGE.match(n) for n in names), names
    ordinals = [int(PAGE.match(n).group(1)) for n in names]
    assert ordinals == list(range(1, len(ordinals) + 1)), names


def test_every_page_declares_its_runtime_and_hermetic_pages_come_first():
    tiers = []
    for page in pages():
        head = opening(page)
        assert ('Runtime: hermetic' in head) != ('Runtime-bound' in head), page.name
        tiers.append('Runtime-bound' in head)
    assert tiers == sorted(tiers), 'a hermetic page sits after a runtime-bound one'


def test_no_page_skips_or_discards_a_process_failure():
    for page in pages():
        text = page.read_text(encoding='utf-8')
        assert not re.search(r'pytest\.skip|SkipTest|skipif|skipUnless', text), page.name
        for match in re.finditer(r'subprocess\.run\(', text):
            # the statement runs on over `...` lines until the next prompt or fence
            statement = re.split(r'\n(?:>>> |```)', text[match.start():], maxsplit=1)[0]
            assert re.search(r'check=True|returncode', statement), f'{page.name}: {statement}'


def test_every_picture_shown_was_recorded_and_every_recording_is_shown():
    shown = set()
    for page in pages():
        shown |= set(re.findall(r'!\[[^\]]*\]\(media/([^)]+)\)', page.read_text(encoding='utf-8')))
    recorded = {p.name for p in MEDIA.glob('*.png')}
    assert shown, 'no page shows a recording'
    assert shown <= recorded, f'shown but never recorded: {shown - recorded}; run {REGENERATE}'
    assert recorded <= shown, f'recorded but shown by no page: {recorded - shown}'


def test_the_pages_ride_the_test_command():
    pyproject = (ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    testpaths = re.search(r'^testpaths = \[(.*)\]$', pyproject, re.M).group(1)
    addopts = re.search(r'^addopts = "(.*)"$', pyproject, re.M).group(1)
    assert "'walkthrough'" in testpaths.replace('"', "'")  # `uv run pytest` with no path
    assert '--doctest-glob=*.md' in addopts.split()
    workflow = (ROOT / '.github' / 'workflows' / 'test.yml').read_text(encoding='utf-8')
    runs = re.findall(r'run: (uv run pytest.*)', workflow)
    # CI names the directory: testpaths is ignored as soon as pytest gets a path
    assert runs and all('walkthrough' in r.split() for r in runs), runs
