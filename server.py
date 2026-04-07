"""GPU show web server. Serves the UI, video files, start/stop, and model switching."""
import os, json, signal, subprocess, time, urllib.request
from pathlib import Path
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import HTMLResponse, JSONResponse, FileResponse
from starlette.staticfiles import StaticFiles
import uvicorn

ROOT = Path(__file__).parent
VIDEOS = ROOT / "videos"
LOGS = ROOT / "logs"
INDEX = ROOT / "index.json"
PIDFILE = ROOT / "runner.pid"
STOP = ROOT / "stop.flag"
STEERING = ROOT / "steering.txt"
RUNNER = ROOT / "runner.py"
LLAMA_BIN = "/home/amiller/installing/ollama/llama.cpp-master/build/bin/llama-server"
NVME = "/media/amiller/fractal-nvme2/gguf-models"

MODELS = {
    "gemma4-26b": {
        "label": "Gemma 4 26B-A4B (MoE)",
        "model": f"{NVME}/google_gemma-4-26B-A4B-it-Q4_K_M.gguf",
        "ctx": 131072, "fa": "on", "kv": "q4_0",
    },
    "qwen3.5-27b": {
        "label": "Qwen3.5 27B Dense",
        "model": f"{NVME}/Qwen_Qwen3.5-27B-IQ4_XS.gguf",
        "ctx": 131072, "fa": "on", "kv": "q4_0",
    },
}

def runner_pid():
    if not PIDFILE.exists(): return None
    try:
        pid = int(PIDFILE.read_text().strip())
        os.kill(pid, 0)  # alive?
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        return None

def llama_pids():
    """Find any running llama-server processes."""
    out = subprocess.run(["pgrep", "-f", "llama-server --model"],
                         capture_output=True, text=True).stdout.strip()
    return [int(p) for p in out.split() if p.isdigit()]

def current_model_id():
    """Inspect /props to determine which model is currently loaded."""
    try:
        r = json.loads(urllib.request.urlopen("http://10.141.207.1:8081/props", timeout=2).read())
        path = r.get("model_path", "") or r.get("default_generation_settings", {}).get("model", "")
        for mid, cfg in MODELS.items():
            if cfg["model"] in path or os.path.basename(cfg["model"]) in path:
                return mid
    except Exception:
        pass
    return None

def start_llama(model_id):
    """Start llama-server for a given model id. Returns pid or None."""
    cfg = MODELS.get(model_id)
    if not cfg: return None
    log = open(LOGS / f"llama_{model_id}_{int(time.time())}.log", "w")
    cmd = [LLAMA_BIN, "--model", cfg["model"],
           "--ctx-size", str(cfg["ctx"]), "--n-gpu-layers", "99",
           "--host", "10.141.207.1", "--port", "8081",
           "--flash-attn", cfg["fa"], "--cache-type-k", cfg["kv"], "--cache-type-v", cfg["kv"],
           "--slot-save-path", "/tmp/llama-slots"]
    p = subprocess.Popen(cmd, stdout=log, stderr=log, start_new_session=True)
    return p.pid

def wait_health(timeout=120):
    """Block until llama-server reports {status:ok}."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = json.loads(urllib.request.urlopen("http://10.141.207.1:8081/health", timeout=2).read())
            if r.get("status") == "ok": return True
        except Exception: pass
        time.sleep(2)
    return False

async def index(request):
    return HTMLResponse((ROOT / "index.html").read_text())

async def api_status(request):
    pid = runner_pid()
    return JSONResponse({"running": pid is not None, "pid": pid})

async def api_videos(request):
    if INDEX.exists():
        data = json.loads(INDEX.read_text())
    else:
        data = {"videos": []}
    return JSONResponse(data)

async def api_start(request):
    if runner_pid():
        return JSONResponse({"ok": False, "error": "already running"})
    if STOP.exists(): STOP.unlink()
    log = open(LOGS / f"runner_{int(time.time())}.log", "w")
    subprocess.Popen(["python3", "-u", str(RUNNER)], stdout=log, stderr=log,
                     start_new_session=True)
    time.sleep(0.3)
    return JSONResponse({"ok": True, "pid": runner_pid()})

async def api_stop(request):
    pid = runner_pid()
    if not pid:
        return JSONResponse({"ok": False, "error": "not running"})
    STOP.touch()  # graceful flag (loop checks at top of iteration)
    try: os.kill(pid, signal.SIGTERM)
    except ProcessLookupError: pass
    return JSONResponse({"ok": True})

async def api_logs(request):
    logs = sorted(LOGS.glob("runner_*.log"))
    if not logs: return JSONResponse({"lines": []})
    text = logs[-1].read_text()
    return JSONResponse({"lines": text.splitlines()[-40:]})

async def api_clear(request):
    """Clear video index but keep files (or remove them)."""
    if INDEX.exists():
        INDEX.write_text(json.dumps({"videos": []}))
    return JSONResponse({"ok": True})

async def api_steering_get(request):
    text = STEERING.read_text() if STEERING.exists() else ""
    return JSONResponse({"text": text})

async def api_steering_set(request):
    body = await request.json()
    text = (body.get("text") or "").strip()[:500]
    if text:
        STEERING.write_text(text)
    elif STEERING.exists():
        STEERING.unlink()
    return JSONResponse({"ok": True, "text": text})

async def api_models(request):
    return JSONResponse({
        "models": [{"id": k, "label": v["label"]} for k, v in MODELS.items()],
        "current": current_model_id(),
    })

async def api_model_switch(request):
    target = request.path_params["mid"]
    if target not in MODELS:
        return JSONResponse({"ok": False, "error": "unknown model"}, status_code=400)
    if current_model_id() == target:
        return JSONResponse({"ok": True, "noop": True, "current": target})
    # kill any running llama-server
    for pid in llama_pids():
        try: os.kill(pid, signal.SIGTERM)
        except ProcessLookupError: pass
    # wait for them to exit + free GPU
    for _ in range(20):
        if not llama_pids(): break
        time.sleep(0.5)
    new_pid = start_llama(target)
    if not new_pid:
        return JSONResponse({"ok": False, "error": "failed to start"})
    ok = wait_health(timeout=180)
    return JSONResponse({"ok": ok, "current": current_model_id() if ok else None, "pid": new_pid})

routes = [
    Route("/", index),
    Route("/api/status", api_status),
    Route("/api/videos", api_videos),
    Route("/api/start", api_start, methods=["POST"]),
    Route("/api/stop", api_stop, methods=["POST"]),
    Route("/api/logs", api_logs),
    Route("/api/clear", api_clear, methods=["POST"]),
    Route("/api/steering", api_steering_get),
    Route("/api/steering", api_steering_set, methods=["POST"]),
    Route("/api/models", api_models),
    Route("/api/model/{mid}", api_model_switch, methods=["POST"]),
    Mount("/videos", app=StaticFiles(directory=str(VIDEOS)), name="videos"),
]

app = Starlette(routes=routes)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8090, log_level="warning")
