"""test_log_middleware.py — Log middleware 單元測試

執行：python3 -m pytest test_log_middleware.py -v
重點：金鑰絕不能出現在 log 檔（致命錯誤）、錯誤要照拋但 log 必寫、
      asyncio 平行下 run_id 不互踩。
"""

import asyncio
import json

import pytest

from log_middleware import (
    ExecutionLog,
    logged,
    sanitize,
    extract_ids,
    start_run,
    set_fallback,
)
from schemas import Evidence


RUN = "run_20260801_BTC_q1"


@pytest.fixture
def sink(tmp_path):
    return ExecutionLog(path=tmp_path / "exec.jsonl")


# ---------------------------------------------------------------- sanitize（死穴）

def test_sanitize_masks_sensitive_keys():
    out = sanitize({
        "api_key": "SECRET123",
        "apiKey": "SECRET456",
        "token": "tok_abc",
        "authorization": "Bearer xyz",
        "symbol": "BTCUSDT",
    })
    assert out["api_key"] == "***"
    assert out["apiKey"] == "***"
    assert out["token"] == "***"
    assert out["authorization"] == "***"
    assert out["symbol"] == "BTCUSDT"          # 正常參數不動


def test_sanitize_masks_key_in_url_value():
    # 金鑰藏在 URL 查詢參數裡（Etherscan / CryptoCompare 的用法）
    out = sanitize({"url": "https://api.etherscan.io/api?module=account&apikey=ABC123XYZ&address=0x1"})
    assert "ABC123XYZ" not in out["url"]
    assert "apikey=***" in out["url"]
    assert "module=account" in out["url"]      # 其他參數保留（主辦方要查得到查詢條件）


def test_sanitize_recurses_into_nested_dict():
    out = sanitize({"config": {"secret": "shh", "coin": "BTC"}})
    assert out["config"]["secret"] == "***"
    assert out["config"]["coin"] == "BTC"


def test_sanitize_truncates_huge_values():
    out = sanitize({"payload": "x" * 5000})
    assert len(out["payload"]) < 500
    assert "truncated" in out["payload"]


# ---------------------------------------------------------------- extract_ids

def test_extract_ids_from_various_shapes():
    ev = Evidence(
        id="ev_price_0001", run_id=RUN, category="price_technical", metric="m",
        source="s", source_url="u", source_type="api",
        fetched_at="2026-08-01T02:30:00Z", content_reference={"v": 1},
        interpretation="v 為 1", credibility_tier="tier0_official",
        independence_group="g", collector="c", fetch_duration_ms=1,
    )
    assert extract_ids(ev) == ["ev_price_0001"]
    assert extract_ids([ev, {"id": "c_fact_0001"}, "cf_0001"]) == [
        "ev_price_0001", "c_fact_0001", "cf_0001"
    ]
    assert extract_ids("not_an_id") == []
    assert extract_ids(None) == []
    assert extract_ids({"data": "whatever"}) == []


# ---------------------------------------------------------------- 成功路徑

def test_async_success_logged(sink):
    start_run(RUN)

    @logged("fetch_demo", sink=sink)
    async def fetch(symbol: str):
        await asyncio.sleep(0.01)
        return ["ev_price_0001", "ev_price_0002"]

    result = asyncio.run(fetch(symbol="BTCUSDT"))
    assert result == ["ev_price_0001", "ev_price_0002"]

    entries = sink.entries()
    assert len(entries) == 1
    e = entries[0]
    assert e["step"] == "fetch_demo"
    assert e["run_id"] == RUN
    assert e["status"] == "ok"
    assert e["params"] == {"symbol": "BTCUSDT"}
    assert e["produced"] == ["ev_price_0001", "ev_price_0002"]
    assert e["duration_ms"] >= 10
    assert e["ts"].endswith("Z")


def test_sync_function_also_supported(sink):
    start_run(RUN)

    @logged("precompute", sink=sink)
    def precompute(window: int):
        return "ev_price_0009"

    assert precompute(window=14) == "ev_price_0009"
    e = sink.entries()[0]
    assert e["status"] == "ok" and e["produced"] == ["ev_price_0009"]


# ---------------------------------------------------------------- 失敗路徑

def test_error_logged_and_reraised(sink):
    start_run(RUN)
    set_fallback("coingecko_backup")

    @logged("fetch_fail", sink=sink)
    async def boom(symbol: str):
        raise TimeoutError("binance timeout after 5s")

    with pytest.raises(TimeoutError):          # 例外必須照拋，降級機制才接得到
        asyncio.run(boom(symbol="BTCUSDT"))

    e = sink.entries()[0]
    assert e["status"] == "error:TimeoutError"
    assert "binance timeout" in e["error"]
    assert e["fallback"] == "coingecko_backup"
    assert e["duration_ms"] is not None        # finally 有跑到


def test_api_key_never_reaches_log_file(sink, tmp_path):
    """整合驗證：就算函數收了金鑰參數又爆炸，金鑰也不會落在 log 檔任何角落。"""
    start_run(RUN)

    @logged("fetch_with_key", sink=sink)
    async def fetch(url: str, api_key: str):
        raise ConnectionError("boom")

    with pytest.raises(ConnectionError):
        asyncio.run(fetch(
            url="https://min-api.cryptocompare.com/data?fsym=BTC&api_key=DEADBEEF99",
            api_key="DEADBEEF99",
        ))

    raw = (tmp_path / "exec.jsonl").read_text(encoding="utf-8")
    assert "DEADBEEF99" not in raw             # ← 比賽死穴，這條紅了就不准 push


# ---------------------------------------------------------------- 平行不互踩

def test_parallel_tasks_keep_own_run_id(sink):
    """四路平行時各任務的 log 綁各自 run_id（contextvars 隔離）。"""

    @logged("parallel_fetch", sink=sink)
    async def fetch(tag: str):
        await asyncio.sleep(0.01)
        return f"ev_price_000{tag}"

    async def task(run_id: str, tag: str):
        start_run(run_id)                      # 每個 task 在自己的 context 設 run_id
        await fetch(tag=tag)

    async def main():
        await asyncio.gather(
            task("run_20260801_BTC_q1", "1"),
            task("run_20260801_ETH_q2", "2"),
        )

    asyncio.run(main())
    by_param = {e["params"]["tag"]: e["run_id"] for e in sink.entries()}
    assert by_param == {"1": "run_20260801_BTC_q1", "2": "run_20260801_ETH_q2"}


# ---------------------------------------------------------------- JSONL 格式

def test_every_line_is_valid_json(sink, tmp_path):
    start_run(RUN)

    @logged("s1", sink=sink)
    def a():
        return None

    @logged("s2", sink=sink)
    def b():
        raise ValueError("x")

    a()
    with pytest.raises(ValueError):
        b()

    lines = (tmp_path / "exec.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        json.loads(line)                       # 每行都必須是合法 JSON
