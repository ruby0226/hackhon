"""validator.py — 五條驗證規則

1. 不管執行幾次，只要輸入一樣，輸出就一定一樣 不碰外部資料庫或全域變數，方便寫 pytest
2. 一律回傳 list[str] 錯誤訊息：沒錯就回傳空陣列 []
   絕對不回傳 True/False，因為要把具體的錯誤訊息丟給 LLM
"""

from __future__ import annotations
import json
import re
from typing import Iterable
from schemas import Evidence, Claim, Conflict, CLAIM_TYPES



# 規則 1：完整性檢查 —— 確保主辦方規定的必填欄位都沒有漏掉

REQUIRED_EVIDENCE_FIELDS = ("source", "source_url", "fetched_at", "content_reference")


def check_completeness(ev: Evidence) -> list[str]:
    errors: list[str] = []
    # 巡過每一個必填欄位
    for field_name in REQUIRED_EVIDENCE_FIELDS:
        value = getattr(ev, field_name)
        # 檢查是不是 None、是不是空字串、或是空的字典
        if value is None or (isinstance(value, str) and not value.strip()) \
           or (isinstance(value, dict) and not value):
            errors.append(
                f"Evidence {ev.id} 缺少必填欄位 {field_name} 主辦查項不能空"
            )
    return errors



# 規則 1 延伸：檢查 Evidence 裡面的資料格式（存進 evidence_store 時會呼叫）

from schemas import CATEGORIES, SOURCE_TYPES, CREDIBILITY_TIERS

# 用 Regex 檢查時間格式是否符合 ISO 8601 UTC 標準
_ISO_UTC_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$"
)


def check_allowed_values(ev: Evidence) -> list[str]:
    """檢查 category、source_type、credibility_tier 有沒有亂填"""
    errors: list[str] = []
    if ev.category not in CATEGORIES:
        errors.append(
            f"Evidence {ev.id} 的 category='{ev.category}' 不對 要是 {'/'.join(CATEGORIES)} 之一"
        )
    if ev.source_type not in SOURCE_TYPES:
        errors.append(
            f"Evidence {ev.id} 的 source_type='{ev.source_type}' 不對 要是 {'/'.join(SOURCE_TYPES)} 之一"
        )
    if ev.credibility_tier not in CREDIBILITY_TIERS:
        errors.append(
            f"Evidence {ev.id} 的 credibility_tier='{ev.credibility_tier}' 不對 要是 {'/'.join(CREDIBILITY_TIERS)} 之一"
        )
    return errors


def check_timestamp_format(ev: Evidence) -> list[str]:
    """檢查 fetched_at 和 published_at（如果有填的話）是不是標準的 UTC 時間格式"""
    errors: list[str] = []
    if ev.fetched_at and not _ISO_UTC_RE.match(ev.fetched_at):
        errors.append(
            f"Evidence {ev.id} 的 fetched_at='{ev.fetched_at}' 不是 UTC ISO 8601 格式"
            f"（正確範例：2026-08-01T02:30:00Z；注意不要用台北時間 +08:00）"
        )
    if ev.published_at and not _ISO_UTC_RE.match(ev.published_at):
        errors.append(
            f"Evidence {ev.id} 的 published_at='{ev.published_at}' 不是 UTC ISO 8601 格式"
            f"（正確範例：2026-07-30T14:05:00Z）"
        )
    return errors


def check_interpretation_numbers(ev: Evidence) -> list[str]:
    """確保 LLM 寫在 interpretation 裡的數字，真的都有出現在 content_reference 裡，防止 LLM 產生幻覺瞎掰"""
    numbers = _extract_numbers(ev.interpretation)
    if not numbers:
        return [] # 沒提到數字就直接過關
        
    # 把 content_reference 轉成文字，並統一格式
    ref_text = _normalize(json.dumps(ev.content_reference, ensure_ascii=False, default=str))
    
    # 預先把原始內容裡的數字轉成 float 存起來備用
    ref_floats = set()
    for n in _extract_numbers(ref_text):
        try:
            ref_floats.add(float(n))
        except ValueError:
            pass

    errors: list[str] = []
    for num in numbers:
        # 如果字串直接比對有中就過關
        if num in ref_text:
            continue
        # 字串沒中，試試看轉成 float 比對
        try:
            if float(num) in ref_floats:
                continue
        except ValueError:
            pass
        # 都比對不到，這數字就是瞎掰的
        errors.append(
            f"Evidence {ev.id} 的 interpretation 出現數字 {num} 但在 content_reference 中找不到"
        )
    return errors


def check_evidence(ev: Evidence) -> list[str]:
    """打包上面 4 個 Evidence 專屬的檢查：有沒有漏填 ＋ 填的對不對 ＋ 時間格式 ＋ 數字有沒有幻覺"""
    return (
        check_completeness(ev)
        + check_allowed_values(ev)
        + check_timestamp_format(ev)
        + check_interpretation_numbers(ev)
    )


# 規則 2：引用存在性檢查 —— 確保 Claim 關聯到的 Evidence 或上游 Claim 真的存在，且是同一次執行的產物

def check_reference_existence(
    claim: Claim,
    evidence_by_id: dict[str, Evidence],
    claims_by_id: dict[str, Claim],
) -> list[str]:
    errors: list[str] = []

    # 檢查該 Claim 拿出支持或反駁的 Evidence ID 是不是真的存在
    for ev_id in claim.supported_by + claim.countered_by:
        ev = evidence_by_id.get(ev_id)
        if ev is None:
            errors.append(
                f"Claim {claim.id} 引用了不存在的 Evidence id：{ev_id}"
            )
        # 確保不會拿前次跑出的資料來混搭
        elif ev.run_id != claim.run_id:
            errors.append(
                f"Claim {claim.id}（run_id={claim.run_id}）引用了不同執行的 Evidence {ev_id}（run_id={ev.run_id}）不可以跨 run 引用"
            )

    # 檢查該 Claim 依賴的上游 Claim ID 是不是真的存在
    for c_id in claim.depends_on:
        upstream = claims_by_id.get(c_id)
        if upstream is None:
            errors.append(
                f"Claim {claim.id} 的 depends_on 引用了不存在的 Claim id：{c_id}"
            )
        elif upstream.run_id != claim.run_id:
            errors.append(
                f"Claim {claim.id}（run_id={claim.run_id}）依賴了不同執行的 Claim {c_id}（run_id={upstream.run_id}）跨 run 依賴不允許"
            )
        elif c_id == claim.id:
            errors.append(f"Claim {claim.id} 的 depends_on 不能依賴自己") # 抓出自己依賴自己的蠢事

    return errors


# 規則 3：層次規則檢查 —— 邏輯鏈條必須是：fact -> inference -> conclusion

def _chain_reaches_evidence(
    claim_id: str,
    claims_by_id: dict[str, Claim],
    _visited: frozenset[str] = frozenset(),
) -> bool:
    """沿著 depends_on 往上找，看看最後能不能摸到帶有 Evidence 的 fact 節點
    會把走過的節點記錄在 _visited，防止 LLM 鬼打牆"""
    if claim_id in _visited:
        return False  # 發生循環依賴，當作找不到證據處理
    node = claims_by_id.get(claim_id)
    if node is None:
        return False
    # 如果找到了 fact，檢查它有沒有掛載證據
    if node.type == "fact":
        return bool(node.supported_by)
    # 如果還沒到 fact 卻斷尾了，也是 False
    if not node.depends_on:
        return False
        
    visited = _visited | {claim_id}
    # 上游的每一個分支都必須能走到 Evidence
    return all(
        _chain_reaches_evidence(up, claims_by_id, visited) for up in node.depends_on
    )


def check_hierarchy(claim: Claim, claims_by_id: dict[str, Claim]) -> list[str]:
    """檢查邏輯階層有沒有遵守 fact -> inference -> conclusion 的規定"""
    errors: list[str] = []

    if claim.type not in CLAIM_TYPES:
        errors.append(
            f"Claim {claim.id} 的 type='{claim.type}' 不對 要是 {'/'.join(CLAIM_TYPES)} 之一"
        )
        return errors

    # 如果是最低階的 fact，必須要有證據，且不能再往上依賴別人
    if claim.type == "fact":
        if not claim.supported_by:
            errors.append(
                f"Claim {claim.id}（fact）必須至少掛載一個 Evidence（supported_by 為空）"
            )
        if claim.depends_on:
            errors.append(
                f"Claim {claim.id}（fact）不得依賴其他 Claim depends_on 必須為空，目前為 {claim.depends_on}"
            )

    # 如果是中階的 inference，它依賴的清單中 至少要有 1 個 fact
    elif claim.type == "inference":
        upstream_types = [
            claims_by_id[c].type for c in claim.depends_on if c in claims_by_id
        ]
        if "fact" not in upstream_types:
            errors.append(
                f"Claim {claim.id}（inference）必須至少依賴一個 fact 節點"
                f"（目前上游類型：{upstream_types or '無'}）"
            )

    # 如果是最高階的 conclusion，它必須依賴 inference，而且要能一路往下追溯到真正的 Evidence
    elif claim.type == "conclusion":
        if not claim.depends_on:
            errors.append(f"Claim {claim.id}（conclusion）必須依賴至少一個 inference")
        for c_id in claim.depends_on:
            upstream = claims_by_id.get(c_id)
            if upstream is not None and upstream.type != "inference":
                errors.append(
                    f"Claim {claim.id}（conclusion）只能依賴 inference "
                    f"但 {c_id} 是 {upstream.type}"
                )
        if claim.depends_on and not all(
            _chain_reaches_evidence(c, claims_by_id) for c in claim.depends_on
        ):
            errors.append(
                f"Claim {claim.id}（conclusion）的依賴鏈無法一路追溯到 Evidence"
            )

    return errors



# 規則 4：數字溯源檢查 —— statement 裡出現的數字，一定要在它引用的證據裡找得到


# 抓出數字的 Regex（包含整數、有逗號的千分位、有小數點的）
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _normalize(text: str) -> str:
    # 把千分位的逗號拔掉
    return re.sub(r"(?<=\d),(?=\d)", "", text)


def _extract_numbers(text: str) -> list[str]:
   # 把一段字串裡面的所有數字挖出來，回傳一個清單
    return _NUMBER_RE.findall(_normalize(text))


def _evidence_text(ev: Evidence) -> str:
    # 把 Evidence 的解讀內容跟原始資料合併成一整串文字，用來作為比對的靶子
    ref = json.dumps(ev.content_reference, ensure_ascii=False, default=str)
    return _normalize(f"{ev.interpretation} {ref}")


def check_number_traceability(
    claim: Claim, evidence_by_id: dict[str, Evidence], claims_by_id: dict[str, Claim] | None = None
) -> list[str]:
    """ 檢查 Claim 的 statement 提到的每個數字，有沒有在它引用的證據裡出現過
    
    無視千分位（1,000 vs 1000）、百分比（10% vs 10）、和尾數的零（71.3 vs 71.30)
    """
    numbers = _extract_numbers(claim.statement)
    if not numbers:
        return []

    # 把這個 claim 相關的 Evidence ID 全部搜集起來
    ev_ids: set[str] = set(claim.supported_by)
    # 如果有給全部的 claims 資料，就沿著 depends_on 爬上去找更多關聯的證據
    if claims_by_id is not None:
        stack, seen = list(claim.depends_on), set()
        while stack:
            cid = stack.pop()
            if cid in seen:
                continue
            seen.add(cid)
            node = claims_by_id.get(cid)
            if node is None:
                continue
            ev_ids.update(node.supported_by)
            stack.extend(node.depends_on)

    # 把所有相關證據的文字全拼在一起
    source_text = " ".join(
        _evidence_text(evidence_by_id[eid]) for eid in ev_ids if eid in evidence_by_id
    )
    
    # 預先把證據裡的數字轉成 float 存起來 無視 71.3 跟 71.30 的差異
    source_floats = set()
    for n in _extract_numbers(source_text):
        try:
            source_floats.add(float(n))
        except ValueError:
            pass

    errors: list[str] = []
    for num in numbers:
        # 字串比對有中就過
        if num in source_text:
            continue
        # 轉成浮點數比對有中也過
        try:
            if float(num) in source_floats:
                continue
        except ValueError:
            pass
        # 還是找不到 判定為幻覺數字
        errors.append(
            f"Claim {claim.id} 的 statement 出現數字「{num}」，"
            f"但在所引用證據（{sorted(ev_ids) or '無引用'}）的 "
            f"content_reference/interpretation 中找不到，疑似幻覺數字"
        )
    return errors



# 規則 5：矛盾覆蓋檢查 —— 每一個 Conflict 推理層都必須給個交代


def check_conflict_coverage(
    conflict: Conflict, claims_by_id: dict[str, Claim] | None = None
) -> list[str]:
    errors: list[str] = []
    # 發現衝突卻沒有寫解決方案
    if conflict.resolution is None or not conflict.resolution.strip():
        errors.append(
            f"Conflict {conflict.id} 尚未提供 resolution 裁決"
            f"（採信哪邊/為何/或承認無法裁決）"
        )
    # 檢查裁決結果所指向的 Claim ID 是否真的存在
    if (
        conflict.resolved_in_claim
        and claims_by_id is not None
        and conflict.resolved_in_claim not in claims_by_id
    ):
        errors.append(
            f"Conflict {conflict.id} 的 resolved_in_claim 指向不存在的 Claim id：{conflict.resolved_in_claim} "
        )
    return errors



# 單一 Claim 驗證 ＋ 整個 run 的最終驗證

def validate_claim(
    claim: Claim,
    evidence_by_id: dict[str, Evidence],
    claims_by_id: dict[str, Claim],
) -> list[str]:
    """給推理層重試用的函式，一次只驗證一個 Claim"""
    errors: list[str] = []
    errors += check_reference_existence(claim, evidence_by_id, claims_by_id)
    errors += check_hierarchy(claim, claims_by_id)
    errors += check_number_traceability(claim, evidence_by_id, claims_by_id)
    return errors


def validate_run(
    evidences: Iterable[Evidence],
    claims: Iterable[Claim],
    conflicts: Iterable[Conflict],
) -> list[str]:
    """有任何錯誤就擋下不給過"""
    evidences, claims, conflicts = list(evidences), list(claims), list(conflicts)
    evidence_by_id = {e.id: e for e in evidences}
    claims_by_id = {c.id: c for c in claims}

    errors: list[str] = []
    for ev in evidences:
        errors += check_evidence(ev)
    for cl in claims:
        errors += validate_claim(cl, evidence_by_id, claims_by_id)
    for cf in conflicts:
        errors += check_conflict_coverage(cf, claims_by_id)
    return errors