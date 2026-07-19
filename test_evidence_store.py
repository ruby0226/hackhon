"""test_evidence_store.py — Evidence Store 單元測試

執行：python3 -m pytest test_evidence_store.py -v
涵蓋筆記「驗證器會擋什麼」五條自查清單 + 持久化重載 + 查詢。
"""

import pytest

from schemas import Evidence, make_evidence_id
from evidence_store import EvidenceStore, EvidenceValidationError

RUN = "run_20260801_BTC_q1"


def make_evidence(**overrides) -> Evidence:
    base = dict(
        id=make_evidence_id("price_technical", 1),
        run_id=RUN,
        category="price_technical",
        metric="rsi_14d",
        source="Binance",
        source_url="https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d",
        source_type="api",
        fetched_at="2026-08-01T02:30:00Z",
        content_reference={"value": 71.3, "window": "14d"},
        interpretation="BTC 14 日 RSI 為 71.3，高於 70 的超買門檻",
        credibility_tier="tier0_official",
        independence_group="grp_binance",
        collector="price_agent_v0.1",
        fetch_duration_ms=100,
    )
    base.update(overrides)
    return Evidence(**base)


@pytest.fixture
def s(tmp_path):
    store = EvidenceStore(path=tmp_path / "ev.jsonl")
    store.start_run(RUN)
    return store


# ---------------------------------------------------------------- 正常流程

def test_append_and_query(s):
    ev = make_evidence(
        content_reference={"value": 71.3, "window": "14d", "threshold": 70},
    )
    s.append(ev)
    assert s.count() == 1
    assert s.get(ev.id).source == "Binance"
    assert ev.id in s.ids()
    assert s.by_category("price_technical")[0].id == ev.id
    assert s.by_category("news") == []


def test_persistence_reload(s, tmp_path):
    ev = make_evidence(
        content_reference={"value": 71.3, "window": "14d", "threshold": 70},
    )
    s.append(ev)
    # 模擬程式重啟：用同一個檔案開新 store
    s2 = EvidenceStore(path=tmp_path / "ev.jsonl")
    s2.start_run(RUN)
    assert s2.count() == 1
    assert s2.get(ev.id) == ev


# ---------------------------------------------------------------- 自查清單 1：★ 欄位為空

def test_missing_required_field_raises(s):
    ev = make_evidence(source_url="")
    with pytest.raises(EvidenceValidationError) as exc:
        s.append(ev)
    assert any("source_url" in e for e in exc.value.errors)
    assert s.count() == 0  # 沒寫進去


# ---------------------------------------------------------------- 自查清單 2：允許值

def test_bad_category_raises(s):
    ev = make_evidence(category="on_chain")  # 筆記點名的經典 typo（正確是 onchain）
    with pytest.raises(EvidenceValidationError) as exc:
        s.append(ev)
    assert any("category" in e and "on_chain" in e for e in exc.value.errors)


def test_bad_tier_raises(s):
    ev = make_evidence(credibility_tier="tier5_random")
    with pytest.raises(EvidenceValidationError):
        s.append(ev)


# ---------------------------------------------------------------- 自查清單 3：時間格式

def test_taipei_time_rejected(s):
    # 筆記點名的常見錯誤：用台北時間 +08:00 或忘了 Z
    ev = make_evidence(fetched_at="2026-08-01T10:30:00+08:00")
    with pytest.raises(EvidenceValidationError) as exc:
        s.append(ev)
    assert any("UTC ISO 8601" in e for e in exc.value.errors)

    ev2 = make_evidence(fetched_at="2026-08-01 10:30:00")
    with pytest.raises(EvidenceValidationError):
        s.append(ev2)


# ---------------------------------------------------------------- 自查清單 4：run_id

def test_wrong_run_id_rejected(s):
    ev = make_evidence(
        run_id="run_20260801_ETH_q2",
        content_reference={"value": 71.3, "window": "14d", "threshold": 70},
    )
    with pytest.raises(EvidenceValidationError) as exc:
        s.append(ev)
    assert any("run_id" in e for e in exc.value.errors)


# ---------------------------------------------------------------- 自查清單 5：interpretation 數字溯源

def test_interpretation_number_not_in_reference_raises(s):
    # interpretation 說 71.3 和門檻 70，但 content_reference 只有 value
    ev = make_evidence(content_reference={"value": 71.3})
    with pytest.raises(EvidenceValidationError) as exc:
        s.append(ev)
    assert any("70" in e and "content_reference" in e for e in exc.value.errors)


def test_interpretation_thousand_separator_ok(s):
    ev = make_evidence(
        id=make_evidence_id("price_technical", 2),
        metric="volume_24h",
        content_reference={"volume": 1234567, "window": "24h"},
        interpretation="24 小時成交量為 1,234,567",
    )
    s.append(ev)  # 千分位差異要容忍，不應 raise
    assert s.count() == 1


# ---------------------------------------------------------------- 撞號

def test_duplicate_id_rejected(s):
    ref = {"value": 71.3, "window": "14d", "threshold": 70}
    s.append(make_evidence(content_reference=ref))
    with pytest.raises(EvidenceValidationError) as exc:
        s.append(make_evidence(content_reference=ref))
    assert any("撞號" in e for e in exc.value.errors)


# ---------------------------------------------------------------- 錯誤要一次全列

def test_all_errors_reported_at_once(s):
    ev = make_evidence(source="", category="on_chain",
                       fetched_at="2026-08-01 10:30")
    with pytest.raises(EvidenceValidationError) as exc:
        s.append(ev)
    assert len(exc.value.errors) >= 3  # 三個問題要同時列出，不能修一個才發現下一個
