"""exporters.py — 三個核心匯出器（隊員 A 負責維護）

匯出器一 export_evidence_list   → 產出 evidence_list.json + evidence_list.csv（主辦方查帳必看）
匯出器二 build_citation / build_citation_index → 產出報告引註索引（給 W3 的前端渲染引擎用）
匯出器三 export_validation_report → 產出 verification_report.json（用來產生嚴謹度白皮書）

設計原則：全部都只吃記憶體傳進來的物件，並寫入指定的資料夾 (out_dir)。
完全不依賴全域狀態 (Stateless)，非常方便寫測試。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from schemas import Evidence, Claim, Conflict
from validator import validate_run


# ===========================================================================
# 匯出器一：Evidence List（一次產出 JSON + CSV 兩種格式）
# ===========================================================================

# CSV 的欄位順序：為了主辦方檢查方便，把最重要的必查欄位排在前面
_CSV_FIELDS = [
    "id", "run_id", "category", "metric",
    "source", "source_url", "source_type", "fetched_at", "published_at",
    "interpretation", "credibility_tier", "independence_group",
    "collector", "fetch_duration_ms", "raw_snapshot_key",
    "content_reference", "conflict_flags", "related_claims", "supersedes",
]


def export_evidence_list(
    evidences: Iterable[Evidence], out_dir: str | Path
) -> tuple[Path, Path]:
    """輸出 evidence_list.json 與 evidence_list.csv，並回傳這兩個檔案的實體路徑。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True) # 確保資料夾存在，沒有就建一個
    
    # 按照 id 排序，讓輸出的檔案內容整齊、有固定順序
    evidences = sorted(evidences, key=lambda e: e.id)

    # 1. 寫入 JSON 格式
    json_path = out_dir / "evidence_list.json"
    json_path.write_text(
        json.dumps([e.to_dict() for e in evidences], ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    # 2. 寫入 CSV 格式
    csv_path = out_dir / "evidence_list.csv"
    # utf-8-sig 會加上 BOM，確保用 Excel 打開 CSV 時中文不會變成亂碼
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:  
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        
        for e in evidences:
            row = e.to_dict()
            # 因為 CSV 只能存純文字，所以要把原本是 dict 或 list 的複合欄位轉成字串
            row["content_reference"] = json.dumps(row["content_reference"], ensure_ascii=False)
            row["conflict_flags"] = ";".join(row["conflict_flags"])
            row["related_claims"] = ";".join(row["related_claims"])
            writer.writerow(row)

    return json_path, csv_path


# ===========================================================================
# 匯出器二：報告引註（當 W3 前端渲染引擎讀到 [ev_price_0012] 標籤時，會來這裡查資料）
# ===========================================================================

def build_citation(ev: Evidence) -> dict:
    """單筆引註：定義了當使用者在畫面上點開 HTML 錨點時，要顯示的所有內容。"""
    return {
        "id": ev.id,
        "source": ev.source,
        "source_url": ev.source_url,
        "fetched_at": ev.fetched_at,
        "published_at": ev.published_at,
        "credibility_tier": ev.credibility_tier,
        "snippet": ev.interpretation,             # 點開彈出視窗第一眼看到的人話解釋
        "content_reference": ev.content_reference,  # 原始數值或引用的具體片段
        "raw_snapshot_key": ev.raw_snapshot_key,    # 主辦方要抽查原始快照時可以往下挖的 Key
    }


def build_citation_index(evidences: Iterable[Evidence]) -> dict[str, dict]:
    """建立字典索引：格式為 {evidence_id: citation}。
    讓渲染引擎在處理文件時，只要看到 [ev_*] 標籤，就能用 O(1) 速度查到這筆引註。
    """
    return {e.id: build_citation(e) for e in evidences}


def export_citations(
    evidences: Iterable[Evidence], out_dir: str | Path
) -> Path:
    """把引註索引直接落地存成 citations.json（通常直接內嵌進 HTML 報告裡使用）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "citations.json"
    path.write_text(
        json.dumps(build_citation_index(evidences), ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return path


# ===========================================================================
# 匯出器三：驗證報告（用來產出最終的嚴謹度白皮書）
# ===========================================================================

def export_validation_report(
    run_id: str,
    evidences: Iterable[Evidence],
    claims: Iterable[Claim],
    conflicts: Iterable[Conflict],
    out_dir: str | Path,
    generated_at: str = "",
) -> Path:
    """跑一次全面的 validate_run 檢查，並連同各項統計數據一起輸出成 verification_report.json。

    如果 passed=True  → 代表全部過關，打包器會放行輸出報告。
    如果 passed=False → 驗證失敗，打包器會擋下流程並印出 errors 錯誤明細。
    """
    # 確保傳進來的是 list，避免 generator 被消耗掉
    evidences, claims, conflicts = list(evidences), list(claims), list(conflicts)
    errors = validate_run(evidences, claims, conflicts)

    # 統計 Evidence 的可信度與類別分佈
    tiers: dict[str, int] = {}
    cats: dict[str, int] = {}
    for e in evidences:
        tiers[e.credibility_tier] = tiers.get(e.credibility_tier, 0) + 1
        cats[e.category] = cats.get(e.category, 0) + 1

    # 統計 Claim 的各種類型數量
    claim_types: dict[str, int] = {}
    for c in claims:
        claim_types[c.type] = claim_types.get(c.type, 0) + 1

    # 組裝整份驗證報告的資料結構
    report = {
        "run_id": run_id,
        "generated_at": generated_at,
        "passed": not errors,  # 只要 error list 是空的，就代表 passed
        "checks": [
            "completeness", "allowed_values", "timestamp_format",
            "interpretation_number_traceability", "reference_existence",
            "hierarchy_rules", "statement_number_traceability",
            "conflict_coverage",
        ],
        "stats": {
            "evidence_count": len(evidences),
            "evidence_by_category": cats,
            "evidence_by_tier": tiers,
            "independence_groups": len({e.independence_group for e in evidences}),
            "claim_count": len(claims),
            "claims_by_type": claim_types,
            "conflict_count": len(conflicts),
            "conflicts_resolved": sum(1 for cf in conflicts if cf.resolution), # 計算成功解決的衝突數量
        },
        "error_count": len(errors),
        "errors": errors,
    }

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "verification_report.json"
    
    # 將報告寫入 JSON 檔
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return path