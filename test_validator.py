"""test_validator.py — 驗證器單元測試（W1 五 Definition of Done 項目）

執行：python3 -m pytest test_validator.py -v
策略：每條規則各準備「正常物件」與「有瑕疵的假物件」，
斷言錯誤清單為空 / 含有對應關鍵字。
"""

from schemas import Evidence, Claim, Conflict
from validator import (
    check_completeness,
    check_reference_existence,
    check_hierarchy,
    check_number_traceability,
    check_conflict_coverage,
    validate_run,
)

RUN = "run_20260801_BTC_q1"


def make_evidence(**overrides) -> Evidence:
    base = dict(
        id="ev_price_0001",
        run_id=RUN,
        category="price_technical",
        metric="rsi_14d",
        source="Binance",
        source_url="https://api.binance.com/api/v3/klines?symbol=BTCUSDT",
        source_type="api",
        fetched_at="2026-08-01T02:30:00Z",
        content_reference={"value": 71.3, "window": "14d"},
        interpretation="BTC 14 日 RSI 為 71.3，位於超買區間",
        credibility_tier="tier0_official",
        independence_group="grp_binance",
        collector="price_agent_v0.1",
        fetch_duration_ms=100,
    )
    base.update(overrides)
    return Evidence(**base)


def make_claim(**overrides) -> Claim:
    base = dict(
        id="c_fact_0001",
        run_id=RUN,
        type="fact",
        statement="BTC 14 日 RSI 為 71.3",
        author="thesis_agent",
        created_at="2026-08-01T02:35:00Z",
        supported_by=["ev_price_0001"],
    )
    base.update(overrides)
    return Claim(**base)


# ---------------------------------------------------------------- 規則 1

def test_completeness_pass():
    assert check_completeness(make_evidence()) == []


def test_completeness_missing_source_url():
    errs = check_completeness(make_evidence(source_url=""))
    assert len(errs) == 1 and "source_url" in errs[0]


def test_completeness_empty_content_reference():
    errs = check_completeness(make_evidence(content_reference={}))
    assert any("content_reference" in e for e in errs)


# ---------------------------------------------------------------- 規則 2

def test_reference_nonexistent_evidence():
    claim = make_claim(supported_by=["ev_price_9999"])
    errs = check_reference_existence(claim, {}, {})
    assert any("不存在的 Evidence" in e and "ev_price_9999" in e for e in errs)


def test_reference_cross_run_rejected():
    ev = make_evidence(run_id="run_20260801_ETH_q2")
    claim = make_claim()
    errs = check_reference_existence(claim, {ev.id: ev}, {})
    assert any("不同執行" in e for e in errs)


def test_reference_pass():
    ev = make_evidence()
    claim = make_claim()
    assert check_reference_existence(claim, {ev.id: ev}, {}) == []


# ---------------------------------------------------------------- 規則 3

def test_fact_without_evidence_fails():
    claim = make_claim(supported_by=[])
    errs = check_hierarchy(claim, {})
    assert any("至少掛載一個 Evidence" in e for e in errs)


def test_fact_with_depends_on_fails():
    claim = make_claim(depends_on=["c_fact_0002"])
    errs = check_hierarchy(claim, {})
    assert any("不得依賴其他 Claim" in e for e in errs)


def test_inference_needs_fact_upstream():
    fact = make_claim()
    inf = make_claim(id="c_infer_0001", type="inference",
                     supported_by=[], depends_on=[fact.id])
    assert check_hierarchy(inf, {fact.id: fact, inf.id: inf}) == []

    bad = make_claim(id="c_infer_0002", type="inference",
                     supported_by=[], depends_on=[])
    errs = check_hierarchy(bad, {})
    assert any("至少依賴一個 fact" in e for e in errs)


def test_conclusion_only_depends_on_inference():
    fact = make_claim()
    concl = make_claim(id="c_concl_0001", type="conclusion",
                       supported_by=[], depends_on=[fact.id])
    errs = check_hierarchy(concl, {fact.id: fact, concl.id: concl})
    assert any("只能依賴 inference" in e for e in errs)


def test_conclusion_chain_traceable_to_evidence():
    fact = make_claim()
    inf = make_claim(id="c_infer_0001", type="inference",
                     supported_by=[], depends_on=[fact.id])
    concl = make_claim(id="c_concl_0001", type="conclusion",
                       supported_by=[], depends_on=[inf.id])
    graph = {c.id: c for c in (fact, inf, concl)}
    assert check_hierarchy(concl, graph) == []


def test_conclusion_circular_dependency_detected():
    # 兩個 inference 互相依賴形成環 → 追溯必須回報失敗而非無限遞迴
    inf_a = make_claim(id="c_infer_a", type="inference",
                       supported_by=[], depends_on=["c_infer_b"])
    inf_b = make_claim(id="c_infer_b", type="inference",
                       supported_by=[], depends_on=["c_infer_a"])
    concl = make_claim(id="c_concl_0001", type="conclusion",
                       supported_by=[], depends_on=["c_infer_a"])
    graph = {c.id: c for c in (inf_a, inf_b, concl)}
    errs = check_hierarchy(concl, graph)
    assert any("無法一路追溯" in e for e in errs)


# ---------------------------------------------------------------- 規則 4

def test_number_traceable_pass():
    ev = make_evidence()
    claim = make_claim(statement="BTC 的 RSI 是 71.3，已達超買")
    assert check_number_traceability(claim, {ev.id: ev}) == []


def test_number_hallucinated_fails():
    ev = make_evidence()
    claim = make_claim(statement="BTC 的 RSI 是 88.8")
    errs = check_number_traceability(claim, {ev.id: ev})
    assert any("88.8" in e and "疑似幻覺" in e for e in errs)


def test_number_thousand_separator_tolerated():
    ev = make_evidence(
        content_reference={"volume": 1234567},
        interpretation="24 小時成交量為 1234567",
    )
    claim = make_claim(statement="成交量高達 1,234,567，且 24 小時內持續放大")
    assert check_number_traceability(claim, {ev.id: ev}) == []


def test_number_percent_and_trailing_zero_tolerated():
    ev = make_evidence(content_reference={"change_pct": 5.2},
                       interpretation="日漲幅 5.2")
    claim = make_claim(statement="日漲幅達 5.20%")
    assert check_number_traceability(claim, {ev.id: ev}) == []


def test_number_via_dependency_chain():
    # inference 沒直接掛 Evidence，數字要能沿依賴鏈到 fact 的證據中找到
    ev = make_evidence()
    fact = make_claim()
    inf = make_claim(id="c_infer_0001", type="inference", supported_by=[],
                     depends_on=[fact.id], statement="RSI 71.3 顯示短線過熱")
    graph = {fact.id: fact, inf.id: inf}
    assert check_number_traceability(inf, {ev.id: ev}, graph) == []


# ---------------------------------------------------------------- 規則 5

def test_conflict_without_resolution_fails():
    cf = Conflict(id="cf_0001", run_id=RUN,
                  evidence_ids=["ev_price_0001", "ev_news_0002"],
                  description="價格看漲 vs 大戶轉出", detected_by="agent:devil_advocate")
    errs = check_conflict_coverage(cf)
    assert any("尚未提供 resolution" in e for e in errs)


def test_conflict_resolved_pass():
    cf = Conflict(id="cf_0001", run_id=RUN,
                  evidence_ids=["ev_price_0001"], description="x",
                  detected_by="rule:price_vs_flow",
                  resolution="採信鏈上數據，因其為 tier0 且時間較新",
                  resolved_in_claim="c_concl_0001")
    concl = make_claim(id="c_concl_0001", type="conclusion")
    assert check_conflict_coverage(cf, {concl.id: concl}) == []


# ---------------------------------------------------------------- 總閘

def test_validate_run_end_to_end():
    ev = make_evidence()
    fact = make_claim()
    inf = make_claim(id="c_infer_0001", type="inference", supported_by=[],
                     depends_on=[fact.id], statement="RSI 71.3 過熱，短線有回檔壓力")
    concl = make_claim(id="c_concl_0001", type="conclusion", supported_by=[],
                       depends_on=[inf.id], statement="短線偏空但中期結構未破壞")
    cf = Conflict(id="cf_0001", run_id=RUN, evidence_ids=[ev.id],
                  description="x", detected_by="rule:test",
                  resolution="已裁決", resolved_in_claim=concl.id)
    assert validate_run([ev], [fact, inf, concl], [cf]) == []


def test_validate_run_collects_all_errors():
    bad_ev = make_evidence(source_url="")                      # 規則 1
    bad_fact = make_claim(supported_by=["ev_ghost_0001"])      # 規則 2
    bad_cf = Conflict(id="cf_0001", run_id=RUN, evidence_ids=[],
                      description="x", detected_by="rule:test")  # 規則 5
    errs = validate_run([bad_ev], [bad_fact], [bad_cf])
    assert len(errs) >= 3  # 錯誤要全部收集，不能遇到第一個就停
