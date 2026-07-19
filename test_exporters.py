"""test_exporters.py — 快照介面與三個匯出器的單元測試"""

import csv
import json

from schemas import Evidence, Claim, Conflict, make_evidence_id
from snapshots import LocalSnapshotStore
from exporters import (
    export_evidence_list,
    build_citation_index,
    export_citations,
    export_validation_report,
)

RUN = "run_20260801_BTC_q1"


def make_evidence(seq=1, **overrides) -> Evidence:
    base = dict(
        id=make_evidence_id("price_technical", seq),
        run_id=RUN,
        category="price_technical",
        metric="rsi_14d",
        source="Binance",
        source_url="https://api.binance.com/api/v3/klines?symbol=BTCUSDT",
        source_type="api",
        fetched_at="2026-08-01T02:30:00Z",
        content_reference={"value": 71.3, "window": "14d", "threshold": 70},
        interpretation="BTC 14 日 RSI 為 71.3，高於 70 的超買門檻",
        credibility_tier="tier0_official",
        independence_group="grp_binance",
        collector="price_agent_v0.1",
        fetch_duration_ms=100,
    )
    base.update(overrides)
    return Evidence(**base)


# ---------------------------------------------------------------- 快照

def test_snapshot_roundtrip(tmp_path):
    s = LocalSnapshotStore(root=tmp_path / "snaps")
    key = s.save({"klines": [1, 2, 3]}, run_id=RUN, name="binance_001.json")
    assert key == f"{RUN}/binance_001.json"
    assert s.exists(key)
    assert json.loads(s.load(key)) == {"klines": [1, 2, 3]}


def test_snapshot_accepts_text_and_bytes(tmp_path):
    s = LocalSnapshotStore(root=tmp_path / "snaps")
    k1 = s.save("<html>raw page</html>", run_id=RUN, name="page.html")
    k2 = s.save(b"raw bytes", run_id=RUN, name="raw.bin")
    assert "raw page" in s.load(k1)
    assert s.load(k2) == "raw bytes"


# ---------------------------------------------------------------- 匯出器一

def test_export_evidence_list(tmp_path):
    evs = [make_evidence(2), make_evidence(1)]
    json_path, csv_path = export_evidence_list(evs, tmp_path)

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert [d["id"] for d in data] == ["ev_price_0001", "ev_price_0002"]  # 有排序
    assert data[0]["source_url"].startswith("https://")

    with csv_path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert rows[0]["id"] == "ev_price_0001"
    assert json.loads(rows[0]["content_reference"])["value"] == 71.3  # 複合欄位可還原


# ---------------------------------------------------------------- 匯出器二

def test_citation_index_has_anchor_fields(tmp_path):
    ev = make_evidence(raw_snapshot_key=f"{RUN}/binance_001.json")
    idx = build_citation_index([ev])
    c = idx["ev_price_0001"]
    for key in ("source", "source_url", "fetched_at", "snippet",
                "content_reference", "raw_snapshot_key"):
        assert c[key], f"引註缺少 {key}"
    assert c["snippet"] == ev.interpretation

    path = export_citations([ev], tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["ev_price_0001"]["source"] == "Binance"


# ---------------------------------------------------------------- 匯出器三

def _graph():
    ev = make_evidence()
    fact = Claim(id="c_fact_0001", run_id=RUN, type="fact",
                 statement="BTC 14 日 RSI 為 71.3", author="thesis_agent",
                 created_at="2026-08-01T02:35:00Z", supported_by=[ev.id])
    inf = Claim(id="c_infer_0001", run_id=RUN, type="inference",
                statement="RSI 71.3 高於 70，短線過熱", author="thesis_agent",
                created_at="2026-08-01T02:36:00Z", depends_on=[fact.id])
    concl = Claim(id="c_concl_0001", run_id=RUN, type="conclusion",
                  statement="短線偏空但中期結構未破壞", author="thesis_agent",
                  created_at="2026-08-01T02:37:00Z", depends_on=[inf.id])
    cf = Conflict(id="cf_0001", run_id=RUN, evidence_ids=[ev.id],
                  description="示範衝突", detected_by="rule:test",
                  resolution="採信 tier0 來源", resolved_in_claim=concl.id)
    return ev, [fact, inf, concl], [cf]


def test_validation_report_passed(tmp_path):
    ev, claims, conflicts = _graph()
    path = export_validation_report(RUN, [ev], claims, conflicts, tmp_path)
    r = json.loads(path.read_text(encoding="utf-8"))
    assert r["passed"] is True
    assert r["error_count"] == 0
    assert r["stats"]["evidence_count"] == 1
    assert r["stats"]["claims_by_type"] == {"fact": 1, "inference": 1, "conclusion": 1}
    assert r["stats"]["conflicts_resolved"] == 1


def test_validation_report_failed_lists_errors(tmp_path):
    ev, claims, conflicts = _graph()
    conflicts[0].resolution = None                     # 弄壞一處：矛盾未裁決
    claims[0].supported_by = ["ev_ghost_9999"]         # 再弄壞一處：引用捏造 id
    path = export_validation_report(RUN, [ev], claims, conflicts, tmp_path)
    r = json.loads(path.read_text(encoding="utf-8"))
    assert r["passed"] is False
    assert r["error_count"] >= 2
    assert any("ev_ghost_9999" in e for e in r["errors"])
    assert any("resolution" in e for e in r["errors"])
