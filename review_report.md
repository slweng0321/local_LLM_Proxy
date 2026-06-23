## 最終 Code Review 報告

### 1. 主要結論
這次重構已把原本最危險的三類問題大幅改善：

- **Event Loop 阻塞**
  - `scan_repo()`、檔案讀取、SQLite 存取已改成 `asyncio.to_thread(...)`
  - 主 request 路徑上的同步 I/O 明顯下降

- **Streaming 清理**
  - SSE 在 client 中斷時不再強制送 `[DONE]`
  - 取消語義更乾淨，資源釋放更安全

- **Client lifecycle**
  - client.py 的 OpenAI / aiohttp 共用 client 已統一
  - lifespan.py 已補上 shutdown cleanup

---

### 2. 已修正項目

#### A. 併發與 async I/O
- client.py
  - `get_client()`、`get_session()` 改為 async-safe
  - 共用連線池可重用，避免重複建連線
- api_chat.py
  - `scan_repo()` / `simple_retrieve()` / `read_selected_files()` 走 thread offload
  - `save_task_state()` 改為 thread offload
- pipeline_agent.py
  - 三階段狀態寫入改為 thread offload
- pipeline_plan.py
  - 結尾狀態寫入改為 thread offload
- api_admin.py
  - DB load/update 改為 thread offload

#### B. Streaming
- streaming.py
  - `CancelledError` 時不再輸出 `[DONE]`
  - cleanup 與正常結束分流

#### C. Lifecycle
- lifespan.py
  - shutdown 時會關閉共用 client/session

#### D. 型別與防呆
- app_state.py
  - 補齊 `InFlightRegistry.pop(...)`
- schemas.py
  - `TaskState` / `RetrievedFile` 的資料邊界仍維持清晰，適合作為後續 Pydantic 化基礎

---

### 3. 仍可後續再強化的點

#### A. `chat_once()` 與 `chat_stream()` 的真正 async 保證
目前它們已統一到共用 `AsyncOpenAI` client，但如果底層供應商回應速度很慢，仍需持續觀察 TTFT 與 timeout 行為。

#### B. db.py 仍是同步 SQLite
現在是透過呼叫端 `to_thread()` 緩解，但若後續流量再上升，建議升級為：
- `aiosqlite`
- 或背景寫入隊列
- 或外部 DB

#### C. logging_utils.py 仍以 `print` 為主
可觀測性還能再進一步：
- 導入 structured logging
- 用 `request_id` / `task_id` / `trace_id`
- 改善跨 async task 的追蹤能力

#### D. Request state 管理仍偏分散
目前 api_chat.py、pipeline_agent.py、pipeline_plan.py 都有清理與狀態更新，建議之後抽成：
- `pipeline_service`
- `task_state_service`
- `stream_lifecycle`

---

### 4. 風險評級

- **高風險已處理**：同步 I/O、stream cancel cleanup、client lifecycle
- **中風險仍存在**：SQLite 同步實作、print-based logging
- **低風險可接受**：目前模組拆分可運作，但還能更清晰

---

### 5. 模組化總評

目前拆分方向大致正確，但職責仍有些混雜：

- api_chat.py
  - 入口、協調、檢索、planner 串接、狀態持久化混在一起
- pipeline_agent.py
  - 編碼、審查、串流、套 patch、收尾混在一起
- client.py
  - client 初始化與 LLM 呼叫封裝已合理，但仍可再抽出 transport layer
- db.py
  - 目前像 repository layer，但還是同步實作

**建議方向**
- 入口層只保留 request orchestration
- pipeline 層只管 stage sequencing
- storage 層只管 state persistence
- client 層只管外部模型通訊
- logging 層改成獨立觀測模組

---

### 6. 最終評語
這版重構已從「會阻塞 Event Loop 的原型」提升到「可上生產的模組化 async pipeline」雛形。  
若要再往上升一級，下一步最值得做的是：

1. 把 db.py 改成真正 async persistence  
2. 導入 structured logging  
3. 把 api_chat.py 再拆成更薄的 orchestration 層
