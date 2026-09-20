# Production Dry-Run — Remediation Log

Findings from a full end-to-end dry-run of the intro-video production pipeline
(April 2026).  Each issue is graded **🔴 Blocker**, **🟡 Significant**, or
**🔵 Minor**, followed by its status and the exact fix applied.

---

## Issue 1 — `PATCH /db/cues/{id}` does not exist 🔴 Blocker

**Symptom:** The runbook (Steps 5–7) tells operators to attach OSC `notes` to
cues via `PATCH /db/cues/{id}` or the ShowAdmin UI.  Neither route existed.
Every cue had `notes: null` after setup; no OSC payload could be dispatched.

**Root cause:** The ShowDB plugin exposed only `GET` and `POST /db/shows`.
No mutation endpoints existed for individual cues.

**Fix:** Added `PATCH /db/cues/{cue_id}` to [plugins/db.py](../../src/showrunner/plugins/db.py).
Accepts a JSON body with any subset of `{name, notes, layer, color}`.

---

## Issue 2 — OSC dispatch cannot reach OBS 🔴 Blocker

**Symptom:** `ShowProgrammer._fire_cue` sends OSC UDP packets to
`osc-targets`.  OBS only exposes a WebSocket server, not an OSC server.
Scene-switch and record-control cues in the RECORDING list would silently fire
OSC into the void.

**Root cause:** The programmer only supported OSC dispatch.  No mechanism
existed for a cue to trigger a ShowRunner REST endpoint.

**Fix:** Added an `http` action key to the cue notes JSON schema in
[plugins/programmer.py](../../src/showrunner/plugins/programmer.py).
When a cue's notes contain `"http": {"method": "POST", "path": "/recorder/scene", "params": {"scene": "Terminal"}}`,
`_fire_cue` makes an internal `httpx` request to `http://localhost:{port}`.

Updated cue notes examples in the runbook accordingly.

---

## Issue 3 — `/voicer/generate` returns HTTP 200 with `generated: 0` 🔴 Blocker

**Symptom:** With kokoro absent, `POST /voicer/generate` returned
`{"generated": 0, "total": 6}` with HTTP 200.  Operators had no indication
that all six files failed; the next step (`/voicer/export/ardour`) then 404'd.

**Fix:** [plugins/voicer.py](../../src/showrunner/plugins/voicer.py) now
returns HTTP 503 when `generated == 0 and total > 0`, with
`"detail": "No audio generated — install kokoro/soundfile (uv sync --group av)"`.
Partial success (some blocks ok) stays 200 with a `"warnings"` list.

---

## Issue 4 — MLT XML has no `<producer>` declarations 🟡 Significant

**Symptom:** `GET /recorder/export/mlt` produced `<entry producer="clip_0"/>`
references with no matching `<producer id="clip_0" resource="..."/>` elements.
Kdenlive opened the file but showed broken/empty clips.

**Root cause:** OBS recording file paths were unknown to ShowRunner.

**Fix:** Added `obs-output-dir` config key under `[plugins.recorder]` in
[show.toml](../../show.toml).  `_build_mlt_xml` now scans that directory for
`.mkv`/`.mp4` files ordered by modification time and emits `<producer>` blocks.
Falls back to stub producers with a `<!-- TODO -->` comment when the dir is
unset or empty.

---

## Issue 5 — `CueLog.triggered_at` loses timezone through SQLite 🟡 Significant

**Symptom:** `_fire_cue` stored `datetime.now(timezone.utc)` (tz-aware), but
SQLite strips the offset.  The API returned `"triggered_at": "2026-04-16T00:53:16"` 
without `+00:00`.  Passing a tz-aware `recording_start` to `_build_mlt_xml`
would throw `TypeError: can't subtract offset-naive and offset-aware datetimes`.

**Fix:** [plugins/programmer.py](../../src/showrunner/plugins/programmer.py)
now stores `datetime.utcnow()` (naive UTC) consistently.  `_build_mlt_xml` in
[plugins/recorder.py](../../src/showrunner/plugins/recorder.py) strips any
`tzinfo` from `recording_start` before subtraction.

---

## Issue 6 — `/voicer/files` returns relative path with Windows backslashes 🟡 Significant

**Symptom:** `GET /voicer/files` returned `"directory": "exports\\narration"`.
The operator needed an absolute, forward-slash path to locate WAV files.

**Fix:** `_output_dir()` in [plugins/voicer.py](../../src/showrunner/plugins/voicer.py)
now calls `.resolve()` before returning, and the API response uses `.as_posix()`.

---

## Issue 7 — No REST API for cue lists; no filter on `/db/shows/{id}/cues` 🟡 Significant

**Symptom:** Operators using the Swagger UI had no way to list cue lists or
look up their IDs.  `/db/shows/{id}/cues` returned all cues across all lists
with no filtering.

**Fix:** Added to [plugins/db.py](../../src/showrunner/plugins/db.py):
- `GET /db/shows/{show_id}/cue-lists` — list all cue lists for a show
- `?cue_list_id=` query param on `GET /db/shows/{show_id}/cues`

---

## Issue 8 — `sr start &` has no server readiness check 🔵 Minor

**Symptom:** Step 8 of the runbook runs `sr start &` immediately followed by
a `curl` to the API.  On slow machines the server isn't up yet and the curl
fails with connection refused.

**Fix:** Runbook updated to use a `until curl -s .../voicer/lines` polling
loop before proceeding.

---

## Issue 9 — Recording session hardcodes `cue_list_id=3` 🔵 Minor

**Symptom:** The Recording Session section used literal `cue_list_id=3`
without explaining the derivation.  A re-run or out-of-order setup assigns
different IDs.

**Fix:** Runbook updated to reference the PLAYBACK ID noted in Step 4, and
added a `GET /db/shows/1/cue-lists` verification step.

---

## Issue 10 — `show.toml` `current-show` not updated after creation 🔵 Minor

**Symptom:** `show.toml` shipped with `current-show = 1`.  On a second run
the new show gets ID 2, but CLI commands continue targeting show 1.

**Fix:** Runbook updated with an explicit step to set `current-show` after
`sr create`, using `sr config set current-show <id>` (new command added to
[cli.py](../../src/showrunner/cli.py)).

---

## Summary

| # | Issue | Severity | Status |
|---|-------|----------|--------|
| 1 | `PATCH /db/cues/{id}` missing | 🔴 Blocker | ✅ Fixed |
| 2 | OSC can't reach OBS | 🔴 Blocker | ✅ Fixed |
| 3 | `/voicer/generate` silent 200 failure | 🔴 Blocker | ✅ Fixed |
| 4 | MLT XML missing `<producer>` blocks | 🟡 Significant | ✅ Fixed |
| 5 | CueLog timezone loss → potential crash | 🟡 Significant | ✅ Fixed |
| 6 | `/voicer/files` relative path | 🟡 Significant | ✅ Fixed |
| 7 | No cue-lists API / no cues filter | 🟡 Significant | ✅ Fixed |
| 8 | No server readiness check in runbook | 🔵 Minor | ✅ Fixed |
| 9 | Hardcoded `cue_list_id=3` | 🔵 Minor | ✅ Fixed |
| 10 | `current-show` not updated after create | 🔵 Minor | ✅ Fixed |
