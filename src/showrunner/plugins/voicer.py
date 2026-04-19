"""ShowVoicer - Kokoro TTS voice-over generator.

Extracts NARRATOR (V.O.) dialogue blocks from a Fountain script stored
in the ShowRunner database, generates WAV audio files with Kokoro's
local neural TTS engine, and exports them for use in Ardour or Kdenlive.

No API key required — Kokoro runs entirely on the local machine.
The model weights (~300 MB) are downloaded automatically on first use.

Dependencies (optional — graceful degradation when absent):
  pip install kokoro soundfile

Output files are written to the ``output-dir`` configured in show.toml
(default: ``./exports/narration``), named ``vo-{N}-{page}.wav``.

For Ardour: ``GET /voicer/export/ardour`` generates a session XML file
with each clip pre-positioned on a "Narration" track.

For Kdenlive: the MLT export from ShowRecorder includes these audio clips
when ``GET /voicer/export/mlt-audio`` is called first.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

import showrunner
from showrunner.models import Script

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voicer", tags=["ShowVoicer"])

# ---------------------------------------------------------------------------
# Fountain parsing
# ---------------------------------------------------------------------------

_PAGE_RE = re.compile(r"\[\[Page\s+(.+?)\]\]")
_NARRATOR_RE = re.compile(r"^NARRATOR\s*\(V\.O\.\)\s*$", re.IGNORECASE)


def parse_narrator_blocks(content: str) -> list[dict]:
    """Extract NARRATOR (V.O.) dialogue blocks from Fountain script text.

    Returns a list of dicts::

        [
            {
                "index": 1,           # 1-based block counter
                "page": "paradox",    # [[Page label]] in effect when found
                "text": "...",        # joined dialogue lines
                "line_start": 42,     # 0-based line index in source
            },
            ...
        ]
    """
    lines = content.splitlines()
    current_page: str = "intro"
    blocks: list[dict] = []
    block_index = 0

    i = 0
    while i < len(lines):
        line = lines[i]

        # Track page label
        m = _PAGE_RE.search(line)
        if m:
            current_page = m.group(1).strip()
            i += 1
            continue

        # Detect NARRATOR (V.O.) character cue
        if _NARRATOR_RE.match(line.strip()):
            line_start = i
            i += 1
            dialogue_lines: list[str] = []

            # Collect non-blank lines that follow as dialogue
            while i < len(lines):
                dl = lines[i]
                if dl.strip() == "":
                    break
                # Skip parentheticals like "(beat)" — they're stage direction, not VO
                if dl.strip().startswith("(") and dl.strip().endswith(")"):
                    i += 1
                    continue
                dialogue_lines.append(dl.strip())
                i += 1

            if dialogue_lines:
                block_index += 1
                blocks.append(
                    {
                        "index": block_index,
                        "page": current_page,
                        "text": " ".join(dialogue_lines),
                        "line_start": line_start,
                    }
                )
            continue

        i += 1

    return blocks


# ---------------------------------------------------------------------------
# Audio generation
# ---------------------------------------------------------------------------

def _get_cfg(app: Any) -> dict[str, Any]:
    """Return plugin settings from show.toml [plugins.voicer]."""
    return getattr(app, "config", None) and app.config.plugins.settings.get(
        "voicer", {}
    ) or {}


def _output_dir(app: Any) -> Path:
    cfg = _get_cfg(app)
    path = Path(cfg.get("output-dir", "./exports/narration")).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Windows: espeak-ng auto-detection
# ---------------------------------------------------------------------------

_ESPEAK_WIN_CANDIDATES = [
    r"C:\Program Files\eSpeak NG",
    r"C:\Program Files (x86)\eSpeak NG",
]


def _configure_espeak_windows() -> bool:
    """On Windows, locate espeak-ng and configure the environment for phonemizer.

    Checks common install paths and the ``PHONEMIZER_ESPEAK_PATH`` /
    ``ESPEAK_PATH`` env vars.  Adds the espeak-ng directory to ``PATH`` so
    that ``libespeak-ng.dll`` is discoverable by the phonemizer C extension.

    Returns ``True`` when espeak-ng is found or the platform is not Windows,
    ``False`` when it cannot be located (caller should warn the user).
    """
    import os
    import sys

    if sys.platform != "win32":
        return True

    # Already configured via environment
    if os.environ.get("PHONEMIZER_ESPEAK_PATH") or os.environ.get("ESPEAK_PATH"):
        return True

    for candidate_dir in _ESPEAK_WIN_CANDIDATES:
        exe = Path(candidate_dir) / "espeak-ng.exe"
        if exe.exists():
            os.environ["PHONEMIZER_ESPEAK_PATH"] = str(exe)
            # Prepend to PATH so libespeak-ng.dll is found by ctypes
            if candidate_dir not in os.environ.get("PATH", ""):
                os.environ["PATH"] = candidate_dir + os.pathsep + os.environ.get("PATH", "")
            logger.info("espeak-ng auto-detected: %s", exe)
            return True

    logger.warning(
        "espeak-ng not found on Windows — voice generation may fail.\n"
        "Install from: https://github.com/espeak-ng/espeak-ng/releases\n"
        "Or set PHONEMIZER_ESPEAK_PATH=C:\\path\\to\\espeak-ng.exe before starting ShowRunner."
    )
    return False


_pipeline: Any = None


def _get_pipeline() -> Any:
    """Return a cached KPipeline instance (loads model weights on first call).

    On Windows, espeak-ng is auto-detected and configured before the pipeline
    is constructed so that phonemizer can find it.
    """
    global _pipeline
    if _pipeline is None:
        _configure_espeak_windows()
        from kokoro import KPipeline
        _pipeline = KPipeline(lang_code="a")  # 'a' = American English
    return _pipeline


def generate_wav(text: str, voice: str, speed: float, out_path: Path) -> bool:
    """Generate a WAV file from ``text`` using Kokoro.

    Returns ``True`` on success, ``False`` when Kokoro is not installed.
    Never raises — callers log and continue.
    """
    try:
        import numpy as np
        import soundfile as sf
        _get_pipeline()  # trigger ImportError early if kokoro missing
    except ImportError:
        logger.warning("kokoro / soundfile not installed — VO generation unavailable")
        return False

    try:
        pipeline = _get_pipeline()
        chunks: list = []
        for _gs, _ps, audio in pipeline(text, voice=voice, speed=speed):
            chunks.append(audio)

        if not chunks:
            logger.warning("Kokoro returned no audio for: %r", text[:60])
            return False

        combined = np.concatenate(chunks, axis=0) if len(chunks) > 1 else chunks[0]
        sf.write(str(out_path), combined, samplerate=24000)
        logger.info("VO written: %s (%d samples)", out_path.name, len(combined))
        return True
    except Exception as exc:
        logger.error("Kokoro generation failed for %r: %s", text[:60], exc)
        return False


# ---------------------------------------------------------------------------
# Ardour session XML export
# ---------------------------------------------------------------------------

_ARDOUR_SAMPLE_RATE = 48000  # Ardour's internal sample rate
_KOKORO_SAMPLE_RATE = 24000  # Kokoro TTS output sample rate


def _wav_length_ardour_samples(path: Path) -> int:
    """Return WAV duration in Ardour (48 kHz) samples.

    Returns 0 if the file cannot be read (e.g. soundfile not installed).
    """
    try:
        import soundfile as sf
        info = sf.info(str(path))
        return int(info.frames * (_ARDOUR_SAMPLE_RATE / info.samplerate))
    except Exception:
        return 0


def _ardour_session_xml(wav_paths: list[Path], total_duration_ms: int = 62000) -> str:
    """Generate a minimal Ardour session XML with VO clips on one track.

    Positions clips evenly across the target duration — the engineer should
    fine-tune before recording.  Offsets and lengths are in Ardour samples
    (48 kHz).
    """
    sources = []
    regions = []
    clips = []

    for i, path in enumerate(wav_paths):
        src_id = 1000 + i
        reg_id = 2000 + i
        # Rough position: evenly spaced across total duration at 48 kHz
        offset_samples = int(
            (i / max(len(wav_paths), 1)) * total_duration_ms / 1000 * _ARDOUR_SAMPLE_RATE
        )
        length_samples = _wav_length_ardour_samples(path)
        sources.append(
            f'  <Source name="{path.name}" channel="0" id="{src_id}" '
            f'type="audio" flags="Writable" />'
        )
        regions.append(
            f'  <Region name="VO-{i+1}" start="0" length="{length_samples}"'
            f' position="{offset_samples}" id="{reg_id}" source-0="{src_id}" />'
        )
        clips.append(f'    <RegionView id="{reg_id}" />')

    sources_xml = "\n".join(sources)
    regions_xml = "\n".join(regions)
    clips_xml = "\n".join(clips)

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Session version="5001" name="narration">\n'
        '  <Sources>\n'
        f"{sources_xml}\n"
        "  </Sources>\n"
        '  <Regions>\n'
        f"{regions_xml}\n"
        "  </Regions>\n"
        '  <Routes>\n'
        '    <Route name="Narration" id="100" flags="AudioTrack">\n'
        '      <Playlist name="Narration.1">\n'
        f"{clips_xml}\n"
        "      </Playlist>\n"
        "    </Route>\n"
        "  </Routes>\n"
        "</Session>\n"
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/")
async def index():
    return {"plugin": "ShowVoicer", "status": "ok"}


@router.get("/lines")
async def preview_lines(script_id: int = Query(..., description="Script ID to parse")):
    """Preview the NARRATOR (V.O.) blocks that would be generated."""
    from showrunner.plugins.voicer import _app_ref
    if _app_ref is None:
        raise HTTPException(status_code=503, detail="Plugin not yet started")
    db = getattr(_app_ref, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")

    with db.session() as session:
        script = session.get(Script, script_id)
        if script is None:
            raise HTTPException(status_code=404, detail=f"Script {script_id} not found")
        content = script.content or ""

    blocks = parse_narrator_blocks(content)
    return {"script_id": script_id, "blocks": blocks}


@router.post("/generate")
async def generate(
    show_id: int = Query(..., description="Show ID"),
    script_id: int = Query(..., description="Script ID to generate VO from"),
):
    """Generate WAV files for all NARRATOR (V.O.) blocks in the script."""
    from showrunner.plugins.voicer import _app_ref
    if _app_ref is None:
        raise HTTPException(status_code=503, detail="Plugin not yet started")
    db = getattr(_app_ref, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")

    with db.session() as session:
        script = session.get(Script, script_id)
        if script is None:
            raise HTTPException(status_code=404, detail=f"Script {script_id} not found")
        content = script.content or ""

    cfg = _get_cfg(_app_ref)
    voice = cfg.get("voice", "af_heart")
    speed = float(cfg.get("speed", 1.0))
    out_dir = _output_dir(_app_ref)

    blocks = parse_narrator_blocks(content)
    results = []

    for block in blocks:
        filename = f"vo-{block['index']}-{block['page']}.wav"
        out_path = out_dir / filename
        ok = generate_wav(block["text"], voice, speed, out_path)
        results.append(
            {
                "index": block["index"],
                "page": block["page"],
                "text": block["text"],
                "file": str(out_path) if ok else None,
                "ok": ok,
            }
        )

    generated = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]

    if len(generated) == 0 and len(results) > 0:
        raise HTTPException(
            status_code=503,
            detail=(
                f"No audio generated (0/{len(results)} blocks succeeded). "
                "Install kokoro and soundfile: uv sync --group av"
            ),
        )

    response: dict = {
        "show_id": show_id,
        "script_id": script_id,
        "voice": voice,
        "generated": len(generated),
        "total": len(results),
        "files": results,
    }
    if failed:
        response["warnings"] = [
            f"Block {r['index']} ({r['page']}) failed" for r in failed
        ]
    return response


@router.post("/preview")
async def preview_line(
    script_id: int = Query(..., description="Script ID"),
    block_index: int = Query(..., description="1-based block index to preview"),
):
    """Generate audio for a single VO block (for voice audition)."""
    from showrunner.plugins.voicer import _app_ref
    if _app_ref is None:
        raise HTTPException(status_code=503, detail="Plugin not yet started")
    db = getattr(_app_ref, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")

    with db.session() as session:
        script = session.get(Script, script_id)
        if script is None:
            raise HTTPException(status_code=404, detail=f"Script {script_id} not found")
        content = script.content or ""

    blocks = parse_narrator_blocks(content)
    match = next((b for b in blocks if b["index"] == block_index), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Block {block_index} not found")

    cfg = _get_cfg(_app_ref)
    voice = cfg.get("voice", "af_heart")
    speed = float(cfg.get("speed", 1.0))
    out_dir = _output_dir(_app_ref)

    filename = f"preview-{block_index}.wav"
    out_path = out_dir / filename
    ok = generate_wav(match["text"], voice, speed, out_path)

    if not ok:
        raise HTTPException(status_code=502, detail="Kokoro not installed or generation failed")
    return {"file": str(out_path), "text": match["text"], "voice": voice}


@router.get("/files")
async def list_files():
    """List generated WAV files in the output directory."""
    from showrunner.plugins.voicer import _app_ref
    if _app_ref is None:
        raise HTTPException(status_code=503, detail="Plugin not yet started")
    out_dir = _output_dir(_app_ref)
    wavs = sorted(out_dir.glob("vo-*.wav"))
    return {
        "directory": out_dir.as_posix(),
        "files": [f.name for f in wavs],
        "count": len(wavs),
    }


@router.get("/export/ardour")
async def export_ardour(script_id: int = Query(..., description="Script ID")):
    """Generate an Ardour session XML with VO clips pre-positioned."""
    from showrunner.plugins.voicer import _app_ref
    if _app_ref is None:
        raise HTTPException(status_code=503, detail="Plugin not yet started")
    out_dir = _output_dir(_app_ref)
    wavs = sorted(out_dir.glob("vo-*.wav"))
    if not wavs:
        raise HTTPException(
            status_code=404,
            detail="No WAV files found — run POST /voicer/generate first",
        )
    xml = _ardour_session_xml(wavs)
    session_path = out_dir / "narration-session.ardour"
    session_path.write_text(xml, encoding="utf-8")
    return {"file": str(session_path), "tracks": len(wavs)}


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------

_app_ref: Any = None


class ShowVoicerPlugin:
    """Local neural TTS voice-over generator using Kokoro.

    Parses NARRATOR (V.O.) blocks from Fountain scripts stored in ShowRunner,
    generates numbered WAV files, and exports Ardour session XML for
    pre-production audio assembly.

    Configure in show.toml::

        [plugins.voicer]
        voice = "af_heart"
        speed = 1.0
        output-dir = "./exports/narration"
    """

    @showrunner.hookimpl
    def showrunner_register(self):
        return {
            "name": "ShowVoicer",
            "description": "Kokoro TTS voice-over generation from Fountain scripts",
            "version": "0.1.0",
        }

    @showrunner.hookimpl
    def showrunner_startup(self, app):
        global _app_ref
        _app_ref = app
        cfg = _get_cfg(app)
        voice = cfg.get("voice", "af_heart")
        out_dir = cfg.get("output-dir", "./exports/narration")
        logger.info("ShowVoicer ready — voice: %s, output: %s", voice, out_dir)

    @showrunner.hookimpl
    def showrunner_shutdown(self, app):
        global _app_ref
        _app_ref = None

    @showrunner.hookimpl
    def showrunner_get_routes(self):
        return router

    @showrunner.hookimpl
    def showrunner_get_commands(self):
        return []

    @showrunner.hookimpl
    def showrunner_get_nav(self):
        return None

    @showrunner.hookimpl
    def showrunner_get_status(self):
        return None
