"""Fundamentals screen from a saved snapshot (no network).

Joins DefiLlama fees / revenue / holders-revenue (30d) with CoinGecko market
data by gecko_id and ranks tokens by price-to-holders-revenue (value that
actually reaches token holders), fee momentum, and float overhang.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
import gzip


def _read_json(path: Path):
    """Snapshot files >400 kB are stored gzipped in git."""
    gz = Path(str(path) + ".gz")
    return json.loads(gzip.decompress(gz.read_bytes()) if gz.exists() and not Path(path).exists() else Path(path).read_text())

ANN = 365 / 30


def load(d: Path, n: str):
    return _read_json(d / f"{n}.json")


def main(d: Path, min_mcap: float = 3e8) -> list[dict]:
    protos = load(d, "llama_protocols")
    by_slug = {p["slug"]: p for p in protos}
    by_id = {str(p.get("id")): p for p in protos}
    parent_gecko = {}
    pf = d / "llama_parents.json"
    if pf.exists():
        for pid, v in json.loads(pf.read_text()).items():
            if v.get("gecko_id"):
                parent_gecko[pid] = v["gecko_id"]
    for p in protos:
        if p.get("parentProtocol") and p.get("gecko_id"):
            parent_gecko.setdefault(p["parentProtocol"], p["gecko_id"])
    agg: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    for kind in ("llama_fees", "llama_revenue", "llama_holders_rev"):
        for e in load(d, kind)["protocols"]:
            p = by_slug.get(e.get("slug")) or by_id.get(str(e.get("defillamaId")))
            gid = (p or {}).get("gecko_id") or parent_gecko.get(e.get("parentProtocol")) or parent_gecko.get((p or {}).get("parentProtocol"))
            if not gid:
                continue
            a = agg[gid]
            a[f"{kind}_30d"] += e.get("total30d") or 0
            a[f"{kind}_prev30d"] += e.get("total60dto30d") or 0
            a[f"tvl"] = max(a["tvl"], (p or {}).get("tvl") or 0)
    mk = {m["id"]: m for m in load(d, "cg_markets_1")}
    rows = []
    for gid, a in agg.items():
        m = mk.get(gid)
        if not m or not m.get("market_cap") or m["market_cap"] < min_mcap:
            continue
        hr = a["llama_holders_rev_30d"] * ANN
        rev = a["llama_revenue_30d"] * ANN
        fees = a["llama_fees_30d"] * ANN
        fdv = m.get("fully_diluted_valuation") or m["market_cap"]
        circ, tot = m.get("circulating_supply") or 0, m.get("total_supply") or m.get("max_supply") or 0
        rows.append({
            "id": gid, "symbol": m["symbol"].upper(), "mcap": m["market_cap"], "fdv": fdv,
            "float_pct": (circ / tot) if tot else None,
            "ann_fees": fees, "ann_revenue": rev, "ann_holders_rev": hr,
            "p_hr": m["market_cap"] / hr if hr > 0 else None,
            "fdv_hr": fdv / hr if hr > 0 else None,
            "p_rev": m["market_cap"] / rev if rev > 0 else None,
            "holders_share_of_fees": hr / fees if fees else None,
            "fees_30d_vs_prev30d": (a["llama_fees_30d"] / a["llama_fees_prev30d"] - 1) if a["llama_fees_prev30d"] else None,
            "hr_30d_vs_prev30d": (a["llama_holders_rev_30d"] / a["llama_holders_rev_prev30d"] - 1) if a["llama_holders_rev_prev30d"] else None,
            "tvl": a["tvl"], "px_30d": m.get("price_change_percentage_30d_in_currency"),
            "px_1y": m.get("price_change_percentage_1y_in_currency"), "cg_last_updated": m.get("last_updated"),
        })
    rows.sort(key=lambda r: (r["p_hr"] is None, r["p_hr"] or 0))
    return rows


if __name__ == "__main__":
    d = Path(sys.argv[1])
    rows = main(d)
    (d / "SCREEN.json").write_text(json.dumps(rows, indent=2))
    f = lambda v, s="{:.1f}": "-" if v is None else s.format(v)
    print(f"{'sym':8}{'mcap$B':>8}{'fdv$B':>8}{'float':>7}{'P/HR':>7}{'FDV/HR':>8}{'P/Rev':>7}{'HR/fee':>7}{'fee30Δ':>8}{'HR30Δ':>8}{'px30d':>7}")
    for r in rows[:45]:
        print(f"{r['symbol']:8}{r['mcap']/1e9:8.2f}{r['fdv']/1e9:8.2f}{f(r['float_pct'] and r['float_pct']*100,'{:.0f}%'):>7}{f(r['p_hr']):>7}{f(r['fdv_hr']):>8}{f(r['p_rev']):>7}{f(r['holders_share_of_fees'] and r['holders_share_of_fees']*100,'{:.0f}%'):>7}{f(r['fees_30d_vs_prev30d'] and r['fees_30d_vs_prev30d']*100,'{:+.0f}%'):>8}{f(r['hr_30d_vs_prev30d'] and r['hr_30d_vs_prev30d']*100,'{:+.0f}%'):>8}{f(r['px_30d'],'{:+.0f}'):>7}")
