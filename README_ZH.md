# Local LLM Proxy & Pipeline - 本地 LLM 代理與管道系統

這是 Anthropic [local-pipeline](https://github.com/localai/pipeline) 的本地實現，經過改造可本地運行而無需擔心 API 成本和隱私問題。該專案在您的應用程式和各種開源大語言模型 (LLM) 之間提供了一個輕量級代理服務層。

## 主要特性

- 🔒 **安全第一**：所有資料都保留在本地 - 無需將提示傳送到外部 API
- 💰 **經濟高效**：零推理訂閱費用
- 🚀 **快速開發**：現成的模板和腳手架支援常見用例
- ⚙️ **靈活配置**：透過統一介面支援多種模型後端 (Ollama、vLLM 等)
- 🔌 **易於整合**：模擬 Anthropic Messages SDK 的 REST API 端點

## 專案結構

```
local-pipeline/
├── api_chat.py # Chat 請求的主要 API 端點處理器
├── client.py # 客戶端工具與請求建構器
├── checks.py #模型驗證與效能偵測工具
├── local_pipeline_dir #設定檔目錄，包含 pipeline.json 模板
│ └── default-pipeline/ #預設設定文件
├── prompts #適用於各種用例的系統提示範本庫
└── requirements.txt # Python 依賴套件 (僅供參考)
```

## 快速開始指南

### 先決條件

1. **模型運行環境**：選擇一種方案：
 - [Ollama](https://ollama.ai/) (建議): `docker run -d ollama/ollama`然後拉取模型如`ollama run llama3`
 - VLLM或其他供應商作為替代運行時支持

2. **Python 環境**: Python 版本需求:Python>=3.10（建議使用）

### 安裝步驟

```bash
# Clone and navigate to repository
git clone https://github.com/slweng0321/local_LLM_Proxy.git
cd local-pipeline

# Create virtual environment (recommended)
python -m venv env && source env/bin/activate pip install -r requirements.txt

# Start the proxy server
export PYTHONPATH="${PYTHONPATH}:$(pwd)" python api_chat.py --port 1234
```