# ShowRunner Intro Video — Production Runbook

This is the stage-manager document for the self-producing intro video.
ShowRunner manages every cue in this production. The script, the cue lists,
and this runbook were all written before the first frame was recorded.

---

## Toolchain

| Role | Tool | Connection |
| ---- | ---- | ---------- |
| Show control | ShowRunner | `http://localhost:8000` |
| Screen recording | OBS Studio 28+ | WebSocket v5 on port 4455 |
| Audio recording / mix | Ardour 7+ | OSC on port 3819 |
| Video assembly | Kdenlive | MLT XML import |
| Voice-over | Kokoro TTS (via ShowVoicer) | Local, no internet required |
| Cue dispatch | ShowProgrammer | OSC to Ardour + custom targets |

---

## Pre-Production Checklist

### 1. Install AV dependencies

```bash
uv sync --group av
```

### 2. Create the show

```bash
sr create "ShowRunner Intro" --venue "localhost"
# Note the show ID returned, then update show.toml:
#   current-show = <id>
```

### 3. Import the script

```bash
sr scripts add "ShowRunner: A Self-Producing Introduction" \
  --show 1 --file examples/scripts/showrunner-intro.fountain
# Note the script ID returned
```

### 4. Create cue lists

```bash
sr cue-lists create "RECORDING" --show 1
sr cue-lists create "AUDIO"     --show 1
sr cue-lists create "PLAYBACK"  --show 1
```

Confirm the IDs assigned (creation order determines ID):

```bash
sr cue-lists list --show 1
# Or via API once the server is up: GET /db/shows/1/cue-lists
```

> **Note the PLAYBACK list ID** — you will need it in the Recording Session
> section.  In a fresh database the IDs will be 1, 2, 3 respectively.

### 5. Populate the RECORDING cue list (Video layer → OBS)

Replace `<list_id>` with the RECORDING list ID.

```bash
# Scene switches + record control
sr cues add <list_id> 1 "Scene: Terminal"   --layer Video --type Network
sr cues add <list_id> 1 "Rec: Start"        --layer Video --type Network --point 1
sr cues add <list_id> 2 "Scene: Dashboard"  --layer Video --type Network
sr cues add <list_id> 3 "Scene: Scripter"   --layer Video --type Network
sr cues add <list_id> 4 "Scene: Programmer" --layer Video --type Network
sr cues add <list_id> 5 "Scene: Terminal"   --layer Video --type Network
sr cues add <list_id> 99 "Rec: Wrap"        --layer Video --type Network
```

For each cue, attach notes via `PATCH /db/cues/{id}` (or `sr cues add ... --notes`):

**Scene switches** use the `http` action to call ShowRecorder's REST endpoint
(OBS uses WebSocket, not OSC — do not use the `osc` key for scene switches):

```json
{"http": {"method": "POST", "path": "/recorder/scene", "params": {"scene": "Terminal"}}}
```

**Record start/stop:**

```json
{"http": {"method": "POST", "path": "/recorder/record", "params": {"action": "start"}}}
```

```json
{"http": {"method": "POST", "path": "/recorder/record", "params": {"action": "stop"}}}
```

**Ardour transport** (AUDIO cue list) still uses OSC, dispatched to
`osc-targets` in `show.toml`:

```json
{"osc": {"address": "/ardour/transport_play", "args": []}}
```

A cue can carry both `http` and `osc` keys to fire multiple systems at once.

### 6. Populate the AUDIO cue list (Audio layer → Ardour)

```bash
sr cues add <list_id> 1 "Arm narration"    --layer Audio --type Network
sr cues add <list_id> 1 "Transport: Roll"  --layer Audio --type Network --point 1
sr cues add <list_id> 4 "Transport: Stop"  --layer Audio --type Network
sr cues add <list_id> 99 "Save session"    --layer Audio --type Network
```

Example notes JSON for `Transport: Roll`:

```json
{"osc": {"address": "/ardour/transport_play", "args": []}}
```

### 7. Populate the PLAYBACK cue list (Stage layer → ShowProgrammer)

```bash
sr cues add <list_id> 1  "Go: Boot"  --layer Stage --type Network
sr cues add <list_id> 4  "Go: Meta"  --layer Stage --type Network
sr cues add <list_id> 99 "Go: Wrap"  --layer Stage --type Network
```

Cue 4.0 is the one the viewer watches fire on screen. Its notes:

```json
{"scene": "This is the cue the viewer will watch fire.", "osc": {"address": "/obs/scene", "args": ["Programmer"]}}
```

### 8. Generate voice-over audio

Requires the `av` dependency group (`uv sync --group av`).

```bash
# Start ShowRunner (if not already running), wait until ready, then generate:
sr start &
until curl -s http://localhost:8000/ > /dev/null 2>&1; do sleep 1; done
curl -X POST "http://localhost:8000/voicer/generate?show_id=1&script_id=1"
```

Verify all 6 files were written (`generated` must equal `total`):

```bash
curl -s "http://localhost:8000/voicer/files"
# → {"directory": "/absolute/path/exports/narration", "files": ["vo-1-paradox.wav", ...], "count": 6}
```

If `generated: 0`, the av group is not installed — run `uv sync --group av` and retry.

This writes 6 WAV files to `./exports/narration/`:

```text
vo-1-paradox.wav   "ShowRunner is the software..."
vo-2-system.wav    "A show is a script..."
vo-3-meta.wav      "Right now, ShowRunner is managing..."
vo-4-meta.wav      "That just happened..."
vo-5-close.wav     "ShowRunner is the show..."
vo-6-close.wav     "Fork it. Build something."
```

Preview a single block before full generation:

```bash
curl -X POST "http://localhost:8000/voicer/preview?script_id=1&block_index=4"
# → plays "That just happened. You watched it happen."
```

### 9. Configure OBS scenes

In OBS, create these scenes to match the RECORDING cue list:

| Scene name | Content |
| ---------- | ------- |
| `Terminal` | Full-screen terminal window |
| `Dashboard` | ShowRunner dashboard at `localhost:8000` |
| `Scripter` | ShowScripter at `localhost:8000/script` |
| `Programmer` | ShowProgrammer at `localhost:8000/programmer` |

Enable WebSocket Server: `Tools → WebSocket Server Settings → Enable`.
Copy the password into `show.toml` under `[plugins.recorder]`.

### 10. Configure Ardour

1. Open Ardour → create a new session
2. Import the VO WAVs: `Session → Import → ./exports/narration/vo-*.wav`
3. Enable OSC: `Edit → Preferences → Control Surfaces → Open Sound Control → Enable`
4. Add a "Narration" track, place WAV clips at rough timecodes from script

Or use the auto-layout export:

```bash
curl "http://localhost:8000/voicer/export/ardour?script_id=1" | jq .file
# Open the .ardour file in Ardour
```

---

## Recording Session

```bash
sr start &

# Wait for the server to be ready before proceeding:
until curl -s http://localhost:8000/ > /dev/null 2>&1; do sleep 1; done
echo "ShowRunner is up"
```

Browser tabs to have open:

- `localhost:8000/script` → script with cue markers
- `localhost:8000/programmer` → ShowProgrammer GO panel
- `localhost:8000/docs` → API reference (Swagger UI)

Look up your PLAYBACK cue list ID if needed:

```bash
curl -s "http://localhost:8000/db/shows/1/cue-lists" | jq '.[] | select(.name=="PLAYBACK") | .id'
# substitute that ID for PLAYBACK_ID below
```

Stage manager fires three GOs from `/programmer` (replace `PLAYBACK_ID` with
the PLAYBACK list ID noted in Step 4):

```bash
curl -X POST "http://localhost:8000/programmer/go?cue_list_id=PLAYBACK_ID&show_id=1"
# Go: Boot  → OBS records, Ardour rolls

curl -X POST "http://localhost:8000/programmer/go?cue_list_id=PLAYBACK_ID&show_id=1"
# Go: Meta  → the visible on-screen cue

curl -X POST "http://localhost:8000/programmer/go?cue_list_id=PLAYBACK_ID&show_id=1"
# Go: Wrap  → OBS stops, Ardour saves
```

The `/programmer` NiceGUI page (once built out) will provide a single **GO**
button.  Until then, fire cues via the API or `/docs` Swagger UI.

---

## Post-Production

### Step 1 — Export MLT project for Kdenlive

```bash
curl "http://localhost:8000/recorder/export/mlt?show_id=1" | jq .mlt > project.mlt
```

Open `project.mlt` in Kdenlive. The OBS recordings appear as clips on the
timeline with in/out points derived from CueLog timestamps.

### Step 2 — Add VO audio

```bash
curl "http://localhost:8000/voicer/export/mlt-audio" | jq .  # coming soon
```

For now: import `exports/narration/vo-*.wav` manually into Kdenlive as
a second audio track alongside the Ardour mix.

### Step 3 — Mix in Ardour

1. Export Ardour session: `Session → Export → Audio Files`
2. Import stem into Kdenlive audio track 1

### Step 4 — Final assembly in Kdenlive

- Trim clips to match narration pacing
- Add lower-thirds titles matching the Fountain scene headings
- Grade: dark terminal sections, bright UI sections
- Export: H.264, 1080p60, stereo AAC

---

## API Quick Reference

| Endpoint | Purpose |
| -------- | ------- |
| `POST /recorder/scene?scene=Terminal` | Switch OBS scene |
| `POST /recorder/record?action=start` | Start OBS recording |
| `POST /recorder/record?action=stop` | Stop OBS recording |
| `GET  /recorder/export/mlt?show_id=1` | Kdenlive MLT project |
| `POST /mixer/transport?action=play` | Ardour transport play |
| `POST /mixer/transport?action=stop` | Ardour transport stop |
| `POST /mixer/record` | Toggle Ardour record-arm |
| `POST /programmer/go?cue_list_id=3&show_id=1` | Fire next cue |
| `POST /programmer/cue/4.0?cue_list_id=3&show_id=1` | Fire cue 4.0 |
| `POST /programmer/reset` | Reset GO pointer |
| `POST /voicer/generate?show_id=1&script_id=1` | Generate all VO WAVs |
| `GET  /voicer/lines?script_id=1` | Preview extracted VO text |
| `GET  /voicer/files` | List generated WAV files |
| `GET  /voicer/export/ardour?script_id=1` | Ardour session XML |

---

## Cue Notes JSON Schema

A cue's `notes` field is a JSON string with up to three optional keys.
All keys are independent — a cue can carry any combination.

```json
{
  "scene": "Human-readable label written to CueLog on fire",
  "osc": {
    "address": "/ardour/transport_play",
    "args": []
  },
  "http": {
    "method": "POST",
    "path": "/recorder/scene",
    "params": {"scene": "Terminal"}
  }
}
```

- **`"scene"`** — label stored in `CueLog.notes`; appears in the cue log view and MLT export comments.
- **`"osc"`** — dispatched via UDP to all `osc-targets` in `show.toml` (e.g. Ardour).
- **`"http"`** — dispatched as an internal HTTP call to another ShowRunner endpoint (e.g. ShowRecorder for OBS).
