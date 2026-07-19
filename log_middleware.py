"""log_middleware.py — 執行紀錄中介層（隊員 A 負責維護）

鐵律：所有工具與 LLM 呼叫由 middleware 自動寫 Log，禁止事後補寫。

給 B / C / D 的用法（總共三行）：

    from log_middleware import logged, start_run

    start_run("run_20260801_BTC_q1")        # 編排層（A）在 run 開始呼叫一次

    @logged("fetch_price_binance")           # 掛在你的函數頭上就完事
    async def fetch_price(symbol: str, api_key: str = ""):
        ...
        return evidences                     # 回傳的 Evidence/Claim id 會自動記進 log

    # 同步函數也可以掛，不用改成 async：
    @logged("precompute_rsi")
    def precompute_rsi(csv_path: str):
        ...

抓取失敗要降級改用備援來源時，先標記再拋出/切換：

    from log_middleware import set_fallback
    set_fallback("coingecko_backup")         # 這筆會出現在該次錯誤 log 的 fallback 欄

⚠️ 金鑰安全：params 寫入前會經過 sanitize() —— 依參數名遮蔽（api_key/token/...）
   並清洗字串值內的 URL 金鑰參數（?apikey=xxx）。但這是保險絲不是免死金牌：
   不要把金鑰塞進奇怪的參數名裡（例如 k="..."），命名照常識來它才擋得住。
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ===========================================================================
# 執行情境（contextvars：asyncio 四路平行下每個任務各自獨立，不會互踩）
# ===========================================================================

_run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "run_id", default=None
)
_fallback_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_fallback", default=None
)


def start_run(run_id: str) -> None:
    """編排層在每次 run 開始時呼叫。之後所有 log 都會綁上這個 run_id。"""
    _run_id_var.set(run_id)
    _fallback_var.set(None)


def current_run_id() -> str | None:
    return _run_id_var.get()


def set_fallback(name: str | None) -> None:
    """某路資料源失敗、準備切換備援前呼叫，錯誤 log 會帶上這個標記。"""
    _fallback_var.set(name)


# ===========================================================================
# sanitize：金鑰白名單過濾（★ 絕對不能拿掉，Log 要打包提交給主辦方）
# ===========================================================================

_SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|apikey|secret|token|password|passwd|authorization|auth|"
    r"credential|private[_-]?key|access[_-]?key|signature)",
    re.IGNORECASE,
)

# 清洗字串值內的 URL 查詢參數，例 ...&apikey=ABC123 → ...&apikey=***
_URL_KEY_RE = re.compile(
    r"((?:api[_-]?key|apikey|token|secret|key|access[_-]?key)=)([^&\s\"']+)",
    re.IGNORECASE,
)

_MAX_VALUE_LEN = 300  # 超長參數截斷，避免整包回應被塞進 log


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return sanitize(value)
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(v) for v in value]
    if isinstance(value, str):
        cleaned = _URL_KEY_RE.sub(r"\1***", value)
        if len(cleaned) > _MAX_VALUE_LEN:
            cleaned = cleaned[:_MAX_VALUE_LEN] + f"...(truncated {len(cleaned)} chars)"
        return cleaned
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return repr(value)[:_MAX_VALUE_LEN]  # 其他物件存 repr，避免不可序列化


def sanitize(params: dict[str, Any]) -> dict[str, Any]:
    """遮蔽敏感參數。key 命中敏感名單 → 整個值換成 ***；其餘遞迴清洗值。"""
    out: dict[str, Any] = {}
    for k, v in params.items():
        if _SENSITIVE_KEY_RE.search(k):
            out[k] = "***"
        else:
            out[k] = _sanitize_value(v)
    return out


# ===========================================================================
# extract_ids：從回傳值萃取產出的 Evidence / Claim / Conflict id
# ===========================================================================

def extract_ids(result: Any) -> list[str]:
    """支援：單一物件、list、dict、或任何帶 .id 的東西。認 ev_/c_/cf_ 前綴。"""
    ids: list[str] = []

    def visit(obj: Any) -> None:
        if obj is None:
            return
        if isinstance(obj, str):
            if obj.startswith(("ev_", "c_", "cf_")):
                ids.append(obj)
            return
        if isinstance(obj, (list, tuple, set)):
            for item in obj:
                visit(item)
            return
        if isinstance(obj, dict):
            visit(obj.get("id"))
            return
        visit(getattr(obj, "id", None))

    visit(result)
    return ids


# ===========================================================================
# JSONL 寫入
# ===========================================================================

class ExecutionLog:
    def __init__(self, path: str | Path = "./data/execution_log.jsonl"):
        self.path = Path(path)

    def append_jsonl(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def entries(self, run_id: str | None = None) -> list[dict[str, Any]]:
        """讀回 log（打包器與 execution_summary 產生器用）。"""
        if not self.path.exists():
            return []
        out = []
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                e = json.loads(line)
                if run_id is None or e.get("run_id") == run_id:
                    out.append(e)
        return out


log = ExecutionLog()  # 模組層級單例；測試時可自建 ExecutionLog(path=臨時檔)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ===========================================================================
# logged 裝飾器本體（async 與同步函數都支援）
# ===========================================================================

def logged(step_name: str, *, sink: ExecutionLog | None = None):
    """掛在任何工具/LLM 呼叫函數上，自動記錄：
    ts / run_id / step / params(已消毒) / status / duration_ms / produced / fallback
    例外照常往上拋（讓管線的降級機制接手），但 log 一定會先寫下來。
    """

    def wrap(fn):
        target_log = sink or log

        def _make_entry(kwargs: dict) -> dict:
            return {
                "ts": _utc_now(),
                "run_id": _run_id_var.get(),
                "step": step_name,
                "func": fn.__qualname__,
                "params": sanitize(kwargs),
                "status": None,
                "duration_ms": None,
                "produced": [],
            }

        def _on_success(entry: dict, result: Any) -> None:
            entry["status"] = "ok"
            entry["produced"] = extract_ids(result)

        def _on_error(entry: dict, e: Exception) -> None:
            entry["status"] = f"error:{type(e).__name__}"
            entry["error"] = str(e)[:_MAX_VALUE_LEN]
            entry["fallback"] = _fallback_var.get()

        def _finish(entry: dict, t0: float) -> None:
            entry["duration_ms"] = int((time.monotonic() - t0) * 1000)
            target_log.append_jsonl(entry)

        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def inner(*args, **kwargs):
                entry = _make_entry(kwargs)
                t0 = time.monotonic()
                try:
                    result = await fn(*args, **kwargs)
                    _on_success(entry, result)
                    return result
                except Exception as e:
                    _on_error(entry, e)
                    raise
                finally:
                    _finish(entry, t0)

            return inner

        @functools.wraps(fn)
        def inner_sync(*args, **kwargs):
            entry = _make_entry(kwargs)
            t0 = time.monotonic()
            try:
                result = fn(*args, **kwargs)
                _on_success(entry, result)
                return result
            except Exception as e:
                _on_error(entry, e)
                raise
            finally:
                _finish(entry, t0)

        return inner_sync

    return wrap
