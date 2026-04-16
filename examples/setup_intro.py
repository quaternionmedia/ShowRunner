#!/usr/bin/env python3
"""Populate the ShowRunner intro video show via the REST API.

Idempotent — safe to run multiple times.  Existing shows, scripts,
and cue lists are reused rather than duplicated.

Usage
-----
    # 1. Start ShowRunner first
    sr start

    # 2. Run this script from the repo root
    python examples/setup_intro.py

    # 3. Open ShowRunner in the browser
    #    http://localhost:8000

Optional flags
--------------
    --url URL       ShowRunner base URL   (default: http://localhost:8000)
    --voice VOICE   Kokoro voice name     (default: af_heart)
    --skip-vo       Skip voice-over generation
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_FILE = Path(__file__).parent / "scripts" / "showrunner-intro.fountain"
SHOW_NAME = "ShowRunner Intro"

# ---------------------------------------------------------------------------
# REST helpers
# ---------------------------------------------------------------------------


def _api(client, method: str, path: str, *, params=None, json_body=None):
    """Make an API call; raise with a readable message on HTTP error."""
    try:
        r = client.request(method, path, params=params or {}, json=json_body)
    except Exception as exc:
        _die(f"Connection error calling {method} {path}: {exc}")
    if not r.is_success:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        _die(f"HTTP {r.status_code} from {method} {path}: {detail}")
    return r.json()


def _die(msg: str, hint: str = "") -> None:
    print(f"\n  ERROR: {msg}", file=sys.stderr)
    if hint:
        print(f"  HINT:  {hint}", file=sys.stderr)
    sys.exit(1)


def _warn(msg: str) -> None:
    print(f"  WARNING: {msg}")


def _ok(msg: str) -> None:
    print(f"  {msg}")


# ---------------------------------------------------------------------------
# Setup steps
# ---------------------------------------------------------------------------


def check_server(client) -> None:
    print("\n[1/6] Checking server …")
    try:
        client.get("/")
    except Exception:
        _die(
            "ShowRunner is not responding.",
            "Start it with:  sr start",
        )
    _ok("Server is up.")


def find_or_create_show(client) -> dict:
    print("\n[2/6] Finding / creating show …")
    shows = _api(client, "GET", "/db/shows")
    for s in shows:
        if s["name"] == SHOW_NAME:
            _ok(f"Using existing show  id={s['id']}")
            return s
    show = _api(client, "POST", "/db/shows", params={"name": SHOW_NAME, "venue": "localhost"})
    _ok(f"Created show  id={show['id']}")
    return show


def find_or_create_script(client, show_id: int) -> dict:
    print("\n[3/6] Importing script …")
    scripts = _api(client, "GET", f"/db/shows/{show_id}/scripts")
    if scripts:
        _ok(f"Using existing script  id={scripts[0]['id']}")
        return scripts[0]

    if not SCRIPT_FILE.exists():
        _die(
            f"Fountain script not found: {SCRIPT_FILE}",
            "Run this script from the ShowRunner repo root directory.",
        )
    content = SCRIPT_FILE.read_text(encoding="utf-8")
    script = _api(
        client,
        "POST",
        f"/db/shows/{show_id}/scripts",
        json_body={"title": "ShowRunner: A Self-Producing Introduction", "format": "fountain", "content": content},
    )
    _ok(f"Imported script  id={script['id']}  ({len(content)} chars)")
    return script


def find_or_create_cue_list(client, show_id: int, name: str) -> dict:
    lists = _api(client, "GET", f"/db/shows/{show_id}/cue-lists")
    for cl in lists:
        if cl["name"] == name:
            return cl
    return _api(
        client,
        "POST",
        f"/db/shows/{show_id}/cue-lists",
        json_body={"name": name},
    )


def _add_cue(client, cue_list_id: int, number: int, name: str, layer: str,
             point: int = 0, notes: dict | None = None) -> dict:
    return _api(
        client,
        "POST",
        f"/db/cue-lists/{cue_list_id}/cues",
        json_body={
            "number": number,
            "point": point,
            "name": name,
            "layer": layer,
            "notes": json.dumps(notes) if notes else None,
        },
    )


def _cues_exist(client, cue_list_id: int) -> bool:
    cues = _api(client, "GET", f"/db/cue-lists/{cue_list_id}/cues")
    return len(cues) > 0


def populate_cue_lists(client, show_id: int) -> tuple[int, int, int]:
    print("\n[4/6] Creating cue lists …")

    rec  = find_or_create_cue_list(client, show_id, "RECORDING")
    aud  = find_or_create_cue_list(client, show_id, "AUDIO")
    play = find_or_create_cue_list(client, show_id, "PLAYBACK")
    _ok(f"RECORDING={rec['id']}  AUDIO={aud['id']}  PLAYBACK={play['id']}")

    # --- RECORDING cues (OBS scene switches + record control via HTTP) ------
    if _cues_exist(client, rec["id"]):
        _ok("RECORDING cues already populated — skipping.")
    else:
        recording_cues = [
            (1,  0, "Scene: Terminal",   "Video",
             {"http": {"method": "POST", "path": "/recorder/scene",  "params": {"scene": "Terminal"}}}),
            (1,  1, "Rec: Start",        "Video",
             {"http": {"method": "POST", "path": "/recorder/record", "params": {"action": "start"}}}),
            (2,  0, "Scene: Dashboard",  "Video",
             {"http": {"method": "POST", "path": "/recorder/scene",  "params": {"scene": "Dashboard"}}}),
            (3,  0, "Scene: Scripter",   "Video",
             {"http": {"method": "POST", "path": "/recorder/scene",  "params": {"scene": "Scripter"}}}),
            (4,  0, "Scene: Programmer", "Video",
             {"http": {"method": "POST", "path": "/recorder/scene",  "params": {"scene": "Programmer"}}}),
            (5,  0, "Scene: Terminal",   "Video",
             {"http": {"method": "POST", "path": "/recorder/scene",  "params": {"scene": "Terminal"}}}),
            (99, 0, "Rec: Wrap",         "Video",
             {"http": {"method": "POST", "path": "/recorder/record", "params": {"action": "stop"}}}),
        ]
        for number, point, name, layer, notes in recording_cues:
            _add_cue(client, rec["id"], number, name, layer, point, notes)
        _ok(f"Added {len(recording_cues)} RECORDING cues.")

    # --- AUDIO cues (Ardour transport via OSC) -------------------------------
    if _cues_exist(client, aud["id"]):
        _ok("AUDIO cues already populated — skipping.")
    else:
        audio_cues = [
            (1,  0, "Arm narration",   "Audio",
             {"osc": {"address": "/ardour/rec_enable_toggle", "args": []}}),
            (1,  1, "Transport: Roll", "Audio",
             {"osc": {"address": "/ardour/transport_play",    "args": []}}),
            (4,  0, "Transport: Stop", "Audio",
             {"osc": {"address": "/ardour/transport_stop",    "args": []}}),
            (99, 0, "Save session",    "Audio",
             {"osc": {"address": "/ardour/save_state",        "args": []}}),
        ]
        for number, point, name, layer, notes in audio_cues:
            _add_cue(client, aud["id"], number, name, layer, point, notes)
        _ok(f"Added {len(audio_cues)} AUDIO cues.")

    # --- PLAYBACK cues (the stage-manager GO sequence) ----------------------
    if _cues_exist(client, play["id"]):
        _ok("PLAYBACK cues already populated — skipping.")
    else:
        playback_cues = [
            (1,  0, "Go: Boot", "Stage", None),
            (4,  0, "Go: Meta", "Stage", {
                "scene": "This is the cue the viewer will watch fire.",
                "osc": {"address": "/obs/scene", "args": ["Programmer"]},
            }),
            (99, 0, "Go: Wrap", "Stage", None),
        ]
        for number, point, name, layer, notes in playback_cues:
            _add_cue(client, play["id"], number, name, layer, point, notes)
        _ok(f"Added {len(playback_cues)} PLAYBACK cues.")

    return rec["id"], aud["id"], play["id"]


def generate_voiceover(client, show_id: int, script_id: int, skip: bool) -> None:
    print("\n[5/6] Generating voice-over …")
    if skip:
        _ok("Skipped (--skip-vo).")
        return

    try:
        result = _api(
            client, "POST", "/voicer/generate",
            params={"show_id": show_id, "script_id": script_id},
        )
    except SystemExit:
        # _api already printed the error; check for espeak-ng hint
        _warn(
            "Voice generation failed.  Common causes on Windows:\n"
            "  • espeak-ng not installed — download from https://github.com/espeak-ng/espeak-ng/releases\n"
            "  • kokoro / soundfile not installed — run:  uv sync --group av\n"
            "  • Set PHONEMIZER_ESPEAK_PATH=C:\\Program Files\\eSpeak NG\\espeak-ng.exe"
        )
        _ok("Continuing without voice-over — you can regenerate later via POST /voicer/generate.")
        return

    generated = result.get("generated", 0)
    total = result.get("total", 0)
    _ok(f"Generated {generated}/{total} WAV files.")

    for w in result.get("warnings", []):
        _warn(w)

    if generated == 0 and total > 0:
        _warn(
            "0 files generated.  If kokoro is installed, check that espeak-ng is on PATH:\n"
            "  Windows: install from https://github.com/espeak-ng/espeak-ng/releases\n"
            "           or set PHONEMIZER_ESPEAK_PATH before running sr start"
        )


def check_av_connections(client) -> None:
    """Probe OBS and Ardour; warn but never block setup."""
    print("\n[6/6] Checking AV connections …")

    try:
        obs = _api(client, "GET", "/recorder/status")
        if obs.get("obs") == "connected":
            _ok(f"OBS connected  (recording={obs.get('recording')})")
        else:
            _warn(
                f"OBS not connected (status={obs.get('obs')}).  "
                "Open OBS and enable WebSocket: Tools → WebSocket Server Settings → Enable."
            )
    except SystemExit:
        _warn("ShowRecorder unreachable — OBS integration unavailable.")

    try:
        mixer = _api(client, "GET", "/mixer/status")
        _ok(f"Ardour target: {mixer.get('ardour_host')}:{mixer.get('ardour_osc_port')}")
    except SystemExit:
        _warn("ShowMixer unreachable — Ardour integration unavailable.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url",     default="http://localhost:8000", help="ShowRunner base URL")
    parser.add_argument("--voice",   default="af_heart",              help="Kokoro voice name")
    parser.add_argument("--skip-vo", action="store_true",             help="Skip voice-over generation")
    args = parser.parse_args()

    try:
        import httpx
    except ImportError:
        print("ERROR: httpx not installed.  Run: pip install httpx", file=sys.stderr)
        sys.exit(1)

    base = args.url.rstrip("/")
    print("ShowRunner Intro — automated setup")
    print(f"Target: {base}")
    print("=" * 50)

    with httpx.Client(base_url=base, timeout=15) as client:
        check_server(client)
        show   = find_or_create_show(client)
        script = find_or_create_script(client, show["id"])
        rec_id, aud_id, play_id = populate_cue_lists(client, show["id"])
        generate_voiceover(client, show["id"], script["id"], skip=args.skip_vo)
        check_av_connections(client)

    print("\n" + "=" * 50)
    print("Setup complete!")
    print(f"  Dashboard:    {base}/")
    print(f"  Script view:  {base}/script")
    print(f"  Programmer:   {base}/programmer")
    print(f"  Show ID:      {show['id']}")
    print(f"  PLAYBACK list ID for recording: {play_id}")
    print()
    print("To run the recording session, fire three GOs from the Programmer page")
    print("or via the API:")
    print(f"  curl -X POST \"{base}/programmer/go?cue_list_id={play_id}&show_id={show['id']}\"")


if __name__ == "__main__":
    main()
