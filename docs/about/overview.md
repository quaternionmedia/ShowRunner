# Overview

**ShowRunner** is a modular, plugin-based system for managing live performances.

## Architecture

The core interface is a FastAPI application that serves as the backend for various tools and plugins.

Each plugin is a self-contained module that registers with the core application through a well-defined set of hooks, allowing for dynamic loading and unloading at runtime without restarting the server.

A default frontend is provided with NiceGUI, offering a responsive and user-friendly interface entirely in python. Plugins can also provide their own custom frontends or APIs as needed.

## Who is it for?

**ShowRunner** is designed by and for:

- stage managers
- designers
- directors
- performers
- musicians
- conductors
- sound mixers
- stage crew
- production teams

... and anyone involved in live productions!

## What types of productions is it for?

**ShowRunner** is designed to be used in any production, including:

### Theatre

- Manage scripts, cues, and cue lists.
- Create cues from scripts and synchronize with tools like QLab.
- Generate DCA assignments from scripts and automatically create labeled cues
- Integrate with lighting equipment (ETC Eos, Chamsys, etc) to synchronize cue lists from scripts directly to the console, and control lighting during performances.

### Music

- Create and share setlists
- Share sheet music, manage arrangements
- Coordinate page turns for sheet music with musicians and crew
- Integrate with sound mixers (Behringer, Allen & Heath, etc.) to monitor and control channels and effects during a performance

### Dance

- Clip music cues and set sync points for dance performances
- Synchronize music cues with lighting and other effects

### Festivals

- Set schedules and track timing across multiple stages
- Communicate with performers and crew
- Create dashboards to monitor and control multiple elements of a production

### Corporate events

- Create teleprompter presentations from scripts
- Manage AV elements from multiple sources

### Film and TV production

- Parse scripts to generate location breakdowns
- Organize and coordinate shooting schedules
- Integrate production teams, including live broadcasts
- React to events and create fail-safes for live broadcasts
