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
# Note the show ID returned (usually 1)
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
# Note the list IDs (e.g. 1, 2, 3)
```

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

For each cue that should dispatch OSC, edit the cue's `notes` field via
the API (`PATCH /db/cues/{id}`) or the ShowAdmin UI (`/admin`).

Example notes JSON for `Scene: Terminal`:

```json
{"osc": {"address": "/obs/scene", "args": ["Terminal"]}}
```

For `Rec: Start` / `Rec: Wrap`, use `POST /recorder/record?action=start`
from the ShowProgrammer's cue notes — or fire the REST endpoint directly.

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

```bash
# Start ShowRunner first, then call the API:
sr start &
curl -X POST "http://localhost:8000/voicer/generate?show_id=1&script_id=1"
```

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
sr start                              # ShowRunner API up on :8000

# Browser tabs to have open:
#   localhost:8000/script             → script with cue markers
#   localhost:8000/programmer         → ShowProgrammer GO panel
#   localhost:8000/docs               → API reference

# Stage manager fires from /programmer:

POST /programmer/go?cue_list_id=3&show_id=1    # Go: Boot  → OBS records, Ardour rolls
POST /programmer/go?cue_list_id=3&show_id=1    # Go: Meta  → The visible on-screen cue
POST /programmer/go?cue_list_id=3&show_id=1    # Go: Wrap  → OBS stops, Ardour saves
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

Any cue can carry an OSC payload in its `notes` field:

```json
{
  "scene": "Human-readable label (shown in CueLog)",
  "osc": {
    "address": "/ardour/transport_play",
    "args": []
  }
}
```

ShowProgrammer reads this on `GO` or `/programmer/cue/{number}` and
broadcasts to all `osc-targets` in `show.toml`.
