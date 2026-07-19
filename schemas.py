"""schemas.py — 全隊共用資料契約
其他模組一律使用：
    from schemas import Evidence, Claim, Conflict, CATEGORIES, ...
如果要加欄位只能改這個檔案，要說一聲

Python 3.10+
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any


COINS = ("BTC", "ETH", "SOL", "BNB", "XRP")

CATEGORIES = ("price_technical", "onchain", "news", "sentiment", "macro")

SOURCE_TYPES = ("api", "rss", "webpage", "dataset", "precomputed")

CREDIBILITY_TIERS = (
    "tier0_official",
    "tier1_major_media",
    "tier2_aggregator",
    "tier3_community",
)

CLAIM_TYPES = ("fact", "inference", "conclusion")

CLAIM_AUTHORS = ("thesis_agent", "devil_advocate")



# ID 產生器
def make_evidence_id(category: str, seq: int) -> str:
    """用來產生 Evidence 的專屬 ID
    例：傳入 ("price_technical", 12)，會回傳 "ev_price_0012"
    """
    short = category.split("_")[0]  # 只取底線前面的第一個字，例：price_technical 變成 price
    return f"ev_{short}_{seq:04d}"


def make_run_id(date: str, coin: str, q: str) -> str:
    """用來產生每次系統執行的專屬 ID (Run ID)。
    例：傳入 ("20260801", "BTC", "q1")，會回傳 "run_20260801_BTC_q1"
    """
    return f"run_{date}_{coin}_{q}"


# Evidence (證據)、Claim (論點/推論)、Conflict (衝突)

# kw_only=True 代表在建立物件時，一定要寫出參數名稱，例：Evidence(id="...", run_id="...")
@dataclass(kw_only=True)
class Evidence:
    """一筆可回溯的原始由 B或C蒐證管線抓回來

    ★ 標註的欄位是主辦方規定的必查項，驗證器會嚴格檢查，絕對不能留空
    """
    id: str                          # Evidence ID，例：ev_price_0012，用 make_evidence_id() 產生
    run_id: str                      # 這次系統執行的流水號，把同一次跑出來的所有資料串在一起
    category: str                    # 必須是 CATEGORIES 裡的其中一個
    metric: str                      # 這個 Evidence 是什麼指標，或是什麼事件
    source: str                      # ★ 資料來源名稱，例如 "Binance"
    source_url: str                  # ★ 完整的網址或 API endpoint，要有查詢參數才能溯源
    source_type: str                 # 必須是 SOURCE_TYPES 裡的其中一個
    fetched_at: str                  # ★ 抓取資料當下的 UTC 時間，符合 ISO 8601，例 "2026-08-01T02:30:00Z"
    content_reference: dict          # ★ 具體引用到的片段、數值或查詢條件（存成字典格式）
    interpretation: str              # 用人話寫的基準事實陳述，推理層（LLM）之後只會看這句話
    credibility_tier: str            # 可信度等級，必須是 CREDIBILITY_TIERS 裡的其中一個
    independence_group: str          # 如果不同新聞都在講同一件事，給它們同一個 group id 綁在一起
    collector: str                   # 是哪隻程式或 Agent 抓的，例 "price_agent_v1.2"
    fetch_duration_ms: int           # 抓這筆資料花了幾毫秒

    # ---- 以下是「可選」或「事後才填」的欄位，一開始抓資料時可以不傳 ----
    published_at: str | None = None          # 新聞或文章發布的時間（不一定有）
    conflict_flags: list[str] = field(default_factory=list)  # 如果有衝突，會在這裡打上記號
    related_claims: list[str] = field(default_factory=list)  # 推理層用完這筆證據後，系統會反向把 Claim ID 寫回來
    supersedes: str | None = None            # 如果這筆資料取代了舊資料，這裡填舊資料的 ID
    raw_snapshot_key: str | None = None      # 原始資料在 AWS S3 的路徑（本機開發時就是本地資料夾路徑）

    def to_dict(self) -> dict[str, Any]:
        """把物件轉成字典 (dict)，方便存進資料庫或轉成 JSON"""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Evidence:
        """把字典 (dict) 還原成 Evidence 物件"""
        return cls(**d)


@dataclass(kw_only=True)
class Claim:
    """D 負責產出的思考節點，總共分三層：fact (事實) -> inference (推論) -> conclusion (結論)。

    驗證器會檢查階層關係：
      - fact       只能掛載 Evidence (寫在 supported_by)，不能依賴別的 Claim (depends_on 必須是空)
      - inference  至少要依賴一個 fact
      - conclusion 只能依賴 inference，而且必須要能一路追溯回最原始的 Evidence
    """
    id: str                          # Claim 的專屬 ID，例：c_fact_001
    run_id: str                      # 綁定是哪一次執行產生的
    type: str                        # 必須是 CLAIM_TYPES 裡的一種 (fact, inference, conclusion)
    statement: str                   # 這個節點推論出來的文字內容
    author: str                      # 是哪個 Agent 寫的，必須在 CLAIM_AUTHORS 內
    created_at: str                  # 產生的 UTC 時間 (ISO 8601)

    supported_by: list[str] = field(default_factory=list)  # 支持這個論點的 Evidence ID 清單（通常 fact 才會填）
    depends_on: list[str] = field(default_factory=list)    # 這個論點是基於哪些上游 Claim 推導出來的
    countered_by: list[str] = field(default_factory=list)  # 反駁這個論點的 Evidence ID 清單
    confidence: dict | None = None                         # 信心水準的分數（conclusion 層才會有，由校準器填寫）

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Claim:
        return cls(**d)


@dataclass(kw_only=True)
class Conflict:
    """用來記錄矛盾的 Evidence 驗證器會檢查，所有的衝突最後都必須有人出來做 resolution"""
    id: str                          # 衝突專屬 ID，例：cf_001
    run_id: str                      # 補上的欄位：原指南雖然漏了，但驗證器需要用這個確保是同一次執行的產物
    evidence_ids: list[str]          # 把打架的那些 Evidence ID 全部列出來
    description: str                 # 說明它們到底哪裡矛盾
    detected_by: str                 # 是誰抓出這個矛盾的，例如 "rule:xxx" 或 "agent:devil_advocate"

    resolution: str | None = None            # 推理層做出的最終裁決（選邊站/說明原因），一定要填！
    resolved_in_claim: str | None = None     # 這個裁決結果具體寫在哪個 Claim 裡面

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Conflict:
        return cls(**d)