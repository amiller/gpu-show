#!/usr/bin/env python3
"""Extract gpu-show shader localStorage from Firefox data.sqlite files.

Firefox stores localStorage in per-origin sqlite at
<profile>/storage/default/<origin>/ls/data.sqlite

Values are UTF-8 text, optionally snappy-compressed (compression_type=1).

Env:
  FIREFOX_PROFILE   path to the Firefox profile directory
                    (default: first matching ~/.mozilla/firefox/*.default-release*)
  FF_ORIGIN_GLOB    glob for origins to scan under storage/default/
                    (default: file++++*+gpu-show+*.html)
"""
import sqlite3, json, os, glob, sys
from pathlib import Path
import cramjam

def find_profile():
    env = os.environ.get("FIREFOX_PROFILE")
    if env: return env
    for p in sorted(Path.home().glob(".mozilla/firefox/*.default-release*")):
        if p.is_dir(): return str(p)
    for p in sorted(Path.home().glob(".mozilla/firefox/*.default*")):
        if p.is_dir(): return str(p)
    print("no Firefox profile found; set FIREFOX_PROFILE", file=sys.stderr); sys.exit(2)

FF = find_profile()
ORIGIN_GLOB = os.environ.get("FF_ORIGIN_GLOB", "file++++*+gpu-show+*.html")
OUT = Path(__file__).parent / "firefox"
OUT.mkdir(parents=True, exist_ok=True)

origins = sorted(glob.glob(f"{FF}/storage/default/{ORIGIN_GLOB}"))
if not origins:
    print(f"no matching origins in {FF}/storage/default/ (glob={ORIGIN_GLOB!r})", file=sys.stderr); sys.exit(1)

summary = []
for origin in origins:
    base = os.path.basename(origin)
    name = base.split("+gpu-show+", 1)[-1].replace(".html", "")
    db = f"{origin}/ls/data.sqlite"
    if not os.path.exists(db): continue
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
            try: dump[key] = json.loads(s)
            except json.JSONDecodeError as e:
                print(f"  ! bad json in {key}: {e}", file=sys.stderr); dump[key] = s
        else:
            dump[key] = s
    path = OUT / f"{name}.json"
    path.write_text(json.dumps(dump, indent=2))
    n = len(dump.get(f"gpushow_{name}_entries", []))
    if not n and name in ("shader", "static-shader"):
        n = len(dump.get("gpushow_shaders_entries", []))
    summary.append((name, n, path.stat().st_size))
    print(f"{name}: {n} entries -> {path} ({path.stat().st_size} B)")

print("\n=== summary ===")
for n, c, sz in summary:
    print(f"  {n:16s} {c:4d} entries  {sz:>8d} B on disk")
