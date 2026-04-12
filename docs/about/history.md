# History

Years before this project was called ShowRunner, its components were designed, developed and refined over a decade of theatrical and live productions. Each show presented unique challenges and opportunities for innovation, leading to the development of new features and capabilities that eventually culminated in the creation of ShowRunner as a unified platform for theatrical and live production management.

**ShowRunner** combines those tools into a single, unified platform.

## Shows

Here's a brief history of notable productions that contributed to the development of ShowRunner, and the components that led to the project:

### 2013-2016 _The Rocky Horror Show_

- Initial code experiments
- OSC integration with 3rd party tools

### 2014 _Language Archive_

- QLab integration
- Multi-camera archival video

### 2014 _African Dream_

- Sound reactive FX

### 2015 _No Exit_

- Real-time video fx engine with VLC player
- Full QLab integration

### 2017 _Cesar and Rubin_

- First fully tech integrated show
- Sound
  - Live fader control with fade cues
  - Controlled mutes for all channels
- Lights
  - Cue integration with Eos
- Video
  - Custom video rendering engine in python
  - Automatic supertitle creation in QLab
- Interactive shell interface with full show controls

### 2018 _The Wedding Singer_

- Realtime LED pixel fx engine with [Ira](https://github.com/quaternionmedia/ira)
  - Arduino + FastLED
  - DMX integration with custom circuitry
  - Multiple show modes:
    - RGB
    - Custom FX patterns with DMX color input
    - Sound reactive modes
  - Remote firmware updating

### 2020 _Holophonor_

- Plugin architecture for tools and show control
- MIDI controller integration
  - Notes, control changes, etc. to trigger cues and control parameters
  - Patch management for different environments
- Novation Launchpad integration
  - Multiple modes for different types of cues and interactions
  - Button light feedback to indicate current states
  - Multiple simultaneous controllers and state management

### 2021 _Play To The Plants_

- Realtime full duplex remote mixing, monitoring, and control
- Live remote controlled broadcasts to video streaming services

### 2022 _The Complete Works of William Shakespeare (Abridged)_

- [TheCueList](https://thecuelist.com/) app integration
  - PDF integration
  - Custom cue layers
  - Export to CSV
  - Create, configure, and label cues in QLab
  - Group cues to trigger in batches
- ETC Eos Augment3d virtual modeling for PAC
- Git revision control of show files and design documents
- Offline design mode
  - Shows can be fully designed away from the performance space
  - Remote design collaboration and pre-production work

### 2023 _Urinetown_

- Virtual Dry Tech
  - Virtual QLab, sound board, and lighting consoles
  - Full virtual set with virtual actors
  - Video chats with creative team
  - Realtime previs with multiple integrations
  - Exploring creative ideas with full tech away from the performance space

### 2024 _1776!_

- [Fountain](https://fountain.io/) script format integration
  - OCR
    - Corrections
    - Reformatting
  - Automatic generation of mute cues
    - From analyzing script
    - Custom "lookahead" values for non-speaking breaks

### 2025 _Seussical_

- [TheatreMix](https://theatremix.com/) integration
  - Auto creation and labeling of DCA assignments
  - Auto creation of QLab reference cue via OSC/MIDI
  - Multiple assignment strategies and parameters for generation
  -

### 2026 _12 Angry Men_

- [[ShowRunner]] v0.1.0
  - Unified application infrastructure
  - Plugin architecture
    - commands
    - API routes
    - events
    - custom databases
  - Web server
    - `/` Show control page
    - `/api` - show control and plugin functions
    - `/admin` - Setup and configuration
  - `sr` command line interface
    - programatic access for show management, show control, etc.
