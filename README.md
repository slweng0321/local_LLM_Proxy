# Local LLM Proxy & Pipeline

A local implementation of Anthropic's [local-pipeline](https://github.com/localai/pipeline), adapted for running locally without API costs or data privacy concerns. This project provides a lightweight proxy layer that sits between your applications and various open-source large language models (LLMs).

## Features

- 🔒 **Privacy First**: Keep all your data local - no sending prompts to external APIs
- 💰 **Cost Effective**: Zero monthly subscription fees for inference
- 🚀 **Fast Development**: Ready-to-use templates and scaffolding for common use cases
- ⚙️ **Flexible Configuration**: Support multiple model backends (Ollama, vLLM, etc.) through a unified interface
- 🔌 **Easy Integration**: REST API endpoints that mimic the Anthropic Messages SDK

## Project Structure

```
local-pipeline/
├── api_chat.py          # Main API endpoint handler for chat requests
├── client.py            # Client-side utilities and request builders
├── checks.py            # Model validation and capability detection
├── local_pipeline_dir   # Configuration directory with pipeline.json templates
│   └── default-pipeline/  # Default configuration files
├── prompts              # System prompt templates for various use cases
└── requirements.txt     # Python dependencies (for reference)
```

## Quick Start

### Prerequisites

1. **Model Runtime**: Choose one:
   - [Ollama](https://ollama.ai/) (recommended): `docker run -d ollama/ollama` then pull models like `ollama run llama3`
   - Alternative runtime with vLLM or other providers as supported
2. **Python Environment**: Python 3.10+ recommended

### Installation

```bash
# Clone and navigate to repository
git clone <repository-url>
cd local-pipeline

# Create virtual environment (recommended)
python -m venv env && source env/bin/activate pip install -r requirements.txt

# Start the proxy server
export PYTHONPATH="${PYTHONPATH}:$(pwd)" python api_chat.py --port 1234
```