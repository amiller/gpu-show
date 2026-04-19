#!/usr/bin/env python3
"""Merge Firefox + Chrome dumps, dedupe by id, one file per logical bucket.

Logical buckets (from the localStorage KEY_ENTRIES keys):
  shaders  -> static-shader.html + shader.html   (shared key)
  scenes   -> static-scene.html                  (Chrome only)
  shader2  -> shader2.html
  shader3  -> shader3.html  (empty)
  shader4  -> shader4.html

Firefox scopes localStorage per-file (so static-shader and shader live in
separate sqlites even though the key collides), Chrome scopes all file://
under one bucket (so they merge naturally in Chrome).
"""
import json, glob
from pathlib import Path

HERE = Path(__file__).parent
FF = HERE / "firefox"
CH = HERE / "chrome"
MERGED = HERE / "merged"
MERGED.mkdir(parents=True, exist_ok=True)

ff_files = {Path(p).stem: json.loads(Path(p).read_text()) for p in glob.glob(str(FF / "*.json"))}
ch_candidates = list(CH.glob("file*.json"))
ch_blob = json.loads(ch_candidates[0].read_text())["data"] if ch_candidates else {}

def entries(obj, js_key):
    v = obj.get(js_key, [])
    return v if isinstance(v, list) else []

sources = {
    "shaders":  [("firefox/static-shader", entries(ff_files.get("static-shader", {}), "gpushow_shaders_entries")),
                 ("firefox/shader",        entries(ff_files.get("shader", {}),        "gpushow_shaders_entries")),
                 ("chrome",                entries(ch_blob,                           "gpushow_shaders_entries"))],
    "scenes":   [("chrome",                entries(ch_blob,                           "gpushow_scenes_entries"))],
    "shader2":  [("firefox",               entries(ff_files.get("shader2", {}),       "gpushow_shader2_entries")),
                 ("chrome",                entries(ch_blob,                           "gpushow_shader2_entries"))],
    "shader3":  [("firefox",               entries(ff_files.get("shader3", {}),       "gpushow_shader3_entries")),
                 ("chrome",                entries(ch_blob,                           "gpushow_shader3_entries"))],
    "shader4":  [("firefox",               entries(ff_files.get("shader4", {}),       "gpushow_shader4_entries")),
                 ("chrome",                entries(ch_blob,                           "gpushow_shader4_entries"))],
}

print(f"{'bucket':10s} {'FF':>4s} {'CH':>4s} {'merged':>7s}  (sources)")
print("-"*60)
for bucket, sources_list in sources.items():
    by_id = {}
    per_source = []
    for label, lst in sources_list:
        per_source.append((label, len(lst)))
        for e in lst:
            if not isinstance(e, dict):
                continue
            eid = e.get("id") or f"ts:{e.get('ts')}:{e.get('title','')}"
            # prefer the first-seen version (FF listed first)
            if eid not in by_id:
                by_id[eid] = e
    merged_list = sorted(by_id.values(), key=lambda e: e.get("ts", 0))
    path = MERGED / f"{bucket}.json"
    path.write_text(json.dumps(merged_list, indent=2))
    ff = sum(n for l, n in per_source if l.startswith("firefox"))
    ch = sum(n for l, n in per_source if l.startswith("chrome"))
    print(f"{bucket:10s} {ff:>4d} {ch:>4d} {len(merged_list):>7d}  [{', '.join(f'{l}:{n}' for l,n in per_source)}]")
