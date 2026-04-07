# gpu-show

A self-prompting render loop for local LLMs. Each cycle the model invents a new
animated rendering task, writes the code, runs it in a sandbox, and the result
is ffmpeg'd into an mp4 you can watch in a web UI.

It exists because watching an LLM's GPU sit at 0% is sad, and watching it
generate weird little fractals is not.

## Workflow

```
                 ┌──────────┐    1. invent task         ┌──────────┐
                 │  runner  │ ────────────────────────► │ llama-   │
                 │  (loop)  │ ◄──────────────────────── │ server   │
                 └────┬─────┘    2. agent: write code   │ (CUDA    │
                      │             + run in sandbox    │  12.8)   │
                      │                                  └──────────┘
                      │ 3. pull /tmp/frames/*.png
                      ▼
                 ┌──────────┐
                 │  ffmpeg  │  → videos/<slug>_<ts>.mp4
                 └────┬─────┘     (60 frames @ 30fps + title overlay)
                      │
                      ▼
                 ┌──────────┐
                 │ index.   │  ← server.py reads
                 │  json    │
                 └────┬─────┘
                      │
                      ▼
                 ┌──────────┐
                 │ index.   │  → http://localhost:8090
                 │  html    │     (autorefresh + autoplay)
                 └──────────┘
```

Each iteration:

1. **Generate a task** — runner asks the LLM (with the last 8 titles as
   "don't repeat these" + any active steering) to invent a fresh creative
   rendering task. Reply format is `TITLE: ... / SPEC: ...`.
2. **Execute** — fresh `ubuntu:22.04` Docker container, model gets a tool-use
   loop, has to install pip packages, write `/tmp/render.py`, produce 60 PNG
   frames in `/tmp/frames/`.
3. **Encode** — frames pulled out, ffmpeg sequences them at 30fps with a title
   + stats overlay.
4. **Update** — appended to `index.json`, the web UI picks it up on its next
   poll (every 4s).

## Run it

```bash
# 1. start a llama-server with one of the configured models on :8081
# 2. start the web server (serves UI on :8090)
python3 server.py
# 3. open http://localhost:8090 and click Start
```

The web UI controls everything from there:

- **Start / Stop** — graceful control of the runner loop
- **Model dropdown** — switch between configured models live (kills the
  current llama-server, starts the new one, runner pauses + resumes)
- **show: last N** — limit grid size; files stay on disk regardless
- **steer:** — soft prompt nudge (see below)
- **Logs** — tail of the runner log
- **Clear** — wipes the index (files preserved)

## Steering

The steering box appends a soft nudge to the meta-prompt:

> The user is currently steering toward this theme/aesthetic (treat as a soft
> nudge, NOT a hard requirement — keep variety): **{your text}**

It influences the *next* generated task. Examples:

- `underwater bioluminescence, slow drifting`
- `MC Escher tessellations`
- `8-bit retro CRT effects`
- `cellular automata, black and white`

Stored in `steering.txt`. Empty string clears it.

## File layout

```
gpu-show/
├── README.md
├── runner.py        # the loop — picks/runs/captures tasks
├── server.py        # starlette API on :8090
├── index.html       # single-page UI (no build step)
├── index.json       # video metadata (capped at 50)
├── steering.txt     # current steering text (auto-managed)
├── runner.pid       # present iff runner alive
├── stop.flag        # graceful stop signal (deleted on start)
├── frames/          # raw PNG frames per task
├── videos/          # ffmpeg outputs
└── logs/            # runner + server + llama-server logs
```

`frames/`, `videos/`, and `logs/` are gitignored. They grow forever — clean
them out if disk fills up.

## API

| Method | Path | What |
|--------|------|------|
| GET    | `/`               | UI |
| GET    | `/api/status`     | runner alive? |
| GET    | `/api/videos`     | full index |
| POST   | `/api/start`      | spawn runner |
| POST   | `/api/stop`       | SIGTERM runner + flag |
| POST   | `/api/clear`      | wipe index (keeps files) |
| GET    | `/api/logs`       | last 40 runner log lines |
| GET    | `/api/models`     | list configured models + current |
| POST   | `/api/model/{id}` | switch llama-server to a different model |
| GET    | `/api/steering`   | current steering text |
| POST   | `/api/steering`   | `{text: "..."}` (empty clears) |
| GET    | `/videos/<file>`  | static mp4 |

## Adding a model

In `server.py`, add to `MODELS`:

```python
MODELS = {
    "my-new-model": {
        "label": "Display name in dropdown",
        "model": "/path/to/file.gguf",
        "ctx": 131072, "fa": "on", "kv": "q4_0",
    },
    ...
}
```

The switcher uses the CUDA 12.8 build at
`/home/amiller/installing/ollama/llama.cpp-master/build/bin/llama-server`. The
model is loaded with `--n-gpu-layers 99`, flash attention on, KV cache
quantized.

## Adding a task type

Currently every task is "produce 60 frames in /tmp/frames" — the model invents
the rest. To add a fundamentally different task type (e.g. WebGL captured by
chromium), edit `runner.py`:

- Override `make_task_prompt()` for the new shape
- Override `execute_task()` if the harness needs different setup or output
  collection
- The mp4 step works on any folder of PNGs

## Caveats

- Frames are pulled out of the container before it's destroyed; if the model
  writes them somewhere else the run fails.
- The model occasionally produces ~5 frames instead of 60 (parameterized the
  loop wrong). The video will still build from whatever was produced; the
  card shows `Nf` so you can see it.
- Container has full internet, runs as root. Don't point this at anything
  hostile.
- Runner has a 15-turn limit per task; if the model can't ship in 15 calls
  it gets cut off and the cycle moves on.
