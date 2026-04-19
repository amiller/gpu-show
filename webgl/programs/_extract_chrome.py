#!/usr/bin/env python3
"""Extract gpu-show shader localStorage from Chrome leveldb.

Chrome Local Storage keys look like:
  b'_' + <origin> + b'\x00\x01' + <js_key>
and values are prefixed with a 1-byte type code:
  b'\x00' -> UTF-16LE
  b'\x01' -> Latin-1 / ASCII
"""
import plyvel, json, os

SRC = "/home/amiller/installing/ollama/gpu-show/programs/_chrome_ldb_copy"
OUT = "/home/amiller/installing/ollama/gpu-show/programs/chrome"
os.makedirs(OUT, exist_ok=True)

db = plyvel.DB(SRC, create_if_missing=False)

def decode_val(v):
    if not v: return ""
    tag = v[0:1]
    body = v[1:]
    if tag == b"\x00":
        return body.decode("utf-16-le", errors="replace")
    if tag == b"\x01":
        return body.decode("latin-1", errors="replace")
    return v.decode("utf-8", errors="replace")

buckets = {}  # origin -> {js_key: value}
for k, v in db:
    if b"gpushow_" not in k:
        continue
    # split on \x00\x01
    i = k.find(b"\x00\x01")
    if i < 0:
        origin, jskey = "?", k.decode("utf-8", errors="replace")
    else:
        origin = k[1:i].decode("utf-8", errors="replace")  # drop leading '_'
        jskey = k[i+2:].decode("utf-8", errors="replace")
    buckets.setdefault(origin, {})[jskey] = decode_val(v)

db.close()

summary = []
for origin, kv in sorted(buckets.items()):
    # derive a filename from origin
    name = origin.rstrip("/").split("/")[-1].replace(".html","") or "unknown"
    # parse _entries keys as JSON
    for jk in list(kv):
        if jk.endswith("_entries"):
            try: kv[jk] = json.loads(kv[jk])
            except Exception: pass
    path = f"{OUT}/{name}.json"
    with open(path, "w") as f:
        json.dump({"origin": origin, "data": kv}, f, indent=2)
    n = 0
    for jk, vv in kv.items():
        if jk.endswith("_entries") and isinstance(vv, list):
            n = len(vv); break
    summary.append((name, origin, n, os.path.getsize(path)))

print("=== chrome summary ===")
for n, o, c, sz in summary:
    print(f"  {n:16s} {c:4d} entries  {sz:>8d} B   [{o}]")
