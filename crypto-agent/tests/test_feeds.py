from jevagent.feeds import okx


def test_okx_sizes_are_converted_from_contracts(monkeypatch):
    okx._CT_VAL.clear()
    responses = {
        "/api/v5/public/instruments?instType=SWAP&instId=SKY-USDT-SWAP": {"data": [{"ctVal": "100", "ctValCcy": "SKY"}]},
        "/api/v5/market/books?instId=SKY-USDT-SWAP&sz=100": {"data": [{"ts": "1795800000000",
            "bids": [["0.0531", "7", "0", "1"]], "asks": [["0.0532", "3", "0", "1"]]}]},
        "/api/v5/market/trades?instId=SKY-USDT-SWAP&limit=100": {"data": [
            {"ts": "1795800000000", "px": "0.0531", "sz": "2", "side": "sell", "tradeId": "42"}]},
    }
    monkeypatch.setattr(okx, "_get", lambda path, timeout=5.0: responses[path])
    b = okx.book("SKY-USDT-SWAP")
    assert b.bids[0] == (0.0531, 700.0) and b.asks[0] == (0.0532, 300.0)
    (t,) = okx.trades("SKY-USDT-SWAP")
    assert t.sz == 200.0 and t.trade_id == "42"
