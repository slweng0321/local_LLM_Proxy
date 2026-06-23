# Local LLM Proxy & Pipeline

A local, repository-aware LLM proxy and workflow engine inspired by Anthropic's local-pipeline.  
This project runs fully on your machine, helping you route chat requests to local models, inspect repository context, and coordinate plan/agent pipelines without sending code or prompts to external APIs.

## Features

- **Local-first**: keep prompts, code, and responses on your machine
- **Repository-aware**: scan the workspace and retrieve relevant files automatically
- **Flexible model routing**: configure different local models for chat, task planning, file planning, coding, review, and critique
- **Plan or agent workflows**: choose between planning-oriented and agent-oriented execution modes
- **FastAPI-based service**: exposes HTTP endpoints for chat/completion workflows and admin operations
- **Persistent task state**: stores run state and backups in a local state directory

## Project Structure

```text
local-pipeline/
├── main.py                # Application entry point
├── llm_proxy.py           # Proxy / runtime entry script
├── local_pipeline/
│   ├── api_chat.py        # Chat/completion API routes
│   ├── api_admin.py       # Admin API routes
│   ├── app_state.py       # Shared application state
│   ├── checks.py          # Model and workspace checks
│   ├── client.py          # Model client helpers
│   ├── config.py          # Environment-driven configuration
│   ├── db.py              # Local task-state database helpers
│   ├── pipeline_agent.py  # Agent workflow execution
│   ├── pipeline_plan.py   # Plan workflow execution
│   ├── prompts.py         # System prompts
│   ├── repository.py      # Repository scanning utilities
│   ├── retrieval.py       # Context retrieval helpers
│   ├── schemas.py         # Data models / schemas
│   └── ...
├── .repo_aware_state/     # Local state, SQLite DB, and backups
└── README_ZH.md
```

## Requirements

- Python 3.10+
- A local model runtime, such as:
  - [Ollama](https://ollama.com/)
  - a compatible OpenAI-style local endpoint
- Recommended: a virtual environment for Python dependencies

## Configuration

Most behavior is controlled through environment variables in `local_pipeline/config.py`.

Common options include:

- `OLLAMA_BASE` - base URL of the local model server
- `WORKSPACE_ROOT` - repository/workspace root to scan
- `STATE_DIR` - directory used for local state and backups
- `CHAT_MODEL` - model used for direct chat responses
- `TASK_PLANNER_MODEL` - model used for task planning
- `FILE_PLANNER_MODEL` - model used for file selection
- `CODER_MODEL` - model used for code generation
- `REVIEWER_MODEL` - model used for review
- `DEFAULT_APPLY_MODE` - `dry-run` or `apply`

See `local_pipeline/config.py` for the full list of available settings and defaults.

## Quick Start

### 1) Set up your model runtime

Make sure your local model server is running and reachable.

Example with Ollama:

```bash
ollama serve
ollama pull qwen3.5:9b
```

### 2) Install dependencies

```bash
python -m venv env
# Windows
env\Scripts\activate
pip install -r requirements.txt
```

### 3) Start the server

```bash
python main.py
```

By default, the service listens on `0.0.0.0:18000`.

## API Overview

### Chat completions

`POST /v1/chat/completions`

This endpoint accepts a chat-style request, builds repository context, selects relevant files, and then runs either:

- a direct chat response
- a plan pipeline
- an agent pipeline

depending on the detected request and requested mode.

### Admin routes

The app also includes admin endpoints via `local_pipeline/api_admin.py` for operational tasks.

## How It Works

1. Parse the incoming request and request context
2. Scan the workspace repository
3. Retrieve relevant files
4. Generate a task plan
5. Generate a file plan
6. Run the plan or agent pipeline
7. Persist task state locally for later inspection

If the request is a normal conversation, the system can bypass the file-planning workflow and answer directly.

## Local State

Runtime state is stored in `.repo_aware_state/`, including:

- `tasks.sqlite3` for task metadata
- `backups/` for patch backups and related files

These files are generated locally and are not meant to be committed.

## Development Notes

- The project is organized as a FastAPI application
- Configuration is environment-driven
- Model calls are routed through the local client layer
- Repository scanning excludes common build artifacts and cache directories

## Change Log

### 2026-06-23

- Refactored the async request path to reduce event-loop blocking in chat, plan, and agent flows.
- Moved synchronous SQLite and file-state writes off the main request path with `asyncio.to_thread(...)`.
- Unified LLM client/session lifecycle and added clean shutdown handling.
- Improved SSE cancellation behavior so client disconnects no longer emit a misleading `[DONE]` chunk.
- Kept the public API and visible behavior unchanged while improving stability and responsiveness.

## License

No license file was included in the repository snapshot. Add one if you plan to share or publish this project.