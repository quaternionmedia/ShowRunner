# Tools

The following are the built-in plugins included with ShowRunner. These are designed to cover a wide range of use cases for live productions, but the plugin architecture allows users to choose which elements to include and enables users to build their own.

## Shipped Plugins

These plugins are fully implemented and available out of the box.

### **ShowDashboard**

Show selector and control dashboard home page at `/`.

### **ShowScripter**

Script viewer (Fountain, plain text) with inline cue placement at `/script`.  Cues can be dragged to any character position in the script; right-click edits name, number, and layer in place.

### **ShowPrinter**

PDF export of scripts with cue annotations, mounted at `/export` (API-only).

### **ShowProgrammer**

Sequential cue dispatcher at `/programmer`.  Fires cues one-by-one on GO, dispatching OSC messages to Ardour (or any OSC target) and HTTP requests to other ShowRunner endpoints such as ShowRecorder.  Maintains a per-session cue pointer, wall clock, show elapsed, and per-cue timer.  Space bar fires GO.

### **ShowRecorder**

OBS Studio integration via WebSocket v5 at `/recorder`.  Switches scenes and controls recording; stores timing data in CueLog; exports a Kdenlive-compatible MLT XML project for post-production assembly.  Gracefully degrades when OBS is not running.

### **ShowVoicer**

Kokoro TTS voice-over generator at `/voicer`.  Parses `NARRATOR (V.O.)` blocks from Fountain scripts and renders numbered WAV files.  Exports an Ardour session XML with clips pre-positioned on a Narration track.  Requires the `av` dependency group (`uv sync --group av`).

### **ShowAdmin**

Web-based database admin panel at `/admin` using SQLAdmin _(requires `admin` group: `uv sync --group admin`)_.

---

## Planned Plugins

The following plugins are on the roadmap. They can be enabled or replaced with custom implementations via the plugin architecture.

### **ShowDesigner**

Design and organise cues based on the parsed script, including setting up cue layers for specific integration with other tools.

### **ShowMixer**

Operate sound mixers — monitor and control channels and effects during a performance (Behringer, Allen & Heath, etc.).

### **ShowLighter**

Integrate cues directly with lighting control systems for design and performance (ETC Eos, Chamsys, MA Lighting, etc.).

### **ShowManager**

Stage Manager view for triggering cues and tracking performance state during a live show.

### **ShowStopper**

Stopwatch with logging and cue-timing features for live performances.

### **ShowPrompter**

Teleprompter application that can display scripts and cues for performers and crew.

### **ShowComms**

Communication tool for crew members to coordinate during a performance, including messaging and cue notifications.

### **ShowCmd**

Command-line and TUI interface to the running server.

---

## Custom Plugins

The plugin architecture allows users to build and integrate their own custom plugins for specific tools, integrations, or workflows. Custom plugins can be developed independently and shared with the community, and can be loaded and unloaded at runtime without restarting the server.

See the [Cookbook](../cookbook/plugins.md) for examples and guides on building custom plugins.
