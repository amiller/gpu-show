#!/usr/bin/env python3
"""Extract gpu-show shader localStorage from Chrome leveldb.

Chrome Local Storage keys look like:
  b'_' + <origin> + b'\\x00\\x01' + <js_key>
Values are prefixed with a 1-byte type code:
  b'\\x00' -> UTF-16LE, b'\\x01' -> Latin-1

Env:
  CHROME_PROFILE  path to Chrome profile (default: ~/.config/google-chrome/Default)
  CHROME_LDB      path to a leveldb dir to read directly (skips the copy step)
  KEY_FILTER      substring keys must contain (default: gpushow_)
"""
import plyvel, json, os, shutil, sys
from pathlib import Path

def source_ldb():
    env = os.environ.get("CHROME_LDB")
    if env: return Path(env)
    profile = Path(os.environ.get("CHROME_PROFILE", Path.home() / ".config/google-chrome/Default"))
    ldb = profile / "Local Storage" / "leveldb"
    if not ldb.is_dir():
        print(f"no leveldb at {ldb}", file=sys.stderr); sys.exit(2)
    copy = Path(__file__).parent / "_chrome_ldb_copy"
    if copy.exists(): shutil.rmtree(copy)
    shutil.copytree(ldb, copy)
    (copy / "LOCK").unlink(missing_ok=True)
    return copy

SRC = source_ldb()
OUT = Path(__file__).parent / "chrome"
OUT.mkdir(parents=True, exist_ok=True)
FILTER = os.environ.get("KEY_FILTER", "gpushow_").encode()

db = plyvel.DB(str(SRC), create_if_missing=False)

def decode_val(v):
    if not v: return ""
    tag, body = v[:1], v[1:]
    if tag == b"\x00": return body.decode("utf-16-le", errors="replace")
    if tag == b"\x01": return body.decode("latin-1", errors="replace")
    return v.decode("utf-8", errors="replace")

buckets = {}
for k, v in db:
    if FILTER not in k: continue
    i = k.find(b"\x00\x01")
    if i < 0:
        origin, jskey = "?", k.decode("utf-8", errors="replace")
    else:
        origin = k[1:i].decode("utf-8", errors="replace")
        jskey = k[i+2:].decode("utf-8", errors="replace")
    buckets.setdefault(origin, {})[jskey] = decode_val(v)
db.close()

summary = []
for origin, kv in sorted(buckets.items()):
    name = origin.rstrip("/").split("/")[-1].replace(".html","") or "unknown"
    safe = name.replace(":", "_").replace("/", "_")
    for jk in list(kv):
        if jk.endswith("_entries"):
            try: kv[jk] = json.loads(kv[jk])
            except Exception: pass
    path = OUT / f"{safe}.json"
    path.write_text(json.dumps({"origin": origin, "data": kv}, indent=2))
    n = 0
    for jk, vv in kv.items():
        if jk.endswith("_entries") and isinstance(vv, list):
            n = len(vv); break
    summary.append((safe, origin, n, path.stat().st_size))

print("=== chrome summary ===")
for n, o, c, sz in summary:
    print(f"  {n:16s} {c:4d} entries  {sz:>8d} B   [{o}]")
