"""Primary-source market snapshot fetcher (research layer).

Every response is saved verbatim with the UTC fetch time and source URL, so
every number in docs/research/* can be traced back to a file on disk.
Stdlib only; no API keys. Sources: CoinGecko, DefiLlama, OKX public, FRED CSV,
alternative.me.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

UA = {"User-Agent": "research-snapshot/1.0", "Accept": "application/json"}

SOURCES: dict[str, str] = {
    # Market structure
    "cg_global": "https://api.coingecko.com/api/v3/global",
    "cg_markets_1": "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1&price_change_percentage=7d,30d,1y",
    "cg_btc_365d": "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=365&interval=daily",
    "cg_eth_365d": "https://api.coingecko.com/api/v3/coins/ethereum/market_chart?vs_currency=usd&days=365&interval=daily",
    # Stablecoin liquidity
    "llama_stables_all": "https://stablecoins.llama.fi/stablecoincharts/all",
    "llama_stables_list": "https://stablecoins.llama.fi/stablecoins?includePrices=false",
    # Fundamentals: fees / revenue / holders revenue (value accrual)
    "llama_fees": "https://api.llama.fi/overview/fees?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true",
    "llama_revenue": "https://api.llama.fi/overview/fees?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyRevenue",
    "llama_holders_rev": "https://api.llama.fi/overview/fees?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyHoldersRevenue",
    "llama_protocols": "https://api.llama.fi/protocols",
    # Derivatives positioning (OKX public; Binance is geo-blocked from this host)
    "okx_funding_btc": "https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP",
    "okx_funding_eth": "https://www.okx.com/api/v5/public/funding-rate?instId=ETH-USDT-SWAP",
    "okx_funding_hist_btc": "https://www.okx.com/api/v5/public/funding-rate-history?instId=BTC-USDT-SWAP&limit=100",
    "okx_oi_swap": "https://www.okx.com/api/v5/public/open-interest?instType=SWAP",
    "okx_oi_hist_btc": "https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-volume?ccy=BTC&period=1D",
    "okx_lsr_btc": "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy=BTC&period=1D",
    # Sentiment (secondary source, labelled as such)
    "fng": "https://api.alternative.me/fng/?limit=60",
}

FRED = {  # Macro (St. Louis Fed, keyless CSV)
    "fred_dff": "DFF", "fred_dgs10": "DGS10", "fred_dxy_broad": "DTWEXBGS",
    "fred_t10yie": "T10YIE", "fred_walcl": "WALCL", "fred_m2": "M2SL",
}


def _get(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def main(out_root: str) -> int:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = Path(out_root) / day
    out.mkdir(parents=True, exist_ok=True)
    manifest = {}
    jobs = list(SOURCES.items()) + [
        (k, f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd=2025-01-01")
        for k, sid in FRED.items()
    ]
    for name, url in jobs:
        fetched = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            body = _get(url)
            ext = "csv" if name.startswith("fred_") else "json"
            (out / f"{name}.{ext}").write_bytes(body)
            manifest[name] = {"url": url, "fetched_utc": fetched, "bytes": len(body), "ok": True}
        except Exception as e:  # record failures honestly; never fill gaps
            manifest[name] = {"url": url, "fetched_utc": fetched, "ok": False, "error": repr(e)[:300]}
        print(("OK  " if manifest[name]["ok"] else "ERR ") + name, flush=True)
        if "coingecko" in url:
            time.sleep(6)  # free-tier rate limit
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))
    return 0 if all(m["ok"] for m in manifest.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "data/snapshots"))
