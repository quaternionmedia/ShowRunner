# Tools

The following are the built-in plugins included with ShowRunner. These are designed to cover a wide range of use cases for live productions, but the plugin architecture allows users to choose which elements to include and enables users to build their own.

## Built-in Plugins

### **ShowDashboard**

Show selector and control dashboard home page

### **ShowScripter**

Script viewer (PDF, Fountain, etc.) with inline cue placement mounted at `/script`

### **ShowAdmin**

Web-based database admin panel mounted at `/admin` _(requires install `admin` group)_

### **ShowDesigner**

Allows users to design cues based on the parsed script, including setting up cue layers for specific integration with other tools

### **ShowProgrammer**

Synchronization with QLab and other tools to automatically create and label cues from a script

### **ShowMixer**

Operates sound mixers to monitor and control channels and effects during a performance (Behringer, Allen & Heath, etc.)

### **ShowLighter**

Integrates cues directly with lighting control systems for design and performance (ETC Eos, Chamsys, MA Lighting, etc.)

### **ShowManager**

Designed for Stage Managers to manage cues during a live performance, including triggering cues

### **ShowStopper**

A stopwatch with helpful features for live performances, such as logging and cue timing

### **ShowPrompter**

A teleprompter application that can display scripts and cues for performers and crew

### **ShowComms**

A communication tool for crew members to coordinate during a performance, including messaging and cue notifications

### **ShowCmd**

A command-line interface to interact with the system with a CLI or TUI

### **ShowRecorder**

A tool for archiving, annotating, and reviewing rehearsals and performances, including cue logs and performance notes

## Custom Plugins

The plugin architecture allows users to build and integrate their own custom plugins for specific tools, integrations, or workflows. Custom plugins can be developed independently and shared with the community, and can be loaded and unloaded at runtime without restarting the server.

See the [Cookbook](../cookbook/plugins.md) for examples and guides on building custom plugins.
