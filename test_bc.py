import asyncio
from pathlib import Path

from evidence_store import EvidenceStore, EvidenceValidationError
from log_middleware import start_run
from snapshots import snapshots

# 1. 建立乾淨的臨時倉庫
TEST_RUN_ID = "run_test_bc_001"
TEST_STORE_PATH = Path("./data/test_evidence.jsonl")

# 每次測試前先清空舊的測試檔案
if TEST_STORE_PATH.exists():
    TEST_STORE_PATH.unlink()

# 初始化測試專用的 Store
test_store = EvidenceStore(path=TEST_STORE_PATH)

# 這裡放 B 或 C 的爬蟲主函數 (import 進來)


# 假設這是 B 寫好的測試函數 (裡面必須呼叫 test_store.append(ev))
async def mock_bc_agent_workflow():
    
    # 2. 宣告執行環境 (非常重要！Log 和 Store 都需要這個)
    start_run(TEST_RUN_ID)              # 初始化 Middleware 日誌追蹤[cite: 6]
    test_store.start_run(TEST_RUN_ID)   # 初始化 Store 驗證機制
    
    try:
        # 3. 呼叫你的爬蟲函數 (這裡以 fetch_rsi 為例)
        # ev = await fetch_rsi(run_id=TEST_RUN_ID, coin="BTC", seq=1)
        
        print(f"測試完成！請去檢查 {TEST_STORE_PATH} 以及 ./snapshots/ 目錄")
        print(f"目前臨時倉庫內有 {test_store.count()} 筆資料")
        
    except EvidenceValidationError as e:
        # 4. 捕捉守門員的退件訊息
        print("\n你的 Evidence 被守門員退件了")
        print(e)
    except Exception as e:
        # 捕捉其他爬蟲錯誤 (例如網路斷線)
        print(f"\n發生未預期的錯誤：{e}")


# 執行測試
if __name__ == "__main__":
    asyncio.run(mock_bc_agent_workflow())