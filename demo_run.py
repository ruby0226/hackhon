"""demo_run.py — W1 Definition of Done 驗收腳本

模擬完整管線（不打真實 API）：
  手塞 10 筆 Evidence（含快照）→ 手寫 5 個 Claim + 1 個 Conflict
  → 跑最終驗證閘 → 三個匯出器產出交付文件 → 全程 log middleware 記錄

執行：python3 demo_run.py
產出：./demo_output/run_20260801_BTC_q1/ 底下四個檔案 + snapshots/ + 執行紀錄
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from schemas import Evidence, Claim, Conflict, make_evidence_id, make_run_id
from evidence_store import EvidenceStore
from snapshots import LocalSnapshotStore
from log_middleware import ExecutionLog, logged, start_run
from exporters import export_evidence_list, export_citations, export_validation_report
from validator import validate_run

RUN = make_run_id("20260801", "BTC", "q1")
OUT = Path("./demo_output") / RUN

# 乾淨環境
shutil.rmtree("./demo_output", ignore_errors=True)
store = EvidenceStore(path=OUT / "evidence.jsonl")
snaps = LocalSnapshotStore(root=OUT / "snapshots")
exec_log = ExecutionLog(path=OUT / "execution_log.jsonl")


# --------------------------------------------------------------------------
# 假資料工廠：10 筆橫跨四路蒐證的 Evidence
# --------------------------------------------------------------------------

FAKE_SOURCES = [
    # (category, metric, source, tier, interpretation, content_reference)
    ("price_technical", "rsi_14d", "Binance", "tier0_official",
     "BTC 14 日 RSI 為 71.3，高於 70 的超買門檻",
     {"value": 71.3, "window": "14d", "threshold": 70}),
    ("price_technical", "ma_50d_200d", "Binance", "tier0_official",
     "BTC 50 日均線位於 200 日均線之上（黃金交叉維持中）",
     {"ma50_gt_ma200": True, "windows": [50, 200]}),
    ("price_technical", "volatility_30d", "CoinGecko", "tier2_aggregator",
     "BTC 30 日年化波動率為 42.5",
     {"value": 42.5, "window": "30d"}),
    ("onchain", "active_addresses", "Coin Metrics", "tier0_official",
     "BTC 活躍地址 7 日均值為 912345，較上週上升 4.2",
     {"value": 912345, "window": "7d", "wow_change_pct": 4.2}),
    ("onchain", "exchange_netflow", "Coin Metrics", "tier0_official",
     "過去 24 小時交易所淨流出 5321 BTC",
     {"netflow_btc": -5321, "window": "24h"}),
    ("news", "etf_flow", "Farside Investors", "tier0_official",
     "BTC 現貨 ETF 於 2026-07-31 淨流入 2.1 億美元",
     {"date": "2026-07-31", "net_inflow_usd_m": 2.1e2, "display": "2.1 億"}),
    ("news", "regulatory_announcement", "CoinDesk", "tier1_major_media",
     "SEC 於 2026-07-30 宣布延後某加密 ETF 裁決",
     {"date": "2026-07-30", "headline": "SEC delays decision"}),
    ("sentiment", "fear_greed", "Alternative.me", "tier2_aggregator",
     "恐懼貪婪指數為 74（貪婪區間）",
     {"value": 74, "label": "greed"}),
    ("sentiment", "reddit_activity", "Reddit", "tier3_community",
     "r/Bitcoin 24 小時熱門貼文中看多比例約 63",
     {"bullish_pct": 63, "window": "24h", "sample": 40}),
    ("macro", "dxy", "FRED", "tier0_official",
     "美元指數 DXY 收於 101.8，週線下跌 0.6",
     {"value": 101.8, "wow_change_pct": -0.6}),
]


@logged("collect_fake_evidence", sink=exec_log)
async def collect_fake_evidence() -> list[Evidence]:
    """模擬四路平行蒐證：造 10 筆 Evidence、每筆都留原始快照、入庫。"""
    seq_by_cat: dict[str, int] = {}
    out = []
    for cat, metric, source, tier, interp, ref in FAKE_SOURCES:
        seq_by_cat[cat] = seq_by_cat.get(cat, 0) + 1
        ev_id = make_evidence_id(cat, seq_by_cat[cat])
        snap_key = snaps.save({"fake_raw_response": ref, "source": source},
                              run_id=RUN, name=f"{ev_id}.json")
        ev = Evidence(
            id=ev_id, run_id=RUN, category=cat, metric=metric,
            source=source,
            source_url=f"https://example.com/{source.lower().replace(' ', '')}/api?metric={metric}",
            source_type="api", fetched_at="2026-08-01T02:30:00Z",
            content_reference=ref, interpretation=interp,
            credibility_tier=tier, independence_group=f"grp_{source.lower().replace(' ', '_')}_{metric}",
            collector="demo_agent_v0.1", fetch_duration_ms=42,
            raw_snapshot_key=snap_key,
        )
        store.append(ev)          # 這裡就會過一次入庫驗證
        out.append(ev)
    return out


@logged("build_fake_claims", sink=exec_log)
async def build_fake_claims() -> tuple[list[Claim], list[Conflict]]:
    """模擬推理層產出：3 fact → 1 inference → 1 conclusion + 1 已裁決 Conflict。"""
    t = "2026-08-01T02:36:00Z"
    f1 = Claim(id="c_fact_0001", run_id=RUN, type="fact", author="thesis_agent",
               created_at=t, supported_by=["ev_price_0001"],
               statement="BTC 14 日 RSI 為 71.3，高於 70")
    f2 = Claim(id="c_fact_0002", run_id=RUN, type="fact", author="thesis_agent",
               created_at=t, supported_by=["ev_onchain_0002"],
               statement="過去 24 小時交易所淨流出 5321 BTC")
    f3 = Claim(id="c_fact_0003", run_id=RUN, type="fact", author="devil_advocate",
               created_at=t, supported_by=["ev_sentiment_0001"],
               statement="恐懼貪婪指數為 74，位於貪婪區間")
    inf = Claim(id="c_infer_0001", run_id=RUN, type="inference", author="thesis_agent",
                created_at=t, depends_on=[f1.id, f2.id, f3.id],
                countered_by=["ev_sentiment_0001"],
                statement="技術面 RSI 71.3 過熱與情緒指數 74 偏貪婪，"
                          "但鏈上 5321 BTC 淨流出顯示籌碼移出交易所、拋壓有限")
    concl = Claim(id="c_concl_0001", run_id=RUN, type="conclusion", author="thesis_agent",
                  created_at=t, depends_on=[inf.id],
                  statement="短線技術過熱有回檔風險，中期籌碼結構仍偏多",
                  confidence={"level": "medium", "score": 0.62})
    cf = Conflict(id="cf_0001", run_id=RUN,
                  evidence_ids=["ev_price_0001", "ev_onchain_0002"],
                  description="技術指標過熱（看空）vs 交易所淨流出（看多）",
                  detected_by="agent:devil_advocate",
                  resolution="兩者時間尺度不同：RSI 反映短線、淨流出反映中期，"
                             "分別採信於不同時間框架，不構成互斥",
                  resolved_in_claim=concl.id)
    return [f1, f2, f3, inf, concl], [cf]


@logged("final_gate_and_export", sink=exec_log)
async def final_gate_and_export(claims, conflicts) -> list[str]:
    evidences = store.all()
    errors = validate_run(evidences, claims, conflicts)
    if errors:
        print("✗ 最終驗證閘未通過，擋住輸出：")
        for e in errors:
            print("  -", e)
        return []
    j, c = export_evidence_list(evidences, OUT)
    cit = export_citations(evidences, OUT)
    rep = export_validation_report(RUN, evidences, claims, conflicts, OUT,
                                   generated_at="2026-08-01T02:40:00Z")
    return [str(j), str(c), str(cit), str(rep)]


async def main():
    start_run(RUN)
    evs = await collect_fake_evidence()
    print(f"✓ 入庫 {len(evs)} 筆 Evidence（含 {len(evs)} 份原始快照）")
    claims, conflicts = await build_fake_claims()
    print(f"✓ 產出 {len(claims)} 個 Claim + {len(conflicts)} 個 Conflict")
    files = await final_gate_and_export(claims, conflicts)
    if files:
        print("✓ 最終驗證閘通過，匯出：")
        for f in files:
            print("  -", f)
    print(f"✓ 執行紀錄 {len(exec_log.entries(RUN))} 條 → {exec_log.path}")


if __name__ == "__main__":
    asyncio.run(main())
