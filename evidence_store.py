"""evidence_store.py — Evidence Store（隊員 A 負責維護）

實作策略（已定案，不要過度工程）：
    一個 JSONL 檔 append + 記憶體 dict index。量級只有百筆，不需要資料庫。

給 B / C 的用法（和 EVIDENCE_GUIDE 筆記一致）：

    from evidence_store import store
    store.start_run(run_id)      # 編排層啟動時呼叫一次；B/C 拿到 run_id 後不用管
    store.append(ev)             # 入庫時自動驗證，不合格直接 raise 並列出缺什麼

給 D / 驗證器的用法：

    store.get("ev_price_0001")   # 取單筆
    store.ids()                  # 本 run 所有 Evidence id 的 set
    store.by_category("news")    # 依類別查
    store.all()                  # 本 run 全部 Evidence

測試時想用乾淨的臨時檔，直接自建實例：
    s = EvidenceStore(path=tmp_path / "ev.jsonl")
"""

from __future__ import annotations

import json
from pathlib import Path

from schemas import Evidence
from validator import check_evidence


class EvidenceValidationError(ValueError):
    """入庫驗證失敗。errors 屬性帶著具體錯誤訊息清單（可直接餵 LLM / 印給人看）。"""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("Evidence 入庫驗證失敗：\n- " + "\n- ".join(errors))


class EvidenceStore:
    """Evidence 的唯一入口。

    ⚠️ id 的唯一性範圍是「同一個 run 之內」，不是全域。
    run_20260801_BTC_q1 的 ev_price_0001 與 run_20260801_ETH_q2 的
    ev_price_0001 是兩筆合法且獨立的資料，可以並存 —— 這正是 run_id
    存在的目的。因此索引用 (run_id, id) 複合鍵，撞號也只在同 run 內判定。
    """

    def __init__(self, path: str | Path = "./data/evidence.jsonl"):
        self.path = Path(path)
        # (run_id, id) -> Evidence。用複合鍵，不同 run 的同名 id 不會互相覆蓋
        self._index: dict[tuple[str, str], Evidence] = {}
        # (run_id, category_prefix) -> 已配發到的最大序號（reserve_id 用）
        self._seq: dict[tuple[str, str], int] = {}
        self._run_id: str | None = None
        if self.path.exists():
            self._load()

    # ------------------------------------------------------------------ 載入

    def _load(self) -> None:
        """重啟後從 JSONL 重建記憶體索引（同 run 同 id 才互相覆蓋）。"""
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                ev = Evidence.from_dict(json.loads(line))
                self._index[(ev.run_id, ev.id)] = ev

    # ------------------------------------------------------------------ run 管理

    def start_run(self, run_id: str) -> None:
        """設定本次執行的 run_id。之後 append 的 Evidence 必須帶同一個 run_id。"""
        self._run_id = run_id

    @property
    def run_id(self) -> str | None:
        return self._run_id

    # ------------------------------------------------------------------ id 配號
    # B / C 不要自己數 seq，也不要去讀 JSONL 自己算 —— 跟這裡要號碼

    def reserve_id(self, category: str, run_id: str | None = None) -> str:
        """配發一個本 run 內保證不重複的 Evidence id，例 'ev_price_0003'。

        用法：
            ev_id = store.reserve_id("price_technical")
            ev = Evidence(id=ev_id, ...)
            store.append(ev)

        本方法是同步的、中途沒有 await，所以在 asyncio 四路平行下
        每個呼叫者拿到的號碼一定不同（先配號再去 await 抓資料也安全）。
        ⚠️ 多 process（W3 Lambda 各自跑）不適用，見 README 的待決事項。
        """
        rid = run_id or self._run_id
        if rid is None:
            raise RuntimeError("尚未 start_run()，無法配號")
        prefix = category.split("_")[0]
        key = (rid, prefix)
        if key not in self._seq:
            # 首次配號：從本 run 既有資料的最大序號接續，避免重啟後從 1 重來
            self._seq[key] = self._max_seq(rid, prefix)
        self._seq[key] += 1
        return f"ev_{prefix}_{self._seq[key]:04d}"

    def _max_seq(self, run_id: str, prefix: str) -> int:
        head = f"ev_{prefix}_"
        best = 0
        for (rid, ev_id) in self._index:
            if rid == run_id and ev_id.startswith(head):
                tail = ev_id[len(head):]
                if tail.isdigit():
                    best = max(best, int(tail))
        return best

    # ------------------------------------------------------------------ 寫入

    def append(self, ev: Evidence) -> Evidence:
        """驗證 → 寫 JSONL → 更新索引。驗證不過 raise EvidenceValidationError。"""
        errors = check_evidence(ev)

        # run_id 一致性（筆記自查清單第 4 條）
        if self._run_id is None:
            self._run_id = ev.run_id  # 未顯式 start_run 時，以第一筆為準
        elif ev.run_id != self._run_id:
            errors.append(
                f"Evidence {ev.id} 的 run_id='{ev.run_id}' 與本次執行 "
                f"'{self._run_id}' 不一致（run_id 由編排層傳入，B/C 不要自己造）"
            )

        # 撞號檢查：只在「同一個 run 之內」判定。
        # 不同 run 用同一個 id 是合法的（run_id 就是拿來隔離的）。
        if (ev.run_id, ev.id) in self._index and ev.supersedes != ev.id:
            errors.append(
                f"Evidence id '{ev.id}' 在本次執行（{ev.run_id}）中已存在（撞號）。"
                f"請改用 store.reserve_id(category) 配號；"
                f"若是刻意重抓覆蓋，請在 supersedes 填入被覆蓋的 id"
            )

        if errors:
            raise EvidenceValidationError(errors)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
        self._index[(ev.run_id, ev.id)] = ev
        return ev

    # ------------------------------------------------------------------ 查詢
    # 預設只回傳「目前 run」的資料，避免舊執行殘留混進來（常見坑第 4 點）

    def _current(self) -> list[Evidence]:
        if self._run_id is None:
            return list(self._index.values())
        return [e for e in self._index.values() if e.run_id == self._run_id]

    def get(self, ev_id: str, run_id: str | None = None) -> Evidence:
        """取單筆。預設在目前 run 內找（跨 run 同名 id 不會拿錯）。"""
        rid = run_id or self._run_id
        if rid is not None:
            ev = self._index.get((rid, ev_id))
            if ev is None:
                raise KeyError(f"Evidence id '{ev_id}' 不存在於 run '{rid}'")
            return ev
        for (_, eid), ev in self._index.items():
            if eid == ev_id:
                return ev
        raise KeyError(f"Evidence id '{ev_id}' 不存在")

    def all(self) -> list[Evidence]:
        return self._current()

    def ids(self) -> set[str]:
        return {e.id for e in self._current()}

    def by_id(self) -> dict[str, Evidence]:
        """給驗證器用的 evidence_by_id dict。"""
        return {e.id: e for e in self._current()}

    def by_category(self, category: str) -> list[Evidence]:
        return [e for e in self._current() if e.category == category]

    def count(self) -> int:
        return len(self._current())

    def __contains__(self, ev_id: str) -> bool:
        """`ev_id in store` 判定的是「目前 run 內」是否存在。"""
        return ev_id in self.ids()

    def __len__(self) -> int:
        return self.count()


# 模組層級單例：一般程式碼直接 `from evidence_store import store`
store = EvidenceStore()
