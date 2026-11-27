"""Regime metrics computed ONLY from a saved snapshot directory (no network)."""
from __future__ import annotations

import csv
import json
import math
import statistics as st
import sys
from pathlib import Path
import gzip


def _read_json(path: Path):
    """Snapshot files >400 kB are stored gzipped in git."""
    gz = Path(str(path) + ".gz")
    return json.loads(gzip.decompress(gz.read_bytes()) if gz.exists() and not Path(path).exists() else Path(path).read_text())


def load(d: Path, name: str):
    return _read_json(d / f"{name}.json")


def fred(d: Path, name: str) -> list[tuple[str, float]]:
    rows = []
    with open(d / f"{name}.csv") as f:
        r = csv.reader(f)
        next(r)
        for date, v in r:
            if v not in ("", "."):
                rows.append((date, float(v)))
    return rows


def trend(prices: list[float]) -> dict:
    p = prices
    lr = [math.log(b / a) for a, b in zip(p[:-1], p[1:])]
    rv30 = st.pstdev(lr[-30:]) * math.sqrt(365)
    rv90 = st.pstdev(lr[-90:]) * math.sqrt(365)
    ma50, ma200 = st.mean(p[-50:]), st.mean(p[-200:])
    hi = max(p)
    return {
        "last": p[-1], "ret_30d": p[-1] / p[-31] - 1, "ret_90d": p[-1] / p[-91] - 1,
        "ret_365d": p[-1] / p[0] - 1, "ma50": ma50, "ma200": ma200,
        "above_ma200": p[-1] > ma200, "ma50_above_ma200": ma50 > ma200,
        "drawdown_from_365d_high": p[-1] / hi - 1, "rv30_ann": rv30, "rv90_ann": rv90,
    }


def main(d: Path) -> dict:
    g = load(d, "cg_global")["data"]
    out: dict = {"snapshot": d.name}
    out["global"] = {
        "total_mcap_usd": g["total_market_cap"]["usd"],
        "mcap_change_24h_pct": g["market_cap_change_percentage_24h_usd"],
        "btc_dominance_pct": g["market_cap_percentage"]["btc"],
        "eth_dominance_pct": g["market_cap_percentage"]["eth"],
        "cg_updated_at_unix": g["updated_at"],
    }
    for sym, f in (("BTC", "cg_btc_365d"), ("ETH", "cg_eth_365d")):
        px = [x[1] for x in load(d, f)["prices"]]
        out[sym] = trend(px)
        out[sym]["last_point_unix_ms"] = load(d, f)["prices"][-1][0]
    eth_btc = [e[1] / b[1] for e, b in zip(load(d, "cg_eth_365d")["prices"], load(d, "cg_btc_365d")["prices"])]
    out["ETHBTC"] = {"last": eth_btc[-1], "ret_30d": eth_btc[-1] / eth_btc[-31] - 1, "ret_90d": eth_btc[-1] / eth_btc[-91] - 1}

    sc = load(d, "llama_stables_all")
    def tot(x):
        return sum(v for v in x["totalCirculatingUSD"].values())
    s_last, s_30, s_90 = tot(sc[-1]), tot(sc[-31]), tot(sc[-91])
    out["stablecoins"] = {"total_usd": s_last, "chg_30d": s_last / s_30 - 1, "chg_90d": s_last / s_90 - 1,
                          "as_of_unix": int(sc[-1]["date"])}

    fb = load(d, "okx_funding_btc")["data"][0]
    fe = load(d, "okx_funding_eth")["data"][0]
    hist = [float(x["realizedRate"] or x["fundingRate"]) for x in load(d, "okx_funding_hist_btc")["data"]]
    out["funding"] = {
        "btc_current_8h": float(fb["fundingRate"]), "eth_current_8h": float(fe["fundingRate"]),
        "btc_mean_last100_8h": st.mean(hist), "btc_annualized_mean": st.mean(hist) * 3 * 365,
        "btc_frac_negative_last100": sum(h < 0 for h in hist) / len(hist),
    }
    oi = load(d, "okx_oi_hist_btc")["data"]  # [ts, oi_usd, vol_usd] newest first
    oi_now, oi_30 = float(oi[0][1]), float(oi[min(30, len(oi) - 1)][1])
    out["okx_btc_oi"] = {"oi_usd_now": oi_now, "chg_30d": oi_now / oi_30 - 1, "as_of_ms": int(oi[0][0])}
    lsr = load(d, "okx_lsr_btc")["data"]
    out["okx_btc_long_short_accounts"] = {"now": float(lsr[0][1]), "as_of_ms": int(lsr[0][0])}
    fng = load(d, "fng")["data"]
    out["fear_greed"] = {"now": int(fng[0]["value"]), "label": fng[0]["value_classification"],
                         "mean_30d": st.mean(int(x["value"]) for x in fng[:30])}
    macro = {}
    for k in ("fred_dff", "fred_dgs10", "fred_dxy_broad", "fred_t10yie", "fred_walcl", "fred_m2"):
        rows = fred(d, k)
        last = rows[-1]
        prev = rows[-min(len(rows), 63)] if k not in ("fred_walcl", "fred_m2") else rows[-min(len(rows), 13 if k == "fred_walcl" else 4)]
        macro[k] = {"last_date": last[0], "last": last[1], "ref_date": prev[0], "ref": prev[1]}
    out["macro"] = macro
    return out


if __name__ == "__main__":
    d = Path(sys.argv[1])
    res = main(d)
    (d / "REGIME.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))
