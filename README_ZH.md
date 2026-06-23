# Local LLM Proxy & Pipeline - 本地 LLM 代理與管道系統

這是一個在本地執行、具備 repository awareness 的 LLM 代理與工作流程引擎，靈感來自 Anthropic 的 local-pipeline。  
它可以在您的電腦上直接處理聊天請求、分析專案內容、擷取相關檔案，並協調 plan / agent 流程，而不需要把程式碼或提示詞送到外部 API。

## 主要特性

- **本地優先**：提示詞、程式碼與回應都保留在本機
- **具備專案感知**：可自動掃描工作區並擷取相關檔案
- **彈性模型路由**：可為對話、任務規劃、檔案規劃、程式生成、審查與批判配置不同模型
- **支援 plan / agent 工作流程**：可依需求選擇規劃式或代理式執行模式
- **基於 FastAPI**：提供聊天 / completions 與管理用的 HTTP API
- **本地持久化狀態**：將執行狀態與備份儲存在本地 state 目錄

## 專案結構

```text
local-pipeline/
├── main.py                # 應用程式入口
├── llm_proxy.py           # Proxy / 執行入口腳本
├── local_pipeline/
│   ├── api_chat.py        # Chat / completion API 路由
│   ├── api_admin.py       # 管理 API 路由
│   ├── app_state.py       # 共用應用狀態
│   ├── checks.py          # 模型與工作區檢查
│   ├── client.py          # 模型客戶端輔助工具
│   ├── config.py          # 以環境變數驅動的設定
│   ├── db.py              # 本地任務狀態資料庫工具
│   ├── pipeline_agent.py  # Agent 工作流程執行
│   ├── pipeline_plan.py   # Plan 工作流程執行
│   ├── prompts.py         # 系統提示詞
│   ├── repository.py      # Repository 掃描工具
│   ├── retrieval.py       # 上下文擷取工具
│   ├── schemas.py         # 資料模型 / schemas
│   └── ...
├── .repo_aware_state/     # 本地狀態、SQLite DB 與備份
└── README.md
```

## 需求

- Python 3.10+
- 本地模型執行環境，例如：
  - [Ollama](https://ollama.com/)
  - 相容 OpenAI 風格介面的本地端點
- 建議使用 Python 虛擬環境安裝相依套件

## 設定

大部分行為都由 `local_pipeline/config.py` 中的環境變數控制。

常見設定包含：

- `OLLAMA_BASE`：本地模型伺服器的 base URL
- `WORKSPACE_ROOT`：要掃描的 repository / 工作區根目錄
- `STATE_DIR`：本地狀態與備份的存放目錄
- `CHAT_MODEL`：用於直接對話回應的模型
- `TASK_PLANNER_MODEL`：用於任務規劃的模型
- `FILE_PLANNER_MODEL`：用於檔案選擇的模型
- `CODER_MODEL`：用於程式生成的模型
- `REVIEWER_MODEL`：用於審查的模型
- `DEFAULT_APPLY_MODE`：`dry-run` 或 `apply`

完整可用設定與預設值請參考 `local_pipeline/config.py`。

## 快速開始

### 1) 啟動模型執行環境

請先確認您的本地模型服務已啟動並可連線。

以 Ollama 為例：

```bash
ollama serve
ollama pull qwen3.5:9b
```

### 2) 安裝相依套件

```bash
python -m venv env
# Windows
env\Scripts\activate
pip install -r requirements.txt
```

### 3) 啟動服務

```bash
python main.py
```

預設情況下，服務會監聽 `0.0.0.0:18000`。

## API 概覽

### Chat completions

`POST /v1/chat/completions`

此端點會接收類 Chat 的請求、建立 repository context、選擇相關檔案，然後依據需求執行：

- 直接對話回應
- plan pipeline
- agent pipeline

實際流程會依據偵測到的請求內容與模式而定。

### 管理路由

應用程式也透過 `local_pipeline/api_admin.py` 提供管理端點，用於操作與維運。

## 運作流程

1. 解析傳入請求與 request context
2. 掃描工作區 repository
3. 擷取相關檔案
4. 產生任務計畫
5. 產生檔案計畫
6. 執行 plan 或 agent pipeline
7. 將任務狀態持久化到本地

若請求屬於一般對話，系統可略過檔案規劃流程並直接回覆。

## 本地狀態

執行期間的狀態會儲存在 `.repo_aware_state/` 中，包括：

- `tasks.sqlite3`：任務中繼資料
- `backups/`：patch 備份與相關檔案

這些檔案都會在本地產生，通常不應提交到版本控制。

## 開發說明

- 本專案以 FastAPI 應用程式組織
- 設定主要由環境變數控制
- 模型呼叫經由本地 client 層路由
- Repository 掃描會排除常見的建置產物與快取目錄

## 變更紀錄

### 2026-06-23

- 重構非同步請求流程，降低 chat、plan、agent 流程中的 event loop 阻塞。
- 將同步 SQLite 與狀態檔寫入移出主要請求路徑，改用 `asyncio.to_thread(...)`。
- 統一 LLM client/session 的生命週期，並補上關閉時的資源釋放。
- 改善 SSE 中斷語義，避免 client 斷線時仍送出誤導性的 `[DONE]`。
- 維持對外 API 與可見行為不變，同時提升穩定性與回應速度。

## 授權

目前倉庫快照中未包含 license 檔案；若您要公開或散布此專案，建議補上授權資訊。