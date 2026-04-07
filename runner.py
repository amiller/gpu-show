"""GPU show runner — self-prompting, generates novel animated rendering tasks.

Each iteration:
1. Asks the LLM to invent a fresh creative animated rendering task
2. Spins up a fresh container
3. Asks the model to write a Python script that produces 60 frames
4. ffmpeg's frames into an mp4 with title overlay
5. Updates index
"""
import json, time, os, sys, signal, subprocess, urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
VIDEOS = f"{ROOT}/videos"
FRAMES = f"{ROOT}/frames"
LOGS = f"{ROOT}/logs"
INDEX = f"{ROOT}/index.json"
PIDFILE = f"{ROOT}/runner.pid"
STOP = f"{ROOT}/stop.flag"
STEERING = f"{ROOT}/steering.txt"
STATE = f"{ROOT}/state.json"

API_URL = os.environ.get("LLAMA_URL", "http://10.141.207.1:8084")

TOOLS = [{"type": "function", "function": {
    "name": "run_command", "description": "Run shell command in the Ubuntu container.",
    "parameters": {"type": "object", "properties": {
        "command": {"type": "string"}}, "required": ["command"]}}}]

META_PROMPT = """Invent a fresh creative animated rendering task. It should be visually striking and produce a SHORT 2-second animation as 60 PNG frames.

Constraints:
- Implementable in Python with pip-installable libraries (numpy, Pillow, matplotlib, scipy, PyOpenGL+osmesa, perlin-noise, noise)
- Frames at least 384x384, 60 of them, each different (animation evolves smoothly)
- Should be ~2 seconds of motion at 30fps
- Visually distinctive — generative art, particle systems, fluid sims, fractals zooming, math visualizations, procedural patterns, agent-based, cellular automata, attractors, flow fields, anything cool

DO NOT REPEAT recent tasks: {recent}
{steering}
Reply with EXACTLY this format and nothing else:
TITLE: <3-6 word title>
SPEC: <one paragraph describing what to render — colors, motion, math, structure>"""

def chat(messages, max_tokens=12000, with_tools=True):
    payload = {"model": "test", "messages": messages,
               "temperature": 0.7, "max_tokens": max_tokens,
               "chat_template_kwargs": {"enable_thinking": False}}
    if with_tools: payload["tools"] = TOOLS
    body = json.dumps(payload).encode()
    req = urllib.request.Request(f"{API_URL}/v1/chat/completions", body,
                                 {"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=600).read())
    return resp["choices"][0]["message"]

def get_recent_titles(n=8):
    if not os.path.exists(INDEX): return []
    data = json.loads(open(INDEX).read())
    return [v.get("title", v.get("name", "?")) for v in data["videos"][:n]]

def read_steering():
    if not os.path.exists(STEERING): return ""
    s = open(STEERING).read().strip()
    return s

def write_state(stage, **kwargs):
    """Publish current runner state for the UI to poll."""
    try:
        with open(STATE, "w") as f:
            json.dump({"stage": stage, "ts": int(time.time()), **kwargs}, f)
    except Exception: pass

def generate_task():
    """Ask the LLM to invent a new task. Returns (title, spec, steering_used)."""
    recent = get_recent_titles()
    recent_str = ", ".join(f'"{t}"' for t in recent) if recent else "(none yet)"
    steer = read_steering()
    steering_block = f"\nThe user is currently steering toward this theme/aesthetic (treat as a soft nudge, NOT a hard requirement — keep variety): {steer}\n" if steer else ""
    msg = chat([{"role": "user", "content": META_PROMPT.format(recent=recent_str, steering=steering_block)}],
               max_tokens=600, with_tools=False)
    text = (msg.get("content", "") or msg.get("reasoning_content", "") or "").strip()
    title = "untitled"
    spec = text
    for line in text.splitlines():
        if line.upper().startswith("TITLE:"):
            title = line.split(":", 1)[1].strip().strip('"').strip()
        elif line.upper().startswith("SPEC:"):
            spec = line.split(":", 1)[1].strip()
    title = title[:60] or "untitled"
    return title, spec, steer

def slug(s):
    return "".join(c.lower() if c.isalnum() else "_" for c in s)[:40].strip("_")

def make_task_prompt(title, spec):
    return f"""Implement this animated rendering task and produce a video.

TASK: {title}
SPEC: {spec}

Requirements:
- Save EXACTLY 60 PNG frames to /tmp/frames/f_000.png through /tmp/frames/f_059.png (zero-padded)
- Each frame at least 384x384 pixels
- Frames must EVOLVE smoothly across the 60 frames so that playing them at 30fps shows continuous animation
- Write your code in /tmp/render.py
- Install any pip packages you need first (numpy, Pillow, matplotlib, scipy, PyOpenGL, etc.)
- Run the script and verify the frames exist with `ls /tmp/frames | wc -l`

The container is fresh Ubuntu 22.04 with python3 and pip pre-installed. Begin by creating /tmp/frames directory."""

def run_in(container, cmd, timeout=180):
    try:
        r = subprocess.run(["docker", "exec", container, "bash", "-c", cmd],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"timeout after {timeout}s\nexit_code: 124"
    out = r.stdout[-1500:]; err = r.stderr[-800:]
    return f"stdout: {out}\nstderr: {err}\nexit_code: {r.returncode}"

def get_model_name():
    try:
        r = json.loads(urllib.request.urlopen("http://10.141.207.1:8081/props", timeout=5).read())
        mn = r.get("model_path", "") or r.get("default_generation_settings", {}).get("model", "")
        return os.path.basename(mn).replace(".gguf", "") if mn else "unknown"
    except Exception:
        return "unknown"

def execute_task(title, spec):
    """Run agent loop to produce frames. Returns dict."""
    container = f"gshow-{int(time.time())}"
    print(f"[exec] {title}", flush=True)
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", container, "ubuntu:22.04",
                    "sleep", "1800"], capture_output=True)
    # base setup
    subprocess.run(["docker", "exec", container, "bash", "-c",
        "apt-get update -qq 2>/dev/null && apt-get install -y -qq python3 python3-pip libosmesa6-dev libglu1-mesa-dev 2>/dev/null && mkdir -p /tmp/frames"],
        capture_output=True, timeout=240)

    messages = [
        {"role": "system", "content": "You are an expert creative coder and computational artist. Use the run_command tool to install packages, write code, and run it. Be efficient — write the whole script at once and run it."},
        {"role": "user", "content": make_task_prompt(title, spec)}]
    tool_calls = errors = 0
    actions = []
    start = time.time()

    for turn in range(15):
        if os.path.exists(STOP): break
        try:
            msg = chat(messages)
        except Exception as e:
            actions.append(f"API error: {e}")
            errors += 1; break
        if msg.get("tool_calls"):
            messages.append(msg)
            for tc in msg["tool_calls"]:
                args = tc["function"]["arguments"]
                if isinstance(args, str):
                    try: args = json.loads(args)
                    except json.JSONDecodeError:
                        messages.append({"role": "tool", "tool_call_id": tc["id"], "content": "Error: bad args"})
                        errors += 1; continue
                cmd = args.get("command", "")
                tool_calls += 1
                short = cmd.split('\n')[0][:90]
                actions.append(short)
                print(f"  T{turn+1}: {short}", flush=True)
                output = run_in(container, cmd)
                if "exit_code: 1" in output or "exit_code: 2" in output:
                    errors += 1
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": output})
        else:
            if tool_calls > 0: break
            break

    elapsed = time.time() - start

    # Pull frames
    ts = int(time.time())
    out_dir = f"{FRAMES}/{slug(title)}_{ts}"
    os.makedirs(out_dir, exist_ok=True)
    cp = subprocess.run(["docker", "cp", f"{container}:/tmp/frames/.", out_dir],
                        capture_output=True)
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)
    frames = sorted([f for f in os.listdir(out_dir) if f.endswith(".png")])
    print(f"  -> {len(frames)} frames pulled", flush=True)

    return {"title": title, "spec": spec, "tool_calls": tool_calls, "errors": errors,
            "elapsed": elapsed, "frames_dir": out_dir, "n_frames": len(frames),
            "actions": actions[-8:], "ts": ts}

def make_video(result, model):
    if result["n_frames"] < 5:
        return None
    title = result["title"]
    sub = f"{model}   {result['n_frames']}f   {result['tool_calls']}c   {result['elapsed']:.0f}s"
    out = f"{VIDEOS}/{slug(title)}_{result['ts']}.mp4"
    def esc(s): return s.replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'").replace(",", r"\,").replace("[", r"\[").replace("]", r"\]")
    drawtext = (
        f"drawtext=text='{esc(title)}':fontcolor=white:fontsize=22:"
        f"x=20:y=20:box=1:boxcolor=black@0.55:boxborderw=8,"
        f"drawtext=text='{esc(sub)}':fontcolor=white:fontsize=14:"
        f"x=20:y=h-40:box=1:boxcolor=black@0.55:boxborderw=6")
    # Try standard 60-frame path first; if fewer frames, use what we got
    # Use glob pattern to handle non-sequential frames
    inputs = sorted([f for f in os.listdir(result["frames_dir"]) if f.endswith(".png")])
    # write a concat file for max compatibility
    list_file = f"{result['frames_dir']}/_list.txt"
    with open(list_file, "w") as f:
        for fn in inputs:
            f.write(f"file '{result['frames_dir']}/{fn}'\nduration 0.0333\n")
        # ffmpeg quirk — repeat last frame
        if inputs:
            f.write(f"file '{result['frames_dir']}/{inputs[-1]}'\n")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
           "-vf", f"scale=768:-2,{drawtext}",
           "-pix_fmt", "yuv420p", "-r", "30", "-c:v", "libx264", "-preset", "veryfast",
           "-movflags", "+faststart", out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[ffmpeg fail] {r.stderr[-400:]}", flush=True)
        return None
    return out

def append_index(result, video, model):
    idx = json.loads(open(INDEX).read()) if os.path.exists(INDEX) else {"videos": []}
    idx["videos"].insert(0, {
        "ts": result["ts"], "title": result["title"], "spec": result.get("spec", "")[:300],
        "steering": result.get("steering", ""),
        "video": os.path.basename(video) if video else None,
        "passed": video is not None, "tool_calls": result["tool_calls"],
        "errors": result["errors"], "elapsed": round(result["elapsed"], 1),
        "n_frames": result["n_frames"], "model": model, "actions": result["actions"],
    })
    idx["videos"] = idx["videos"][:50]
    with open(INDEX, "w") as f: json.dump(idx, f, indent=2)

def main():
    with open(PIDFILE, "w") as f: f.write(str(os.getpid()))
    if os.path.exists(STOP): os.remove(STOP)

    def cleanup(*a):
        if os.path.exists(PIDFILE): os.remove(PIDFILE)
        sys.exit(0)
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    print(f"[runner] up, pid={os.getpid()}", flush=True)
    write_state("idle")
    while True:
        if os.path.exists(STOP):
            print("[runner] stop flag, exiting", flush=True)
            write_state("stopped")
            cleanup()
        write_state("waiting_for_model")
        ok = False
        for _ in range(60):
            if os.path.exists(STOP): cleanup()
            try:
                r = json.loads(urllib.request.urlopen("http://10.141.207.1:8081/health", timeout=2).read())
                if r.get("status") == "ok": ok = True; break
            except Exception: pass
            time.sleep(2)
        if not ok:
            print("[runner] llama-server not healthy, retrying", flush=True)
            time.sleep(3); continue
        try:
            model = get_model_name()
            steer_now = read_steering()
            write_state("generating", model=model, steering=steer_now)
            print(f"[gen] generating prompt... (model={model}, steer={steer_now!r})", flush=True)
            title, spec, steering_used = generate_task()
            print(f"[gen] {title} :: {spec[:120]}", flush=True)
            write_state("executing", model=model, title=title, spec=spec[:200], steering=steering_used)
            result = execute_task(title, spec)
            result["steering"] = steering_used
            write_state("encoding", model=model, title=title, steering=steering_used,
                        n_frames=result["n_frames"])
            video = make_video(result, model)
            append_index(result, video, model)
            print(f"[done] {title} pass={video is not None} {result['elapsed']:.0f}s frames={result['n_frames']}", flush=True)
            write_state("idle", last_title=title, last_passed=video is not None)
        except Exception as e:
            import traceback
            print(f"[error] {e}\n{traceback.format_exc()}", flush=True)
            write_state("error", error=str(e)[:200])
        time.sleep(2)

if __name__ == "__main__":
    main()
