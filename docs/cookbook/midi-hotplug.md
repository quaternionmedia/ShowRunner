# Testing MIDI hotplug

ShowMidi reopens a connection when its device comes back (see the **Hotplug**
notes in [Plugins](../ref/plugins.md#showmidi)). Two scripts test that on
Windows: one drives a virtual device unattended, the other watches a real one
while you plug it by hand. Both start their own `sr start` on a scratch show,
on port 18010 by default, and send CC and panic messages only.

## Unattended, with a virtual device

This needs [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html)
installed; the script uses its driver to create and close a port.

```bash
uv sync --extra midi
uv run python scripts/midi/hotplug.py --interval 2.0 --phases cycles,flaps,fast
```

| Phase | What it does | Passes when |
| ----- | ------------ | ----------- |
| `cycles` | Unplugs and replugs, waiting for each status change. | Every replug reconnects, a CC into the input comes back out as a panic, and the server stays up. |
| `flaps` | Random up and down spells, many shorter than the interval. Then one steady plug. | The server survives and the steady plug works end to end. |
| `fast` | Unplugs and replugs inside one interval. | The stale connection is noticed. It currently fails; see below. |

The script prints one JSON line per step and exits 1 if a phase failed.

## By hand, with a real device

```bash
uv run python scripts/midi/watch.py "Launchkey 49" --interval 2.0
```

Pass the device's port name without its trailing number. The script prints a
line when a connection opens or closes, when any key or knob reaches the input,
when rtmidi reports reopening a half-closed input, and when the server exits
or stops answering.

1. Press a key, and check that it arrives.
2. Unplug, wait one interval, replug, wait, and press a key. Repeat a few times.
3. Unplug and replug as fast as you can, then press a key.

## Results, 2026-10-11

Windows 11 (10.0.26200), Python 3.12.10, python-rtmidi 1.5.8 (RtMidi 5.0.0,
WinMM).

**At `f8ae176`, before the fix:**

| Device | Interval | Result |
| ------ | -------- | ------ |
| Virtual | 0.5 s | Cycle 1 passes. Cycle 2 reconnects with its input dead, and the next unplug crashes the process with `0xC0000374`. |
| Launchkey 49 | 2 s | The first replug logs `MidiInWinMM::openPort: a valid connection already exists!` and no input arrives. The next unplug hangs the server: the process stays alive but stops answering HTTP. |

**With the fix that retires a client whose close fails:**

| Device | Interval | Result |
| ------ | -------- | ------ |
| Virtual | 0.5 s | 5 of 5 cycles pass. 40 flaps pass, recovering in 0.48 s. |
| Launchkey 49 | 2 s | 3 of 3 cycles pass, with input after each. 2 of 2 quick wiggles are detected. No crash and no hang. |

**On both builds:** the `fast` phase at 2 s fails 5 of 5 times. Status reads
connected while input and output are dead, until the next unplug that lasts
longer than an interval. The Launchkey's USB re-enumeration outlasted the
interval, so this did not occur by hand.
