import math
import random

import pytest

from jevagent.state_engine import BookEvent, TradeEvent

T0 = 1_795_800_000_000  # 2026-11-27T14:40Z, minute-aligned
MIN = 60_000


def book(ts, mid, spread_bps=2.0, depth=10, size=50.0, skew=0.0):
    half = mid * spread_bps / 2e4
    bids = tuple((mid - half - i * mid * 1e-4, size * (1 + skew)) for i in range(depth))
    asks = tuple((mid + half + i * mid * 1e-4, size * (1 - skew)) for i in range(depth))
    return BookEvent(ts, bids, asks)


def random_walk(n, mid0=60.0, vol_ann=0.6, seed=7, drift=0.0):
    rng = random.Random(seed)
    s = vol_ann / math.sqrt(525_600)
    mids, m = [], mid0
    for _ in range(n):
        m *= math.exp(rng.gauss(drift, s))
        mids.append(m)
    return mids


@pytest.fixture
def feed():
    def _feed(engine, mids, t0=T0, trades=True, seed=3):
        rng = random.Random(seed)
        for i, m in enumerate(mids):
            ts = t0 + i * MIN
            engine.on_book(book(ts, m))
            if trades:
                for j in range(3):
                    engine.on_trade(TradeEvent(ts - j * 1000, m, rng.uniform(1, 5), "buy" if rng.random() < 0.5 else "sell"))
        return t0 + (len(mids) - 1) * MIN
    return _feed
