# Self-Producing Intro Video

A complete AV recording pipeline managed entirely by ShowRunner.
ShowRunner controls OBS (video capture), Ardour (audio), and generates its own
narration via Kokoro TTS — while being recorded by the very cues it fires.

Each block below is independently runnable. Shell variables (`SHOW_ID`,
`REC_LIST`, etc.) thread context from one block to the next.

---

## Prerequisites

Install AV dependencies and start the server:

```bash
uv sync --group av
sr start &
until curl -s http://localhost:8000/ > /dev/null 2>&1; do sleep 1; done
echo "ShowRunner ready"
```

Configure OBS WebSocket and the output directory in `show.toml`:

```toml
[plugins.recorder]
obs-host     = "localhost"
obs-port     = 4455
obs-password = "your-password"
obs-output-dir = "~/Videos"   # where OBS saves recordings — used for MLT export

[plugins.voicer]
voice = "af_heart"
speed = 1.0
output-dir = "./exports/narration"
```

---

## Block 1 — Bootstrap: show + script

Creates the show and imports the Fountain screenplay. Every subsequent block
references `$SHOW_ID` and `$SCRIPT_ID`.

```bash
# Create the show
SHOW_ID=$(curl -s -X POST \
  "http://localhost:8000/db/shows?name=ShowRunner+Intro&venue=localhost" \
  | jq '.id')
echo "Show: $SHOW_ID"

# Record it as the active show in show.toml
#   current-show = $SHOW_ID

# Import the script and capture its ID from the printed output
SCRIPT_ID=$(sr scripts add "ShowRunner: A Self-Producing Introduction" \
  --show "$SHOW_ID" \
  --file examples/scripts/showrunner-intro.fountain \
  2>&1 | grep -oP 'id=\K\d+')
echo "Script: $SCRIPT_ID"
```

Verify the script was stored:

```bash
curl -s "http://localhost:8000/voicer/lines?script_id=$SCRIPT_ID" | jq '.blocks[].page'
# → "paradox", "system", "meta", "meta", "close", "close"
```

---

## Block 2 — Create layered cue lists

Three lists map to three production layers: `Video` (OBS), `Audio` (Ardour),
`Stage` (ShowProgrammer's own GO sequence).

```bash
sr cue-lists create "RECORDING" --show "$SHOW_ID"
sr cue-lists create "AUDIO"     --show "$SHOW_ID"
sr cue-lists create "PLAYBACK"  --show "$SHOW_ID"
```

Then look up the assigned IDs via the REST API and capture them as shell
variables for the blocks that follow:

```bash
LISTS=$(curl -s "http://localhost:8000/db/shows/$SHOW_ID/cue-lists")
REC_LIST=$(echo "$LISTS"      | jq '.[] | select(.name=="RECORDING") | .id')
AUDIO_LIST=$(echo "$LISTS"    | jq '.[] | select(.name=="AUDIO")     | .id')
PLAYBACK_LIST=$(echo "$LISTS" | jq '.[] | select(.name=="PLAYBACK")  | .id')
echo "RECORDING=$REC_LIST  AUDIO=$AUDIO_LIST  PLAYBACK=$PLAYBACK_LIST"
```

---

## Block 3 — Populate RECORDING cues (OBS via HTTP)

OBS uses WebSocket, not OSC. Scene switches and record control reach OBS
through ShowRecorder's REST endpoints using the `"http"` action key.

```bash
# Add the cue stubs (layer=Video, no notes yet)
sr cues add "$REC_LIST" 1  "Scene: Terminal"   --layer Video --type Network
sr cues add "$REC_LIST" 1  "Rec: Start"        --layer Video --type Network --point 1
sr cues add "$REC_LIST" 2  "Scene: Dashboard"  --layer Video --type Network
sr cues add "$REC_LIST" 3  "Scene: Scripter"   --layer Video --type Network
sr cues add "$REC_LIST" 4  "Scene: Programmer" --layer Video --type Network
sr cues add "$REC_LIST" 5  "Scene: Terminal"   --layer Video --type Network
sr cues add "$REC_LIST" 99 "Rec: Wrap"         --layer Video --type Network
```

Fetch the cue IDs so you can attach notes:

```bash
curl -s "http://localhost:8000/db/shows/$SHOW_ID/cues?cue_list_id=$REC_LIST" \
  | jq '.[] | {id, number, point, name}'
```

Attach the HTTP action to each cue via `PATCH`. The `notes` field is a
JSON-encoded string, so use `jq -n --arg` to build the payload cleanly:

```bash
# Scene switch — ShowProgrammer calls POST /recorder/scene?scene=Terminal
SCENE_CUE_ID=<id of "Scene: Terminal">
NOTES='{"http":{"method":"POST","path":"/recorder/scene","params":{"scene":"Terminal"}}}'
curl -s -X PATCH "http://localhost:8000/db/cues/$SCENE_CUE_ID" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg n "$NOTES" '{notes: $n}')"
```

Record start and stop use the `/recorder/record` endpoint:

```bash
# Rec: Start (cue 1.1)
NOTES='{"http":{"method":"POST","path":"/recorder/record","params":{"action":"start"}}}'
curl -s -X PATCH "http://localhost:8000/db/cues/$REC_START_ID" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg n "$NOTES" '{notes: $n}')"

# Rec: Wrap (cue 99)
NOTES='{"http":{"method":"POST","path":"/recorder/record","params":{"action":"stop"}}}'
curl -s -X PATCH "http://localhost:8000/db/cues/$REC_WRAP_ID" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg n "$NOTES" '{notes: $n}')"
```

!!! note "Why HTTP, not OSC, for OBS?"
    OBS Studio's remote control protocol is WebSocket v5, not OSC.
    ShowRecorder bridges that gap: it exposes a REST API (`/recorder/scene`,
    `/recorder/record`) and translates calls to OBS WebSocket internally.
    Cues reach OBS by pointing at ShowRecorder — not by speaking WebSocket
    directly.

---

## Block 4 — Populate AUDIO cues (Ardour via OSC)

Ardour understands OSC natively. AUDIO cues use the `"osc"` key and are
dispatched by ShowProgrammer to the targets listed in `show.toml`.

```bash
sr cues add "$AUDIO_LIST" 1  "Arm narration"   --layer Audio --type Network
sr cues add "$AUDIO_LIST" 1  "Transport: Roll" --layer Audio --type Network --point 1
sr cues add "$AUDIO_LIST" 4  "Transport: Stop" --layer Audio --type Network
sr cues add "$AUDIO_LIST" 99 "Save session"    --layer Audio --type Network
```

Attach OSC payloads:

```bash
ROLL_ID=<id of "Transport: Roll">
NOTES='{"osc":{"address":"/ardour/transport_play","args":[]}}'
curl -s -X PATCH "http://localhost:8000/db/cues/$ROLL_ID" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg n "$NOTES" '{notes: $n}')"

STOP_ID=<id of "Transport: Stop">
NOTES='{"osc":{"address":"/ardour/transport_stop","args":[]}}'
curl -s -X PATCH "http://localhost:8000/db/cues/$STOP_ID" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg n "$NOTES" '{notes: $n}')"
```

!!! tip "One cue, two systems"
    A cue can carry both `"http"` and `"osc"` keys in its notes JSON.
    ShowProgrammer fires both on a single GO, reaching OBS and Ardour
    simultaneously from one button press.

A dual-action cue notes payload:

```json
{
  "http": {"method": "POST", "path": "/recorder/record", "params": {"action": "start"}},
  "osc":  {"address": "/ardour/transport_play", "args": []}
}
```

---

## Block 5 — Populate PLAYBACK cues (the stage-manager sequence)

Three GOs drive the whole recording. Cue 4.0 is the meta moment — the viewer
watches it fire on screen.

```bash
sr cues add "$PLAYBACK_LIST" 1  "Go: Boot" --layer Stage --type Network
sr cues add "$PLAYBACK_LIST" 4  "Go: Meta" --layer Stage --type Network
sr cues add "$PLAYBACK_LIST" 99 "Go: Wrap" --layer Stage --type Network
```

Attach a human label and the on-screen OSC payload to cue 4.0:

```bash
META_ID=<id of "Go: Meta">
NOTES='{"scene":"This is the cue the viewer will watch fire.","osc":{"address":"/obs/scene","args":["Programmer"]}}'
curl -s -X PATCH "http://localhost:8000/db/cues/$META_ID" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg n "$NOTES" '{notes: $n}')"
```

The `"scene"` key is the label written to `CueLog.notes` on each fire — it
appears in the log view and in the MLT export comments.

---

## Block 6 — Generate voice-over (ShowVoicer)

ShowVoicer parses every `NARRATOR (V.O.)` block from the stored Fountain
script and writes a numbered WAV file for each:

```bash
curl -s -X POST \
  "http://localhost:8000/voicer/generate?show_id=$SHOW_ID&script_id=$SCRIPT_ID" \
  | jq '{generated, total}'
# → {"generated": 6, "total": 6}
```

Verify the files exist:

```bash
curl -s "http://localhost:8000/voicer/files" | jq '{count, files}'
# → {"count": 6, "files": ["vo-1-paradox.wav", "vo-2-system.wav", ...]}
```

Preview a single block before committing to a full run:

```bash
# Audition block 4: "That just happened. You watched it happen."
curl -s -X POST \
  "http://localhost:8000/voicer/preview?script_id=$SCRIPT_ID&block_index=4" \
  | jq '{text, file}'
```

Import into Ardour or generate a pre-positioned session file:

```bash
curl -s "http://localhost:8000/voicer/export/ardour?script_id=$SCRIPT_ID" \
  | jq '.file'
# Open the returned .ardour file to get clips pre-laid on a Narration track
```

!!! note "If `generated: 0`"
    The `av` dependency group is not installed. Run `uv sync --group av` and
    retry. The `/voicer/generate` endpoint returns HTTP 503 with a diagnostic
    message when Kokoro is missing.

---

## Block 7 — Run the recording session

Open these browser tabs before pressing GO:

| Tab | URL |
| --- | --- |
| Script + cue markers | `http://localhost:8000/script` |
| ShowProgrammer GO panel | `http://localhost:8000/programmer` |
| API explorer | `http://localhost:8000/docs` |

Fire the three GOs. Each call advances the PLAYBACK list by one cue:

```bash
# GO 1 — Boot: OBS starts recording, Ardour rolls
curl -s -X POST \
  "http://localhost:8000/programmer/go?cue_list_id=$PLAYBACK_LIST&show_id=$SHOW_ID" \
  | jq '{name, number}'

# GO 2 — Meta: the on-screen cue the viewer watches fire
curl -s -X POST \
  "http://localhost:8000/programmer/go?cue_list_id=$PLAYBACK_LIST&show_id=$SHOW_ID" \
  | jq '{name, number, http_ok}'

# GO 3 — Wrap: OBS stops, Ardour saves
curl -s -X POST \
  "http://localhost:8000/programmer/go?cue_list_id=$PLAYBACK_LIST&show_id=$SHOW_ID" \
  | jq '{name, number}'
```

Check what was logged:

```bash
curl -s "http://localhost:8000/db/shows/$SHOW_ID/logs" \
  | jq '.[] | {triggered_at, notes}'
```

If you need to re-run from the top, reset the pointer first:

```bash
curl -s -X POST "http://localhost:8000/programmer/reset" | jq .
```

---

## Block 8 — Export for post-production

**Kdenlive (video edit):** generates an MLT XML project with OBS recording files
matched to CueLog timestamps as timeline clips.

```bash
curl -s "http://localhost:8000/recorder/export/mlt?show_id=$SHOW_ID" \
  | jq '.mlt' -r > project.mlt
# Open project.mlt in Kdenlive
```

Each `<producer>` in the MLT file has a `resource=` path taken from
`obs-output-dir` in `show.toml`. If the directory was empty at export time,
producers are emitted as stubs with a `<!-- TODO -->` comment so the structure
is valid and media can be linked manually.

**Ardour (audio mix):** the session XML generated in Block 6 pre-positions
VO clips on a Narration track at 48 kHz offsets derived from each clip's
actual sample length.

```bash
# Re-export if you added or re-generated WAV files after the initial export
curl -s "http://localhost:8000/voicer/export/ardour?script_id=$SCRIPT_ID" \
  | jq '{file, tracks}'
```

---

## Cue notes JSON reference

| Action | Key | When to use |
| ------ | --- | ----------- |
| Switch OBS scene | `"http": {"method": "POST", "path": "/recorder/scene", "params": {"scene": "…"}}` | OBS WebSocket is not OSC; use HTTP to reach ShowRecorder |
| Start / stop OBS recording | `"http": {"method": "POST", "path": "/recorder/record", "params": {"action": "start"}}` | Same — REST bridge to OBS |
| Ardour transport play | `"osc": {"address": "/ardour/transport_play", "args": []}` | Ardour's native OSC API |
| Ardour transport stop | `"osc": {"address": "/ardour/transport_stop", "args": []}` | — |
| CueLog label | `"scene": "Human-readable label"` | Written to `CueLog.notes`; appears in the MLT export |

Any cue can combine `"http"` and `"osc"` in one notes object to fire both
systems on a single GO.
