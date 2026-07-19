"""evidence_store.py — Evidence Store

就用一個 JSONL 檔案不斷 append 寫入，搭配記憶體裡的 dict 做 index 查詢
資料量少(幾百筆)，先放記憶體就好，資料庫下禮拜會改

給 B / C的用法（和筆記一樣）

    from evidence_store import store
    store.start_run(run_id)      # 系統一開始跑的時候呼叫一次就好 拿到 run_id 後不用管
    store.append(ev)             # 把 Evidence 存進去，系統會自動驗證，不合格會直接 raise 報錯並告訴你缺什麼

給 D

    store.get("ev_price_0001")   # 拿特定的某一筆
    store.ids()                  # 拿到這次 run 裡面所有的 Evidence id (回傳 set)
    store.by_category("news")    # 根據 category 把相關的證據全撈出來
    store.all()                  # 拿這次 run 的全部 Evidence

測試時想用乾淨的臨時檔，可以直接自己建立一個
    s = EvidenceStore(path=tmp_path / "ev.jsonl")
"""

from __future__ import annotations
import json
from pathlib import Path
from schemas import Evidence
from validator import check_evidence


class EvidenceValidationError(ValueError):
    """專門用來處理入庫驗證失敗的錯誤類別。
    裡面包了一個 errors 屬性，裝著所有具體的錯誤訊息
    """

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("Evidence 入庫驗證失敗：\n- " + "\n- ".join(errors))


class EvidenceStore:
    def __init__(self, path: str | Path = "./data/evidence.jsonl"):
        self.path = Path(path)
        self._index: dict[str, Evidence] = {}   # 用來把 id 對應到 Evidence 物件的字典（包含歷史所有的資料）
        self._run_id: str | None = None         # 記錄當下這次執行的 run_id
        
        # 如果檔案已經存在，就先把舊資料讀進記憶體
        if self.path.exists():
            self._load()

    # 載入

    def _load(self) -> None:
        """系統重啟時，把 JSONL 檔案裡的資料一行一行讀出來，重建記憶體裡的 index
        如果有重複的 id，後面讀到的會直接覆蓋前面的
        """
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue  # 跳過空行
                ev = Evidence.from_dict(json.loads(line))
                self._index[ev.id] = ev

    # run 管理

    def start_run(self, run_id: str) -> None:
        """設定這次大流程的 run_id 設定好之後，後面 append 進來的 Evidence 都要是這個 run_id"""
        self._run_id = run_id

    @property
    def run_id(self) -> str | None:
        return self._run_id

    # 寫入

    def append(self, ev: Evidence) -> Evidence:
        """把資料存進去先 驗證 → 寫入 JSONL 檔案 → 更新記憶體 index
        如果驗證沒過，會直接丟出 EvidenceValidationError 擋下來
        """
        errors = check_evidence(ev)

        # 檢查 run_id 有沒有一致
        if self._run_id is None:
            self._run_id = ev.run_id  # 如果沒人呼叫過 start_run，就以第一筆收到的資料為準
        elif ev.run_id != self._run_id:
            errors.append(
                f"Evidence {ev.id} 的 run_id='{ev.run_id}' 與本次執行 "
                f"'{self._run_id}' 不一致（run_id 由編排層傳入，B/C 不要自己造）"
            )

        # 檢查有沒有撞號（同一個 id 已經存在了）
        # 除非這筆資料明確在 supersedes 寫說我要覆蓋我自己，才能放行
        if ev.id in self._index and ev.supersedes != ev.id:
            errors.append(
                f"Evidence id '{ev.id}' 已存在"
                f"若是刻意重抓覆蓋，請在 supersedes 填入被覆蓋的 id"
            )

        # 只要有任何錯誤，立刻拋出 Exception 中斷
        if errors:
            raise EvidenceValidationError(errors)

        # 確保資料夾存在，然後用 append (a) 模式把 JSON 寫到檔案最後一行
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
            
        # 更新記憶體裡的 dict
        self._index[ev.id] = ev
        return ev

    # 查詢
    # 預設只回傳目前這個 run 的資料，避免把以前跑的舊資料混進來

    def _current(self) -> list[Evidence]:
        """只挑出屬於目前 run_id 的 Evidence"""
        if self._run_id is None:
            return list(self._index.values())
        return [e for e in self._index.values() if e.run_id == self._run_id]

    def get(self, ev_id: str) -> Evidence:
        """用 id 拿單筆資料。如果找不到就報 KeyError"""
        ev = self._index.get(ev_id)
        if ev is None:
            raise KeyError(f"Evidence id '{ev_id}' 不存在")
        return ev

    def all(self) -> list[Evidence]:
        """拿這次 run 的全部 Evidence"""
        return self._current()

    def ids(self) -> set[str]:
        """拿這次 run 所有 Evidence 的 id 清單"""
        return {e.id for e in self._current()}

    def by_id(self) -> dict[str, Evidence]:
        """把這次 run 的資料打包成 {id: Evidence} 的 dict 格式，主要是給驗證器對答案用的"""
        return {e.id: e for e in self._current()}

    def by_category(self, category: str) -> list[Evidence]:
        """根據特定的分類 (例如 "news" 或 "onchain") 撈出這次 run 的相關證據"""
        return [e for e in self._current() if e.category == category]

    def count(self) -> int:
        """算一下這次 run 總共有幾筆 Evidence"""
        return len(self._current())

    def __contains__(self, ev_id: str) -> bool:
        """讓你可以用 `if "ev_001" in store:` 這種寫法來檢查資料存不存在"""
        return ev_id in self._index

    def __len__(self) -> int:
        """讓你可以用 `len(store)` 來取得目前 run 的證據總數"""
        return self.count()


# 直接 `from evidence_store import store` 就可以共用同一個例了
store = EvidenceStore()