# API

Common patterns and recipes for working with ShowRunner.

---

## API – Using the REST Endpoints

Once the server is started (`sr start`), all plugin routes are available.

### List shows via HTTP

```bash
curl http://localhost:8000/db/shows
```

### Create a show via HTTP

```bash
curl -X POST "http://localhost:8000/db/shows?name=Hamlet&venue=Globe%20Theatre"
```

---

### Check a plugin's status

Every built-in plugin exposes a health/index endpoint at its prefix:

```bash
curl http://localhost:8000/recorder/    # ShowRecorder
curl http://localhost:8000/cmd/         # ShowCmd
curl http://localhost:8000/mixer/       # ShowMixer
```

### Web UI pages

The NiceGUI-powered pages are browser-only (not REST):

| URL                          | Plugin        | Description                                      |
| ---------------------------- | ------------- | ------------------------------------------------ |
| http://localhost:8000/       | ShowDashboard | Show selector and control dashboard              |
| http://localhost:8000/script | ShowScripter  | Script viewer with drag-and-drop cue placement   |
| http://localhost:8000/admin  | ShowAdmin     | SQLAdmin CRUD interface _(requires admin group)_ |

---

## Admin Panel

The admin panel requires the optional `admin` dependency group:

```bash
uv sync --group admin
sr start
# Then open http://localhost:8000/admin
```

The admin panel provides full CRUD for Shows, Scripts, CueLists, Cues, Actors, CueLogs, and Config.
