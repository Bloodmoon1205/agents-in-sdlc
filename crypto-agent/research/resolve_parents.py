"""Resolve DefiLlama parent protocols -> CoinGecko ids (network step, recorded).

Only parents whose children earned >= $200k fees in the last 30 days are looked
up. Output: <snapshot>/llama_parents.json  {parent_id: {gecko_id, symbol, name, fetched_utc}}
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import gzip


def _read_json(path: Path):
    """Snapshot files >400 kB are stored gzipped in git."""
    gz = Path(str(path) + ".gz")
    return json.loads(gzip.decompress(gz.read_bytes()) if gz.exists() and not Path(path).exists() else Path(path).read_text())

ALIASES = {"maker": ["sky", "sky-ecosystem"]}


def get(slug: str):
    req = urllib.request.Request(f"https://api.llama.fi/protocol/{slug}", headers={"User-Agent": "research-snapshot/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main(d: Path) -> None:
    tot = defaultdict(float)
    for kind in ("llama_fees", "llama_holders_rev"):
        for e in _read_json(d / f"{kind}.json")["protocols"]:
            if e.get("parentProtocol"):
                tot[e["parentProtocol"]] += e.get("total30d") or 0
    out = {}
    for pid, v in sorted(tot.items(), key=lambda kv: -kv[1]):
        if v < 2e5:
            continue
        slug = pid.split("#", 1)[1]
        for cand in [slug] + ALIASES.get(slug, []):
            try:
                j = get(cand)
                out[pid] = {"gecko_id": j.get("gecko_id"), "symbol": j.get("symbol"), "name": j.get("name"),
                            "slug": cand, "fetched_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                break
            except Exception as e:  # noqa: BLE001 - record and move on
                out[pid] = {"error": repr(e)[:200]}
            time.sleep(0.2)
    (d / "llama_parents.json").write_text(json.dumps(out, indent=2))
    print(f"resolved {sum(1 for v in out.values() if v.get('gecko_id'))}/{len(out)} parents")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
