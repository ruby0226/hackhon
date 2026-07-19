"""snapshots.py — 原始快照儲存（

主辦方防作弊抽查。每一筆 Evidence 的 raw_snapshot_key 都要能在這個庫裡面
找到當時打 API 或爬網頁吐回來的、完全未經加工的 Raw Response

給 B / C 的用法

    from snapshots import snapshots

    # 把抓下來的原始資料存進去，會拿到一組 key
    key = snapshots.save(raw_response_text,
                         run_id="run_20260801_BTC_q1",
                         name="binance_klines_001.json")
                         
    # 把拿到的 key 綁定到 Evidence 的 raw_snapshot_key 欄位上
    ev = Evidence(..., raw_snapshot_key=key)

本機開發時，資料會存到 ./snapshots/ 資料夾下
要正式部署上 AWS 的時候，只要把最下面那行單例切換成 S3SnapshotStore
其他 B/C 端的程式碼連一行都不用改
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SnapshotStore:
    """Interface 定義了所有 SnapshotStore 必須實作的方法
    呼叫 save 會回傳一組 key（用來存進 Evidence.raw_snapshot_key 的字串）
    """

    def save(self, raw: Any, *, run_id: str, name: str) -> str:
        raise NotImplementedError

    def load(self, key: str) -> str:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError


class LocalSnapshotStore(SnapshotStore):
    """把快照以純文字或 JSON 檔案格式存到硬碟裡"""
    
    def __init__(self, root: str | Path = "./snapshots"):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        """把相對路徑的 key 組裝成本機端的完整實體路徑"""
        return self.root / key

    def save(self, raw: Any, *, run_id: str, name: str) -> str:
        """儲存快照 並負責自動轉換資料型態"""
        # 組裝存檔的 key 例如 "run_20260801_BTC_q1/binance_klines_001.json"
        key = f"{run_id}/{name}"
        path = self._path(key)
        
        # 確保該 run_id 對應的資料夾有被建出來
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # 判斷傳進來的原始資料是什麼型態，把它轉成文字
        if isinstance(raw, (dict, list)):
            # 如果是 JSON 物件，就幫忙轉成格式化的 JSON 字串
            text = json.dumps(raw, ensure_ascii=False, indent=1)
        elif isinstance(raw, bytes):
            # 如果是 byte，就幫忙解碼成 UTF-8 字串（遇到亂碼用替代字元取代，防當機）
            text = raw.decode("utf-8", errors="replace")
        else:
            # 其他東西就直接硬轉成字串
            text = str(raw)
            
        # 寫入檔案
        path.write_text(text, encoding="utf-8")
        return key

    def load(self, key: str) -> str:
        """用 key 把存好的快照檔案讀出來。"""
        return self._path(key).read_text(encoding="utf-8")

    def exists(self, key: str) -> bool:
        """檢查這個 key 的檔案存不存在。"""
        return self._path(key).exists()


class S3SnapshotStore(SnapshotStore):
    """W3 正式版要上 AWS 的骨架：介面完全相同，只是裡面換成 boto3 實作。

    def __init__(self, bucket: str, prefix: str = "snapshots/"):
        import boto3
        self.s3 = boto3.client("s3"); self.bucket = bucket; self.prefix = prefix

    save   → self.s3.put_object(Bucket=..., Key=self.prefix+key, Body=text)
    load   → self.s3.get_object(...)["Body"].read().decode()
    exists → head_object + except ClientError
    """

    def __init__(self, bucket: str, prefix: str = "snapshots/"):
        # 目前還沒實作，如果在本地端不小心叫到它會報錯
        raise NotImplementedError("W3 部署 AWS 時才實作，本地開發請用 LocalSnapshotStore")


# 其他模組直接 `from snapshots import snapshots` 就能用
# 測試環境或正式上線要切換儲存方式，只要改底下這行就好
snapshots: SnapshotStore = LocalSnapshotStore()