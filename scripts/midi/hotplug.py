"""Hotplug ShowMidi's auto-reconnect through a virtual device, over the REST API.

    uv run --extra midi python scripts/midi/hotplug.py [--interval 2.0] [--phases cycles,flaps,fast]

Starts ``sr start`` on a scratch show with one input and one output on a
virtual port ``SR-Hot``, and an incoming mapping CC 64 -> ``midi.panic``. A CC
sent into the input then comes back out of the output as 32 panic messages, so
one round trip proves both directions. Windows with loopMIDI installed; sends CC
and panic only.

Phases:
  cycles  unplug and replug, each step awaited: status, round trip, survival
  flaps   random up and down spells, many shorter than the interval, then recovery
  fast    unplug and replug inside one interval: is the stale handle noticed?

Prints one JSON line per step and exits 1 if any phase failed.
"""

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tevm import VirtualPort, driver_version  # noqa: E402

T0 = time.monotonic()


def say(step: str, **kw) -> None:
    print(json.dumps({'t': round(time.monotonic() - T0, 2), 'step': step, **kw}), flush=True)


class Hub:
    def __init__(self, port: int, interval: float, work: Path):
        self.base = f'http://127.0.0.1:{port}'
        self.interval = interval
        (work / 'show.toml').write_text(CONFIG.format(port=port, interval=interval), 'utf-8')
        sr = shutil.which('sr')
        if sr is None:
            raise SystemExit('sr not on PATH: run with `uv run`')
        self.log = (work / 'server.log').open('w', encoding='utf-8')
        self.proc = subprocess.Popen(
            [sr, 'start', '--host', '127.0.0.1', '--port', str(port), '-c', 'show.toml'],
            cwd=work, stdout=self.log, stderr=subprocess.STDOUT,
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
        )
        if wait_for(lambda: self.state() is not None, 60) is None:
            raise SystemExit(f'server never answered (exit {self.proc.poll()})')

    def api(self, method: str, path: str, timeout: float = 3.0):
        req = urllib.request.Request(self.base + path, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, None
        except Exception:
            return None, None

    def state(self) -> dict | None:
        code, body = self.api('GET', '/midi/')
        return {c['name']: c['open'] for c in body['connections']} if code == 200 else None

    def alive(self) -> bool:
        return self.proc.poll() is None and self.api('GET', '/openapi.json', 5)[0] == 200

    def both(self, is_open: bool) -> float | None:
        def check():
            s = self.state()
            return bool(s) and s['hot-in'] is is_open and s['hot-out'] is is_open

        return wait_for(check, self.interval * 4 + 5)

    def round_trip(self, vp: VirtualPort) -> bool:
        vp.received.clear()
        vp.send([0xB0, 64, 127])
        panic = wait_for(lambda: len(vp.received) >= 32, 3.0) is not None
        vp.received.clear()
        self.api('POST', '/midi/cc?connection=hot-out&control=7&value=0')
        cc = wait_for(lambda: [0xB0, 7, 0] in vp.received, 2.0) is not None
        return panic and cc

    def stop(self) -> int:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(self.proc.pid)],
                               capture_output=True)
        return self.proc.returncode


CONFIG = '''[showrunner]
current-show = 1
[database]
path = "show.db"
[server]
host = "127.0.0.1"
port = {port}
[plugins.showmidi]
auto_reconnect = true
reconnect_interval = {interval}
[[plugins.showmidi.connections]]
name = "hot-in"
port = "SR-Hot"
direction = "in"
[[plugins.showmidi.connections]]
name = "hot-out"
port = "SR-Hot"
direction = "out"
[[plugins.showmidi.mappings]]
name = "panic on 64"
direction = "in"
type = "cc"
channel = 1
number = 64
command = "midi.panic"
'''


def wait_for(pred, timeout: float) -> float | None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if pred():
            return round(time.monotonic() - start, 2)
        time.sleep(0.05)
    return None


def cycles(hub: Hub, n: int) -> bool:
    ok = True
    for i in range(1, n + 1):
        vp = VirtualPort('SR-Hot')
        up = hub.both(True)
        works = up is not None and hub.round_trip(vp)
        vp.close()
        down = hub.both(False)
        alive = hub.alive()
        say('cycle', n=i, reconnect_s=up, round_trip=works, disconnect_s=down, alive=alive)
        ok = ok and works and down is not None and alive
        if not alive:
            break
    return ok


def flaps(hub: Hub, n: int) -> bool:
    rng = random.Random(22)
    for _ in range(n):
        vp = VirtualPort('SR-Hot')
        time.sleep(rng.uniform(0.05, hub.interval * 2.5))
        vp.close()
        time.sleep(rng.uniform(0.05, hub.interval * 2.5))
        if hub.proc.poll() is not None:
            break
    alive = hub.alive()
    hub.both(False)  # let the last unplug be seen, or recovery measures a fast replug
    vp = VirtualPort('SR-Hot')
    up = hub.both(True)
    works = alive and up is not None and hub.round_trip(vp)
    vp.close()
    hub.both(False)
    say('flaps', n=n, alive=alive, recovered_s=up, round_trip=works)
    return works


def fast(hub: Hub, n: int) -> bool:
    vp = VirtualPort('SR-Hot')
    hub.both(True)
    ok = hub.round_trip(vp)
    for i in range(1, n + 1):
        vp.close()
        time.sleep(0.15)
        vp = VirtualPort('SR-Hot')
        time.sleep(hub.interval * 3)
        works = hub.round_trip(vp)
        say('fast replug', n=i, state=hub.state(), round_trip=works, alive=hub.alive())
        ok = ok and works
    vp.close()
    hub.both(False)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--port', type=int, default=18010)
    ap.add_argument('--interval', type=float, default=2.0)
    ap.add_argument('--phases', default='cycles,flaps,fast')
    ap.add_argument('--cycles', type=int, default=5)
    ap.add_argument('--flaps', type=int, default=40)
    ap.add_argument('--fast', type=int, default=5)
    args = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix='sr-hotplug-'))
    say('start', driver=driver_version(), interval=args.interval, work=str(work))
    hub = Hub(args.port, args.interval, work)
    results = {}
    try:
        for phase in args.phases.split(','):
            fn = {'cycles': cycles, 'flaps': flaps, 'fast': fast}[phase]
            results[phase] = fn(hub, getattr(args, phase))
            if hub.proc.poll() is not None:
                break
    finally:
        code = hub.stop()
        say('end', results=results, server_exit=f'0x{(code or 0) & 0xFFFFFFFF:08X}')
    return 0 if results and all(results.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
