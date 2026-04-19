#!/usr/bin/env python3
"""Extract gpu-show shader localStorage from Firefox data.sqlite files.

Firefox stores localStorage in per-origin sqlite at
.mozilla/firefox/<profile>/storage/default/<origin>/ls/data.sqlite

Values are UTF-8 text, optionally snappy-compressed (compression_type=1).
"""
import sqlite3, json, os, glob, sys
import cramjam

FF = "/home/amiller/.mozilla/firefox/6z2rzd23.default-release-1688918195831"
OUT = "/home/amiller/installing/ollama/gpu-show/programs/firefox"
os.makedirs(OUT, exist_ok=True)

origins = sorted(glob.glob(f"{FF}/storage/default/file++++home+amiller+installing+ollama+gpu-show+*.html"))

summary = []
for origin in origins:
    name = os.path.basename(origin).replace("file++++home+amiller+installing+ollama+gpu-show+", "").replace(".html", "")
    db = f"{origin}/ls/data.sqlite"
    if not os.path.exists(db):
        continue
    # read-only, URI mode
    con = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)
    rows = con.execute("SELECT key, compression_type, value FROM data").fetchall()
    con.close()
    dump = {}
    for key, comp, blob in rows:
        raw = blob.encode("utf-8") if isinstance(blob, str) else bytes(blob)
        if comp == 1 and raw:
            raw = bytes(cramjam.snappy.decompress_raw(raw))
        s = raw.decode("utf-8", errors="replace")
        if key.endswith("_entries"):
            try:
                dump[key] = json.loads(s)
            except json.JSONDecodeError as e:
                print(f"  ! bad json in {key}: {e}", file=sys.stderr)
                dump[key] = s
        else:
            dump[key] = s
    path = f"{OUT}/{name}.json"
    with open(path, "w") as f:
        json.dump(dump, f, indent=2)
    n_entries = len(dump.get(f"gpushow_{name}_entries", []))
    if not n_entries and name in ("shader", "static-shader"):
        n_entries = len(dump.get("gpushow_shaders_entries", []))
    summary.append((name, n_entries, os.path.getsize(path)))
    print(f"{name}: {n_entries} entries -> {path} ({os.path.getsize(path)} B)")

print()
print("=== summary ===")
for n, c, sz in summary:
    print(f"  {n:16s} {c:4d} entries  {sz:>8d} B on disk")
