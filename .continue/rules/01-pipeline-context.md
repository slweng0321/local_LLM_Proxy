---
name: Pipeline Project Context
alwaysApply: true
---
# 專案背景
這是一個 Python Pipeline 專案，架構為 Ollama + LiteLLM Router + Continue，
實作多模型非同步推理與流式傳輸。

## 模組結構
- `router/` — LiteLLM 路由邏輯，負責分派請求到不同 Ollama 模型
- `pipeline/` — 非同步 Pipeline 主邏輯，使用 asyncio
- `streaming/` — SSE / 流式傳輸處理層
- `config/` — 模型設定與環境變數

## 技術約束
- Python 3.11+，使用 asyncio / aiohttp
- 所有模組需維持 interface 一致性
- 不使用 sync blocking call，全面 async/await