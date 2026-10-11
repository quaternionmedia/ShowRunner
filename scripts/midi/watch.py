"""Serve ShowMidi against a real device and print what it sees, for hand hotplug tests.

    uv run --extra midi python scripts/midi/watch.py "Launchkey 49" [--interval 2.0]

Starts ``sr start`` on a scratch show with one input and one output matching
PORT. Every note and CC on every channel maps to a no-op command, so any key or
knob shows up as an ``input`` line. Prints one JSON line per event: a
connection's state changing (polled every 0.2 s), input arriving, rtmidi's
half-closed warning, or the server exiting or no longer answering. Sends nothing
to the device. Ctrl+C stops it.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

T0 = time.monotonic()


def say(event: str, **kw) -> None:
    print(json.dumps({'t': round(time.monotonic() - T0, 1), 'event': event, **kw}), flush=True)


def config(port_name: str, port: int, interval: float) -> str:
    head = f'''[showrunner]
current-show = 1
[database]
path = "show.db"
[server]
host = "127.0.0.1"
port = {port}
[plugins.showlogger]
level = "INFO"
file = "show.log"
[plugins.showmidi]
auto_reconnect = true
reconnect_interval = {interval}
[[plugins.showmidi.connections]]
name = "dev-in"
port = "{port_name}"
direction = "in"
[[plugins.showmidi.connections]]
name = "dev-out"
port = "{port_name}"
direction = "out"
'''
    return head + ''.join(
        f'[[plugins.showmidi.mappings]]\nname = "{t}{ch}-{n}"\ndirection = "in"\n'
        f'connection = "dev-in"\ntype = "{t}"\nchannel = {ch}\nnumber = {n}\n'
        f'command = "midi.ping"\n'
        for t in ('note', 'cc')
        for ch in range(1, 17)
        for n in range(128)
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('device', help="substring of the device's MIDI port name, without its number")
    ap.add_argument('--port', type=int, default=18010)
    ap.add_argument('--interval', type=float, default=2.0)
    args = ap.parse_args()
    sr = shutil.which('sr')
    if sr is None:
        raise SystemExit('sr not on PATH: run with `uv run`')
    work = Path(tempfile.mkdtemp(prefix='sr-watch-'))
    (work / 'show.toml').write_text(config(args.device, args.port, args.interval), 'utf-8')
    proc = subprocess.Popen(
        [sr, 'start', '--host', '127.0.0.1', '--port', str(args.port), '-c', 'show.toml'],
        cwd=work, stdout=(work / 'server.log').open('w', encoding='utf-8'),
        stderr=subprocess.STDOUT, env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
    )
    say('start', device=args.device, interval=args.interval, work=str(work))
    log, seen, last, silent = work / 'show.log', 0, None, 0
    try:
        while True:
            if proc.poll() is not None:
                say('server exited', code=f'0x{proc.returncode & 0xFFFFFFFF:08X}')
                return 1
            try:
                url = f'http://127.0.0.1:{args.port}/midi/'
                with urllib.request.urlopen(url, timeout=1) as r:
                    body = json.load(r)
                state = {c['name']: 'open' if c['open'] else (c['error'] or 'closed')
                         for c in body['connections']}
                silent = 0
            except Exception:
                state, silent = None, silent + 1
                if silent == 15:
                    say('server not answering', alive=proc.poll() is None)
            if state is not None and state != last:
                say('status', **state)
                last = state
            lines = log.read_text('utf-8', errors='replace').splitlines() if log.exists() else []
            for line in lines[seen:]:
                m = re.search(r"MIDI '((?:note|cc)\d+-\d+)' ->", line)
                if m:
                    say('input', got=m.group(1))
                elif 'a valid connection already exists' in line:
                    say('rtmidi: reopened a half-closed input')
            seen = len(lines)
            time.sleep(0.2)
    except KeyboardInterrupt:
        return 0
    finally:
        if proc.poll() is None:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)], capture_output=True)


if __name__ == '__main__':
    sys.exit(main())
