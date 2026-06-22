import glob
import hashlib
import json
import os
import py_compile
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import requests
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from openai import OpenAI

try:
    import yaml
except Exception:
    yaml = None


OLLAMA_BASE = os.getenv("OLLAMA_BASE", "http://localhost:11434")
OPENAI_BASE = f"{OLLAMA_BASE}/v1"

WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", ".")).resolve()
STATE_DIR = Path(os.getenv("STATE_DIR", ".repo_aware_state"))
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_DB = STATE_DIR / "tasks.sqlite3"
PATCH_BACKUP_DIR = STATE_DIR / "backups"
PATCH_BACKUP_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_APPLY_MODE = os.getenv("DEFAULT_APPLY_MODE", "dry-run")
ENABLE_PRE_APPLY_CHECK = os.getenv("ENABLE_PRE_APPLY_CHECK", "1") == "1"
PRECHECK_TIMEOUT = int(os.getenv("PRECHECK_TIMEOUT", "20"))
PROJECT_RUNNER_TIMEOUT = int(os.getenv("PROJECT_RUNNER_TIMEOUT", "120"))
ENABLE_PROJECT_RUNNERS = os.getenv("ENABLE_PROJECT_RUNNERS", "1") == "1"
ENABLE_AFFECTED_GRAPH_EXPANSION = os.getenv("ENABLE_AFFECTED_GRAPH_EXPANSION", "1") == "1"

TASK_PLANNER_MODEL = os.getenv("TASK_PLANNER_MODEL", "qwen2.5:3b-instruct")
FILE_PLANNER_MODEL = os.getenv("FILE_PLANNER_MODEL", "qwen2.5:3b-instruct")
CODER_MODEL = os.getenv("CODER_MODEL", "qwen2.5-coder:7b")
CRITIC_MODEL = os.getenv("CRITIC_MODEL", "qwen2.5-coder:3b-instruct")
REVIEWER_MODEL = os.getenv("REVIEWER_MODEL", "qwen2.5-coder:7b")
PLAN_REVIEWER_MODEL = os.getenv("PLAN_REVIEWER_MODEL", REVIEWER_MODEL)

TASK_PLANNER_CTX = 4096
FILE_PLANNER_CTX = 6144
CODER_CTX = 8192
CODER_PREDICT = 4096
CRITIC_CTX = 8192
REVIEWER_CTX = 8192
REVIEWER_PREDICT = 4096
PLAN_REVIEWER_CTX = 6144
PLAN_REVIEWER_PREDICT = 2048

REQUEST_TIMEOUT = 600
AUTO_UNLOAD_AFTER_STAGE = True
DUPLICATE_WINDOW_SECONDS = 8
MAX_FILE_BYTES = 120_000
MAX_RETRIEVED_FILES = 8
MAX_RETRIEVED_CHARS_PER_FILE = 12_000

# ── Continue 模型別名 ──────────────────────────────────────────────────────
# Plan 模式：唯讀分析，不生成/寫入任何檔案
PLAN_MODEL_ALIASES = {
    "local-pipeline-plan",
    "plan",
}
# Agent 模式：完整 Coder → Critic → Reviewer 四階段
# 新增 "multi-agent" 以保持舊版 Continue 設定相容
AGENT_MODEL_ALIASES = {
    "local-pipeline-agent",
    "agent",
    "multi-agent",   # ← 相容舊版 Continue config（model: multi-agent）
}

EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode", "node_modules", "dist", "build",
    ".venv", "venv", "__pycache__", ".next", ".nuxt", ".repo_aware_state", ".turbo",
}
EXCLUDE_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".tar",
    ".gz", ".7z", ".mp4", ".mp3", ".wav", ".lock", ".pyc", ".pyo",
}
SOURCE_SUFFIXES = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".cpp", ".c",
    ".h", ".hpp", ".cs", ".php", ".rb", ".swift", ".kt", ".m", ".scala",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".md", ".sql", ".sh",
}

TASK_PLANNER_SYSTEM = """You are a senior software task planner for a repository-aware coding agent.
Return valid JSON only with these keys:
{
  "task_goal": str,
  "change_type": str,
  "constraints": [str],
  "success_criteria": [str],
  "repo_assumptions": [str],
  "search_hints": [str]
}"""

FILE_PLANNER_SYSTEM = """You are a repository file planner.
Given the user request, repository summary, retrieved files, and task plan, decide what to read and edit.
Return valid JSON only with these keys:
{
  "must_read": [str],
  "must_edit": [str],
  "may_edit": [str],
  "new_files": [str],
  "edit_strategy": [str]
}
Only include repository-relative file paths."""

PLAN_REVIEWER_SYSTEM = """You are a repository-aware planning reviewer.
Use the user request, task plan, file plan, repository context, and selected files to produce a read-only plan.
Do not generate code. Do not output patches. Do not output full file contents.
Return valid JSON only with this schema:
{
  "summary": [str],
  "intent": str,
  "diagnosis": [str],
  "recommended_steps": [str],
  "candidate_files": [str],
  "risks": [str],
  "suggested_apply_mode": str
}"""

CODER_SYSTEM = """You are a precise multi-file code generator.
Follow the task plan and file plan strictly.
Output valid JSON only with this schema:
{
  "files": [
    {
      "path": str,
      "action": "create" | "replace",
      "content": str
    }
  ],
  "notes": [str]
}
Rules:
- Only modify files listed in must_edit or new_files.
- Output full file contents, not diffs.
- Preserve existing architecture unless change is required.
- Do not add markdown fences.
- IMPORTANT: Never truncate the output mid-function. If the implementation is long, prioritize completeness over brevity."""

CRITIC_SYSTEM = """You are a strict repository-aware code critic.
Review the generated file outputs against the user request, task plan, file plan, and repository context.
Do NOT rewrite the full code. Only identify concrete issues, risks, and precise fixes.
Return valid JSON only with this schema:
{
  "acceptable": bool,
  "must_fix": [
    {
      "severity": "high" | "medium" | "low",
      "path": str,
      "issue": str,
      "reason": str,
      "fix_hint": str
    }
  ],
  "optional_improvements": [str],
  "reviewer_instruction": str
}"""

REVIEWER_SYSTEM = """You are a final repository-aware reviewer.
Use the original request, task plan, file plan, generated files, and critic report.
Return valid JSON only with this schema:
{
  "files": [
    {
      "path": str,
      "action": "create" | "replace",
      "content": str
    }
  ],
  "summary": [str]
}
Rules:
- Fix all must_fix items from the critic report.
- Preserve original architecture unless correctness requires change.
- Only touch allowed files.
- Output full file contents, not diffs.
- Ensure the final code is complete and not truncated."""

app = FastAPI()
client = OpenAI(base_url=OPENAI_BASE, api_key="ollama")
IN_FLIGHT: dict[str, float] = {}


@dataclass
class RetrievedFile:
    path: str
    reason: str
    preview: str


@dataclass
class TaskState:
    task_id: str
    created_at: float
    request_hash: str
    user_request: str
    repo_root: str
    pipeline_mode: str = "agent"
    requested_model: str = ""
    plan_result: dict[str, Any] = field(default_factory=dict)
    repo_manifest: dict[str, Any] = field(default_factory=dict)
    retrieved_files: list[dict[str, Any]] = field(default_factory=list)
    task_plan: dict[str, Any] = field(default_factory=dict)
    file_plan: dict[str, Any] = field(default_factory=dict)
    generated_files: list[dict[str, Any]] = field(default_factory=list)
    critic_report: dict[str, Any] = field(default_factory=dict)
    final_files: list[dict[str, Any]] = field(default_factory=list)
    apply_mode: str = "dry-run"
    apply_result: dict[str, Any] = field(default_factory=dict)
    status: str = "created"
    metrics: dict[str, Any] = field(default_factory=dict)


def _init_db() -> None:
    con = sqlite3.connect(STATE_DB)
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                request_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        con.commit()
    finally:
        con.close()


_init_db()


def _log(label: str, elapsed: float | None = None) -> float:
    if elapsed is not None:
        print(f"      ⏱  耗時 {elapsed:.2f}s")
    print(f"\n{label}")
    return time.perf_counter()


def _save_task_state(state: TaskState) -> None:
    con = sqlite3.connect(STATE_DB)
    try:
        con.execute(
            """
            INSERT INTO tasks(task_id, created_at, request_hash, status, payload_json)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                status=excluded.status,
                payload_json=excluded.payload_json
            """,
            (
                state.task_id,
                state.created_at,
                state.request_hash,
                state.status,
                json.dumps(asdict(state), ensure_ascii=False),
            ),
        )
        con.commit()
    finally:
        con.close()


def _load_task_payload(task_id: str) -> dict[str, Any] | None:
    con = sqlite3.connect(STATE_DB)
    try:
        row = con.execute("SELECT payload_json FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        con.close()


def _update_task_payload(task_id: str, payload: dict[str, Any]) -> None:
    con = sqlite3.connect(STATE_DB)
    try:
        con.execute(
            "UPDATE tasks SET status = ?, payload_json = ? WHERE task_id = ?",
            (payload.get("status", "unknown"), json.dumps(payload, ensure_ascii=False), task_id),
        )
        con.commit()
    finally:
        con.close()


def _native_keep_alive(model: str, keep_alive: int | str) -> dict[str, Any]:
    r = requests.post(
        f"{OLLAMA_BASE}/api/chat",
        json={"model": model, "messages": [], "keep_alive": keep_alive},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()


def _unload_model(model: str) -> None:
    try:
        result = _native_keep_alive(model, 0)
        reason = result.get("done_reason", "unknown")
        print(f"      🧹 已請求卸載 {model} (done_reason={reason})")
    except Exception as e:
        print(f"      ⚠️ 卸載 {model} 失敗: {e}")


def _chat_once(
    model: str,
    system: str,
    messages: list[dict[str, str]],
    *,
    temperature: float,
    options: dict[str, Any],
    think: bool | None = None,
) -> str:
    extra_body: dict[str, Any] = {"options": options}
    if think is not None:
        extra_body["think"] = think
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, *messages],
        temperature=temperature,
        extra_body=extra_body,
        timeout=REQUEST_TIMEOUT,
    )
    return resp.choices[0].message.content or ""


def _load_json(text: str, default: Any) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return default


def _resolve_pipeline_mode(body: dict[str, Any]) -> str:
    requested = str(body.get("model", "")).strip().lower()
    if requested in PLAN_MODEL_ALIASES:
        return "plan"
    if requested in AGENT_MODEL_ALIASES:
        return "agent"
    return "agent"


def _is_candidate_file(path: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in path.parts):
        return False
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return False
    if not path.is_file():
        return False
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return False
    except OSError:
        return False
    return path.suffix.lower() in SOURCE_SUFFIXES or path.name.lower() in {"readme", "dockerfile", "makefile"}


def _scan_repo(root: Path) -> dict[str, Any]:
    files = []
    for path in root.rglob("*"):
        if not _is_candidate_file(path):
            continue
        rel = path.relative_to(root).as_posix()
        suffix = path.suffix.lower()
        kind = "test" if ("test" in rel.lower() or rel.startswith("tests/")) else "source"
        if suffix in {".md", ".json", ".yaml", ".yml", ".toml", ".ini"}:
            kind = "config_or_docs"
        files.append({"path": rel, "size": path.stat().st_size, "suffix": suffix, "kind": kind})
    files.sort(key=lambda x: x["path"])
    return {"root": root.as_posix(), "file_count": len(files), "files": files[:2000]}


def _read_file_snippet(path: Path, max_chars: int = MAX_RETRIEVED_CHARS_PER_FILE) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    return text[:max_chars]


def _simple_retrieve(root: Path, user_request: str, manifest: dict[str, Any], top_k: int = MAX_RETRIEVED_FILES) -> list[RetrievedFile]:
    tokens = {tok.lower() for tok in user_request.replace("/", " ").replace("_", " ").split() if len(tok) >= 3}
    scored: list[tuple[int, str]] = []
    for item in manifest.get("files", []):
        path = item["path"]
        score = 0
        lower = path.lower()
        for tok in tokens:
            if tok in lower:
                score += 5
        if item["kind"] == "source":
            score += 2
        if item["kind"] == "test" and ("test" in user_request.lower() or "測試" in user_request):
            score += 3
        if path.endswith(("README.md", "readme.md")):
            score += 2
        if score > 0:
            scored.append((score, path))
    scored.sort(key=lambda x: (-x[0], x[1]))

    selected = []
    seen = set()
    for score, rel in scored:
        if rel in seen:
            continue
        seen.add(rel)
        snippet = _read_file_snippet(root / rel)
        selected.append(RetrievedFile(path=rel, reason=f"score={score}", preview=snippet))
        if len(selected) >= top_k:
            break

    if not selected:
        for item in manifest.get("files", [])[:min(top_k, 5)]:
            rel = item["path"]
            snippet = _read_file_snippet(root / rel)
            selected.append(RetrievedFile(path=rel, reason="fallback", preview=snippet))
    return selected


def _format_retrieved_files(files: list[RetrievedFile]) -> str:
    parts = []
    for item in files:
        parts.append(f"## FILE: {item.path}\nReason: {item.reason}\n```\n{item.preview}\n```")
    return "\n\n".join(parts)


def _read_selected_files(root: Path, paths: list[str]) -> list[dict[str, str]]:
    out = []
    seen = set()
    for rel in paths:
        rel = rel.strip()
        if not rel or rel in seen:
            continue
        seen.add(rel)
        path = (root / rel).resolve()
        if not str(path).startswith(str(root)):
            continue
        if not path.exists() or not path.is_file():
            continue
        out.append({"path": rel, "content": _read_file_snippet(path)})
    return out


def _safe_paths(file_plan: dict[str, Any]) -> set[str]:
    allowed = set()
    for key in ("must_edit", "new_files", "may_edit"):
        for item in file_plan.get(key, []) or []:
            if isinstance(item, str) and item.strip():
                allowed.add(item.strip())
    return allowed


def _filter_generated_files(files: list[dict[str, Any]], allowed: set[str]) -> list[dict[str, Any]]:
    out = []
    for item in files:
        path = str(item.get("path", "")).strip()
        action = str(item.get("action", "")).strip()
        content = item.get("content", "")
        if path not in allowed:
            continue
        if action not in {"create", "replace"}:
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        out.append({"path": path, "action": action, "content": content})
    return out


def _normalize_apply_mode(body: dict[str, Any]) -> str:
    extra = body.get("extra_body") or {}
    apply_mode = extra.get("apply_mode") or body.get("apply_mode") or DEFAULT_APPLY_MODE
    apply_mode = str(apply_mode).strip().lower()
    return apply_mode if apply_mode in {"dry-run", "apply"} else "dry-run"


def _validate_patch_targets(root: Path, files: list[dict[str, Any]]) -> list[str]:
    errors = []
    for item in files:
        rel = item["path"]
        abs_path = (root / rel).resolve()
        if not str(abs_path).startswith(str(root)):
            errors.append(f"path escapes workspace: {rel}")
    return errors


def _build_backup_path(task_id: str, rel_path: str) -> Path:
    safe_rel = rel_path.replace("/", "__")
    task_dir = PATCH_BACKUP_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir / safe_rel


def _build_created_marker_path(task_id: str, rel_path: str) -> Path:
    safe_rel = rel_path.replace("/", "__")
    task_dir = PATCH_BACKUP_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir / f"__created__{safe_rel}.json"


def _write_backup(task_id: str, rel_path: str, src_path: Path) -> str:
    backup_path = _build_backup_path(task_id, rel_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, backup_path)
    return backup_path.as_posix()


def _record_created_file(task_id: str, rel_path: str) -> str:
    marker = _build_created_marker_path(task_id, rel_path)
    marker.write_text(json.dumps({"path": rel_path}, ensure_ascii=False), encoding="utf-8")
    return marker.as_posix()


def _atomic_write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(target.parent)) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, target)


def _run_subprocess(cmd: list[str], cwd: Path | None = None, timeout: int | None = None) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout or PRECHECK_TIMEOUT,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, output.strip()
    except Exception as e:
        return False, str(e)


def _check_python(rel: str, content: str) -> tuple[bool, str]:
    try:
        compile(content, rel, "exec")
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".py", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            py_compile.compile(tmp_path, doraise=True)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return True, "ok"
    except Exception as e:
        return False, str(e)


def _check_json_content(content: str) -> tuple[bool, str]:
    try:
        json.loads(content)
        return True, "ok"
    except Exception as e:
        return False, str(e)


def _check_node_syntax(content: str, suffix: str) -> tuple[bool, str]:
    ext = suffix if suffix in {".js", ".mjs", ".cjs"} else ".js"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=ext, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        return _run_subprocess(["node", "--check", tmp_path])
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _check_typescript(content: str, suffix: str) -> tuple[bool, str]:
    ext = suffix if suffix in {".ts", ".tsx"} else ".ts"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=ext, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        return _run_subprocess(["tsc", "--noEmit", "--pretty", "false", tmp_path])
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _check_shell(content: str) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".sh", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        return _run_subprocess(["bash", "-n", tmp_path])
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _check_toml(content: str) -> tuple[bool, str]:
    try:
        import tomllib
        tomllib.loads(content)
        return True, "ok"
    except Exception as e:
        return False, str(e)


def _check_yaml(content: str) -> tuple[bool, str]:
    try:
        if yaml is None:
            return False, "PyYAML not installed"
        yaml.safe_load(content)
        return True, "ok"
    except Exception as e:
        return False, str(e)


def _check_ini(content: str) -> tuple[bool, str]:
    try:
        import configparser
        parser = configparser.ConfigParser()
        parser.read_string(content)
        return True, "ok"
    except Exception as e:
        return False, str(e)


def _iter_existing_dirs_from_glob(root: Path, pattern: str) -> list[str]:
    matches = []
    raw_pattern = pattern.strip().strip("/")
    if not raw_pattern:
        return matches
    abs_pattern = root / raw_pattern
    for hit in glob.glob(str(abs_pattern), recursive=True):
        p = Path(hit)
        if p.is_dir():
            try:
                matches.append(p.relative_to(root).as_posix())
            except Exception:
                pass
    return sorted(set(matches))


def _load_workspace_manifest(root: Path) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "manager": None,
        "packages": [],
        "workspace_packages": [],
        "dependency_graph": {},
        "reverse_dependency_graph": {},
        "sources": [],
    }

    pnpm_path = root / "pnpm-workspace.yaml"
    if pnpm_path.exists():
        manifest["manager"] = "pnpm"
        manifest["sources"].append("pnpm-workspace.yaml")
        try:
            if yaml is not None:
                data = yaml.safe_load(pnpm_path.read_text(encoding="utf-8")) or {}
                for pat in data.get("packages", []) if isinstance(data, dict) else []:
                    if isinstance(pat, str):
                        manifest["packages"].append({"pattern": pat, "source": "pnpm"})
        except Exception:
            pass

    package_json = root / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
            workspaces = data.get("workspaces")
            patterns: list[str] = []
            if isinstance(workspaces, list):
                patterns = [x for x in workspaces if isinstance(x, str)]
            elif isinstance(workspaces, dict):
                patterns = [x for x in workspaces.get("packages", []) if isinstance(x, str)]
            for pat in patterns:
                manifest["packages"].append({"pattern": pat, "source": "package.json#workspaces"})
            if patterns and not manifest["manager"]:
                manifest["manager"] = "npm-workspaces"
                manifest["sources"].append("package.json#workspaces")
        except Exception:
            pass

    if (root / "turbo.json").exists():
        manifest["sources"].append("turbo.json")
        if not manifest["manager"]:
            manifest["manager"] = "turbo"

    if (root / "nx.json").exists():
        manifest["sources"].append("nx.json")
        if not manifest["manager"]:
            manifest["manager"] = "nx"

    expanded_dirs: set[str] = set()
    for pkg in manifest["packages"]:
        expanded_dirs.update(_iter_existing_dirs_from_glob(root, pkg["pattern"]))

    package_entries = []
    name_to_root: dict[str, str] = {}
    for rel_dir in sorted(expanded_dirs):
        abs_dir = root / rel_dir
        entry = {
            "relative_root": rel_dir,
            "root": abs_dir.as_posix(),
            "type": "workspace-package",
            "markers": [],
            "name": rel_dir,
            "tooling": {},
            "internal_deps": [],
        }
        pj = abs_dir / "package.json"
        if pj.exists():
            entry["markers"].append("package.json")
            try:
                data = json.loads(pj.read_text(encoding="utf-8"))
                entry["name"] = data.get("name") or rel_dir
                scripts = data.get("scripts", {}) if isinstance(data, dict) else {}
                entry["tooling"]["scripts"] = sorted(scripts.keys()) if isinstance(scripts, dict) else []
            except Exception:
                pass
        if (abs_dir / "project.json").exists():
            entry["markers"].append("project.json")
        if (abs_dir / "tsconfig.json").exists():
            entry["markers"].append("tsconfig.json")
        name_to_root[entry["name"]] = rel_dir
        package_entries.append(entry)

    for entry in package_entries:
        abs_dir = Path(entry["root"])
        pj = abs_dir / "package.json"
        if not pj.exists():
            continue
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
        except Exception:
            continue
        deps = {}
        for key in ["dependencies", "devDependencies", "peerDependencies", "optionalDependencies"]:
            block = data.get(key, {})
            if isinstance(block, dict):
                deps.update(block)
        internal = []
        for dep_name in deps.keys():
            if dep_name in name_to_root:
                internal.append(name_to_root[dep_name])
        entry["internal_deps"] = sorted(set(internal))

    dep_graph = {entry["relative_root"]: list(entry.get("internal_deps", [])) for entry in package_entries}
    rev_graph: dict[str, list[str]] = {entry["relative_root"]: [] for entry in package_entries}
    for pkg_root, deps in dep_graph.items():
        for dep_root in deps:
            rev_graph.setdefault(dep_root, []).append(pkg_root)
    for key in list(rev_graph.keys()):
        rev_graph[key] = sorted(set(rev_graph[key]))

    manifest["workspace_packages"] = package_entries
    manifest["dependency_graph"] = dep_graph
    manifest["reverse_dependency_graph"] = rev_graph
    return manifest


def _find_manifest_package_for_path(rel_path: str, ws_manifest: dict[str, Any]) -> dict[str, Any] | None:
    normalized = rel_path.strip("/")
    best = None
    best_len = -1
    for pkg in ws_manifest.get("workspace_packages", []):
        rel_root = pkg.get("relative_root", "").strip("/")
        if not rel_root:
            continue
        if normalized == rel_root or normalized.startswith(rel_root + "/"):
            if len(rel_root) > best_len:
                best = pkg
                best_len = len(rel_root)
    return best


def _find_marker_root(path: Path, markers: list[str], stop_root: Path) -> Path | None:
    current = path if path.is_dir() else path.parent
    stop_root = stop_root.resolve()
    while True:
        for marker in markers:
            if (current / marker).exists():
                return current
        if current == stop_root or current.parent == current:
            return None
        current = current.parent


def _detect_segment_for_path(root: Path, rel_path: str, ws_manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    ws_manifest = ws_manifest or _load_workspace_manifest(root)
    manifest_pkg = _find_manifest_package_for_path(rel_path, ws_manifest)
    if manifest_pkg:
        seg_root = Path(manifest_pkg["root"])
        return {
            "type": "workspace-package",
            "workspace_manager": ws_manifest.get("manager"),
            "root": seg_root.as_posix(),
            "relative_root": manifest_pkg["relative_root"],
            "markers": manifest_pkg.get("markers", []),
            "package_name": manifest_pkg.get("name"),
            "tooling": manifest_pkg.get("tooling", {}),
            "internal_deps": manifest_pkg.get("internal_deps", []),
            "manifest_sources": ws_manifest.get("sources", []),
        }

    abs_path = (root / rel_path).resolve()
    marker_sets = [
        ("node", ["package.json"]),
        ("python", ["pyproject.toml", "requirements.txt", "pytest.ini"]),
        ("go", ["go.mod"]),
        ("rust", ["Cargo.toml"]),
    ]
    for seg_type, markers in marker_sets:
        seg_root = _find_marker_root(abs_path, markers, root)
        if seg_root:
            return {
                "type": seg_type,
                "workspace_manager": ws_manifest.get("manager"),
                "root": seg_root.as_posix(),
                "relative_root": seg_root.relative_to(root).as_posix() if seg_root != root else ".",
                "markers": [m for m in markers if (seg_root / m).exists()],
                "manifest_sources": ws_manifest.get("sources", []),
            }
    return {
        "type": "generic",
        "workspace_manager": ws_manifest.get("manager"),
        "root": root.as_posix(),
        "relative_root": ".",
        "markers": [],
        "manifest_sources": ws_manifest.get("sources", []),
    }


def _detect_repo_profile(root: Path) -> dict[str, Any]:
    ws_manifest = _load_workspace_manifest(root)
    profile = _detect_segment_for_path(root, ".", ws_manifest)
    profile["workspace_manifest"] = ws_manifest
    return profile


def _expand_affected_workspace_segments(initial_roots: list[str], ws_manifest: dict[str, Any]) -> list[str]:
    if not ENABLE_AFFECTED_GRAPH_EXPANSION:
        return sorted(set(initial_roots))
    rev_graph = ws_manifest.get("reverse_dependency_graph", {}) or {}
    visited = set(initial_roots)
    queue = deque(initial_roots)
    while queue:
        current = queue.popleft()
        for dependent in rev_graph.get(current, []) or []:
            if dependent not in visited:
                visited.add(dependent)
                queue.append(dependent)
    return sorted(visited)


def _build_runners_for_segment(seg: dict[str, Any]) -> list[dict[str, Any]]:
    seg_root = Path(seg["root"])
    seg_type = seg["type"]
    runners: list[dict[str, Any]] = []

    if seg_type in {"workspace-package", "node"}:
        package_json = {}
        try:
            package_json = json.loads((seg_root / "package.json").read_text(encoding="utf-8"))
        except Exception:
            package_json = {}
        scripts = package_json.get("scripts", {}) if isinstance(package_json, dict) else {}
        if "lint" in scripts:
            runners.append({"name": "npm lint", "cmd": ["npm", "run", "lint", "--", "--no-fix"]})
        if "typecheck" in scripts:
            runners.append({"name": "npm typecheck", "cmd": ["npm", "run", "typecheck"]})
        elif (seg_root / "tsconfig.json").exists():
            runners.append({"name": "tsc project", "cmd": ["tsc", "-p", "tsconfig.json", "--noEmit"]})
        if "test" in scripts:
            runners.append({"name": "npm test", "cmd": ["npm", "test", "--", "--runInBand"]})

    elif seg_type == "python":
        if (seg_root / "pyproject.toml").exists():
            try:
                text = (seg_root / "pyproject.toml").read_text(encoding="utf-8")
                if "[tool.ruff" in text:
                    runners.append({"name": "ruff check", "cmd": ["ruff", "check", "."]})
            except Exception:
                pass
        if any((seg_root / name).exists() for name in ["pytest.ini", "tests", "conftest.py"]):
            runners.append({"name": "pytest", "cmd": ["pytest", "-q"]})

    elif seg_type == "go":
        runners.append({"name": "go test", "cmd": ["go", "test", "./..."]})

    elif seg_type == "rust":
        runners.append({"name": "cargo check", "cmd": ["cargo", "check"]})
        if (seg_root / "tests").exists():
            runners.append({"name": "cargo test", "cmd": ["cargo", "test", "--quiet"]})

    return runners


def _group_changed_files_by_segment(root: Path, files: list[dict[str, Any]], ws_manifest: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    ws_manifest = ws_manifest or _load_workspace_manifest(root)
    groups: dict[str, dict[str, Any]] = {}
    directly_changed_workspace_roots: list[str] = []

    for item in files:
        rel = item["path"]
        seg = _detect_segment_for_path(root, rel, ws_manifest)
        key = f"{seg['type']}::{seg['root']}"
        if key not in groups:
            groups[key] = {
                **seg,
                "changed_files": [],
                "runner_candidates": _build_runners_for_segment(seg),
                "trigger_reason": "direct-change",
            }
        groups[key]["changed_files"].append(rel)
        if seg["type"] == "workspace-package":
            directly_changed_workspace_roots.append(seg["relative_root"])

    affected_roots = _expand_affected_workspace_segments(directly_changed_workspace_roots, ws_manifest)
    pkg_index = {pkg["relative_root"]: pkg for pkg in ws_manifest.get("workspace_packages", [])}
    for rel_root in affected_roots:
        pkg = pkg_index.get(rel_root)
        if not pkg:
            continue
        seg = {
            "type": "workspace-package",
            "workspace_manager": ws_manifest.get("manager"),
            "root": pkg["root"],
            "relative_root": pkg["relative_root"],
            "markers": pkg.get("markers", []),
            "package_name": pkg.get("name"),
            "tooling": pkg.get("tooling", {}),
            "internal_deps": pkg.get("internal_deps", []),
            "manifest_sources": ws_manifest.get("sources", []),
        }
        key = f"{seg['type']}::{seg['root']}"
        if key not in groups:
            groups[key] = {
                **seg,
                "changed_files": [],
                "runner_candidates": _build_runners_for_segment(seg),
                "trigger_reason": "affected-dependent",
                "affected_via": ws_manifest.get("dependency_graph", {}).get(rel_root, []),
            }
        else:
            groups[key]["affected_dependents"] = [
                dep for dep in ws_manifest.get("reverse_dependency_graph", {}).get(rel_root, [])
            ]

    return sorted(groups.values(), key=lambda x: (x.get("trigger_reason") != "direct-change", x["relative_root"]))


def _run_segmented_project_runners(root: Path, files: list[dict[str, Any]]) -> dict[str, Any]:
    ws_manifest = _load_workspace_manifest(root)
    result = {
        "enabled": ENABLE_PROJECT_RUNNERS,
        "affected_graph_expansion": ENABLE_AFFECTED_GRAPH_EXPANSION,
        "workspace_manifest": ws_manifest,
        "segments": [],
        "errors": [],
    }
    if not ENABLE_PROJECT_RUNNERS:
        return result

    segments = _group_changed_files_by_segment(root, files, ws_manifest)
    for seg in segments:
        seg_root = Path(seg["root"])
        seg_result = {
            "type": seg["type"],
            "workspace_manager": seg.get("workspace_manager"),
            "package_name": seg.get("package_name"),
            "root": seg["root"],
            "relative_root": seg["relative_root"],
            "markers": seg["markers"],
            "manifest_sources": seg.get("manifest_sources", []),
            "trigger_reason": seg.get("trigger_reason"),
            "changed_files": seg.get("changed_files", []),
            "internal_deps": seg.get("internal_deps", []),
            "executed": [],
            "errors": [],
        }
        for runner in seg.get("runner_candidates", []):
            ok, output = _run_subprocess(runner["cmd"], cwd=seg_root, timeout=PROJECT_RUNNER_TIMEOUT)
            record = {"name": runner["name"], "cmd": runner["cmd"], "ok": ok}
            if output:
                record["output"] = output[:4000]
            seg_result["executed"].append(record)
            if not ok:
                seg_result["errors"].append({"name": runner["name"], "output": output[:4000]})
                result["errors"].append({
                    "segment": seg["relative_root"],
                    "runner": runner["name"],
                    "output": output[:4000],
                })
                break
        result["segments"].append(seg_result)
    return result


def _pre_apply_syntax_check(root: Path, files: list[dict[str, Any]]) -> dict[str, Any]:
    result = {"ok": True, "checked": [], "errors": [], "skipped": [], "project_runner": {}}
    if not ENABLE_PRE_APPLY_CHECK:
        return result

    for item in files:
        rel = item["path"]
        content = item["content"]
        suffix = Path(rel).suffix.lower()
        ok = True
        detail = "skipped"
        runner = "none"

        if suffix == ".py":
            runner = "python"
            ok, detail = _check_python(rel, content)
        elif suffix == ".json":
            runner = "json"
            ok, detail = _check_json_content(content)
        elif suffix in {".yaml", ".yml"}:
            runner = "yaml"
            ok, detail = _check_yaml(content)
        elif suffix == ".toml":
            runner = "toml"
            ok, detail = _check_toml(content)
        elif suffix == ".ini":
            runner = "ini"
            ok, detail = _check_ini(content)
        elif suffix == ".sh":
            runner = "bash -n"
            ok, detail = _check_shell(content)
        elif suffix in {".js", ".mjs", ".cjs"}:
            runner = "node --check"
            ok, detail = _check_node_syntax(content, suffix)
        elif suffix in {".ts", ".tsx"}:
            runner = "tsc --noEmit"
            ok, detail = _check_typescript(content, suffix)
        else:
            result["skipped"].append({"path": rel, "reason": f"no file-level precheck runner for {suffix or 'no-suffix'}"})
            continue

        if ok:
            result["checked"].append({"path": rel, "runner": runner})
        else:
            result["ok"] = False
            result["errors"].append({"path": rel, "runner": runner, "error": detail})

    if result["ok"]:
        project_runner_result = _run_segmented_project_runners(root, files)
        result["project_runner"] = project_runner_result
        if project_runner_result.get("errors"):
            result["ok"] = False
            result["errors"].append({
                "path": "<repo-segment>",
                "runner": "affected-graph-aware-segmented-profile",
                "error": "one or more segmented project runners failed",
            })
    return result


def _apply_patches(root: Path, task_id: str, files: list[dict[str, Any]], *, apply_mode: str) -> dict[str, Any]:
    result = {
        "mode": apply_mode,
        "applied": [],
        "skipped": [],
        "backups": [],
        "created_markers": [],
        "errors": [],
        "precheck": _pre_apply_syntax_check(root, files),
    }

    if not result["precheck"].get("ok", True):
        result["errors"].append("pre-apply syntax check failed")
        return result

    target_errors = _validate_patch_targets(root, files)
    if target_errors:
        result["errors"].extend(target_errors)
        return result

    for item in files:
        rel = item["path"]
        action = item["action"]
        content = item["content"]
        abs_path = (root / rel).resolve()
        exists = abs_path.exists()

        if action == "replace" and not exists:
            result["errors"].append(f"replace target does not exist: {rel}")
            continue
        if action == "create" and exists:
            result["skipped"].append({"path": rel, "reason": "create target already exists; treated as skipped"})
            continue

        if apply_mode == "apply":
            if exists:
                backup_path = _write_backup(task_id, rel, abs_path)
                result["backups"].append({"path": rel, "backup": backup_path})
            else:
                marker = _record_created_file(task_id, rel)
                result["created_markers"].append({"path": rel, "marker": marker})
            _atomic_write_text(abs_path, content)
            result["applied"].append({"path": rel, "action": action, "bytes": len(content.encode("utf-8"))})
        else:
            result["applied"].append({
                "path": rel,
                "action": action,
                "bytes": len(content.encode("utf-8")),
                "dry_run": True,
                "would_backup": exists,
                "would_record_created_marker": not exists,
            })

    return result


def _rollback_task(task_id: str, repo_root: Path) -> dict[str, Any]:
    backup_dir = PATCH_BACKUP_DIR / task_id
    result = {"task_id": task_id, "restored": [], "deleted_created": [], "errors": []}
    if not backup_dir.exists():
        result["errors"].append("backup directory not found")
        return result

    for entry in sorted(backup_dir.iterdir()):
        if not entry.is_file():
            continue
        if entry.name.startswith("__created__") and entry.suffix == ".json":
            try:
                payload = json.loads(entry.read_text(encoding="utf-8"))
                rel = payload["path"]
                target = (repo_root / rel).resolve()
                if not str(target).startswith(str(repo_root)):
                    result["errors"].append(f"rollback delete path escapes workspace: {rel}")
                    continue
                if target.exists() and target.is_file():
                    target.unlink()
                    result["deleted_created"].append({"path": rel})
            except Exception as e:
                result["errors"].append(f"created marker parse failed: {entry.name}: {e}")
            continue

        rel = entry.name.replace("__", "/")
        target = (repo_root / rel).resolve()
        if not str(target).startswith(str(repo_root)):
            result["errors"].append(f"rollback path escapes workspace: {rel}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(target, entry.read_text(encoding="utf-8", errors="ignore"))
        result["restored"].append({"path": rel, "from": entry.as_posix()})
    return result


def _build_repo_summary(state: TaskState) -> str:
    return json.dumps(
        {
            "root": state.repo_manifest.get("root"),
            "file_count": state.repo_manifest.get("file_count"),
            "files": state.repo_manifest.get("files", [])[:80],
        },
        ensure_ascii=False,
    )


def _build_selected_files_text(selected_files: list[dict[str, str]]) -> str:
    return "\n\n".join(f"## FILE: {item['path']}\n```\n{item['content']}\n```" for item in selected_files)


def _run_plan_pipeline_and_stream(
    state: TaskState,
    latest_user_content: str,
    selected_files_text: str,
    repo_summary: str,
    request_hash: str,
    total_start: float,
):
    _log(f"📝 [PlanReviewer · {PLAN_REVIEWER_MODEL}] 產生唯讀計畫並串流...")
    stream_response = client.chat.completions.create(
        model=PLAN_REVIEWER_MODEL,
        messages=[
            {"role": "system", "content": PLAN_REVIEWER_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"## User Request\n{latest_user_content}\n\n"
                    f"## Repository Summary\n{repo_summary}\n\n"
                    f"## Task Plan\n{json.dumps(state.task_plan, ensure_ascii=False)}\n\n"
                    f"## File Plan\n{json.dumps(state.file_plan, ensure_ascii=False)}\n\n"
                    f"## Selected Files\n{selected_files_text}"
                ),
            },
        ],
        temperature=0.1,
        stream=True,
        extra_body={"options": {"num_ctx": PLAN_REVIEWER_CTX, "num_predict": PLAN_REVIEWER_PREDICT}},
        timeout=REQUEST_TIMEOUT,
    )

    stage_start = time.perf_counter()
    first_chunk = True
    final_buffer: list[str] = []

    def stream_generator():
        nonlocal first_chunk
        try:
            for chunk in stream_response:
                text = chunk.choices[0].delta.content or ""
                if text:
                    final_buffer.append(text)
                if first_chunk:
                    print(f"      ⚡ TTFT: {time.perf_counter() - stage_start:.2f}s")
                    first_chunk = False
                yield f"data: {chunk.model_dump_json()}\n\n"
        finally:
            full_text = "".join(final_buffer)
            state.plan_result = _load_json(
                full_text,
                {
                    "summary": [],
                    "intent": "",
                    "diagnosis": [],
                    "recommended_steps": [],
                    "candidate_files": [],
                    "risks": [],
                    "suggested_apply_mode": "dry-run",
                },
            )
            state.status = "planned"
            state.metrics = {
                "total_elapsed_sec": round(time.perf_counter() - total_start, 2),
                "plan_reviewer_elapsed_sec": round(time.perf_counter() - stage_start, 2),
            }
            _save_task_state(state)
            if AUTO_UNLOAD_AFTER_STAGE:
                _unload_model(PLAN_REVIEWER_MODEL)
            yield "data: [DONE]\n\n"
            IN_FLIGHT.pop(request_hash, None)

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


def _run_agent_pipeline_and_stream(
    state: TaskState,
    latest_user_content: str,
    selected_files_text: str,
    request_hash: str,
    total_start: float,
):
    allowed_paths = _safe_paths(state.file_plan)

    t = _log(f"💻 [Coder · {CODER_MODEL}] 生成多檔案修改...")
    coder_text = _chat_once(
        model=CODER_MODEL,
        system=CODER_SYSTEM,
        messages=[{"role": "user", "content": f"## User Request\n{latest_user_content}\n\n## Task Plan\n{json.dumps(state.task_plan, ensure_ascii=False)}\n\n## File Plan\n{json.dumps(state.file_plan, ensure_ascii=False)}\n\n## Selected Files\n{selected_files_text}"}],
        temperature=0.15,
        options={"num_ctx": CODER_CTX, "num_predict": CODER_PREDICT},
    )
    coder_json = _load_json(coder_text, {"files": [], "notes": []})
    state.generated_files = _filter_generated_files(coder_json.get("files", []), allowed_paths)
    state.status = "coded"
    _save_task_state(state)
    if AUTO_UNLOAD_AFTER_STAGE:
        _unload_model(CODER_MODEL)

    t = _log(f"🧪 [Critic · {CRITIC_MODEL}] 檢查 multi-file patches...", time.perf_counter() - t)
    critic_text = _chat_once(
        model=CRITIC_MODEL,
        system=CRITIC_SYSTEM,
        messages=[{"role": "user", "content": f"## User Request\n{latest_user_content}\n\n## Task Plan\n{json.dumps(state.task_plan, ensure_ascii=False)}\n\n## File Plan\n{json.dumps(state.file_plan, ensure_ascii=False)}\n\n## Original Selected Files\n{selected_files_text}\n\n## Generated Files\n{json.dumps(state.generated_files, ensure_ascii=False)}"}],
        temperature=0.1,
        options={"num_ctx": CRITIC_CTX},
    )
    state.critic_report = _load_json(critic_text, {"acceptable": False, "must_fix": [], "optional_improvements": [], "reviewer_instruction": ""})
    state.status = "critic_done"
    _save_task_state(state)
    if AUTO_UNLOAD_AFTER_STAGE:
        _unload_model(CRITIC_MODEL)

    t = _log(f"🔍 [Reviewer · {REVIEWER_MODEL}] 整理最終 multi-file 結果並串流...", time.perf_counter() - t)
    stream_response = client.chat.completions.create(
        model=REVIEWER_MODEL,
        messages=[
            {"role": "system", "content": REVIEWER_SYSTEM},
            {"role": "user", "content": f"## User Request\n{latest_user_content}\n\n## Task Plan\n{json.dumps(state.task_plan, ensure_ascii=False)}\n\n## File Plan\n{json.dumps(state.file_plan, ensure_ascii=False)}\n\n## Original Selected Files\n{selected_files_text}\n\n## Generated Files\n{json.dumps(state.generated_files, ensure_ascii=False)}\n\n## Critic Report\n{json.dumps(state.critic_report, ensure_ascii=False)}"},
        ],
        temperature=0.15,
        stream=True,
        extra_body={"options": {"num_ctx": REVIEWER_CTX, "num_predict": REVIEWER_PREDICT}},
        timeout=REQUEST_TIMEOUT,
    )

    stage_start = time.perf_counter()
    first_chunk = True
    final_buffer: list[str] = []

    def stream_generator():
        nonlocal first_chunk
        try:
            for chunk in stream_response:
                text = chunk.choices[0].delta.content or ""
                if text:
                    final_buffer.append(text)
                if first_chunk:
                    print(f"      ⚡ TTFT: {time.perf_counter() - stage_start:.2f}s")
                    first_chunk = False
                yield f"data: {chunk.model_dump_json()}\n\n"
        finally:
            full_text = "".join(final_buffer)
            reviewer_json = _load_json(full_text, {"files": [], "summary": []})
            state.final_files = _filter_generated_files(reviewer_json.get("files", []), allowed_paths)
            state.apply_result = _apply_patches(
                Path(state.repo_root), state.task_id, state.final_files, apply_mode=state.apply_mode
            )
            state.status = "applied" if state.apply_mode == "apply" else "dry_run_complete"
            state.metrics = {
                "total_elapsed_sec": round(time.perf_counter() - total_start, 2),
                "reviewer_stream_elapsed_sec": round(time.perf_counter() - stage_start, 2),
            }
            _save_task_state(state)
            if AUTO_UNLOAD_AFTER_STAGE:
                _unload_model(REVIEWER_MODEL)
            yield "data: [DONE]\n\n"
            IN_FLIGHT.pop(request_hash, None)

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


def _find_git_root(start: Path) -> Path | None:
    current = start.resolve()
    while True:
        if (current / ".git").exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


def _resolve_workspace_root(body: dict[str, Any]) -> Path:
    extra = body.get("extra_body") or {}
    override = extra.get("workspace_root") or body.get("workspace_root")
    if override:
        p = Path(override).resolve()
        if p.exists():
            return p

    messages = body.get("messages", [])
    for msg in reversed(messages):
        content = msg.get("content", "")
        if isinstance(content, str):
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("WORKSPACE:"):
                    candidate = Path(line[len("WORKSPACE:"):].strip()).resolve()
                    if candidate.exists():
                        return candidate

    git_root = _find_git_root(WORKSPACE_ROOT)
    if git_root:
        return git_root

    return WORKSPACE_ROOT


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    user_messages = body.get("messages", [])
    if not user_messages:
        return JSONResponse({"error": "messages is required"}, status_code=400)

    pipeline_mode = _resolve_pipeline_mode(body)
    requested_model = str(body.get("model", "")).strip()
    apply_mode = _normalize_apply_mode(body)
    effective_root = _resolve_workspace_root(body)

    latest_user_content = ""
    for msg in reversed(user_messages):
        if msg.get("role") == "user":
            latest_user_content = msg.get("content", "")
            break

    request_hash = hashlib.sha256(
        json.dumps({"content": latest_user_content, "root": effective_root.as_posix(), "mode": pipeline_mode}, ensure_ascii=False).encode()
    ).hexdigest()

    now = time.perf_counter()
    if request_hash in IN_FLIGHT and now - IN_FLIGHT[request_hash] < DUPLICATE_WINDOW_SECONDS:
        return JSONResponse({"error": "duplicate request in flight"}, status_code=429)
    IN_FLIGHT[request_hash] = now

    total_start = time.perf_counter()

    task_id = str(uuid.uuid4())
    state = TaskState(
        task_id=task_id,
        created_at=time.time(),
        request_hash=request_hash,
        user_request=latest_user_content,
        repo_root=effective_root.as_posix(),
        pipeline_mode=pipeline_mode,
        requested_model=requested_model,
        apply_mode=apply_mode,
    )
    _save_task_state(state)

    t = _log(f"🗂️  [Workspace] root={effective_root} | mode={pipeline_mode} | apply={apply_mode}")

    t = _log(f"📂 [RepoScan] 掃描 {effective_root}...", time.perf_counter() - t)
    state.repo_manifest = _scan_repo(effective_root)

    t = _log(f"🔍 [Retrieve] 抓取相關檔案...", time.perf_counter() - t)
    retrieved = _simple_retrieve(effective_root, latest_user_content, state.repo_manifest)
    state.retrieved_files = [asdict(r) for r in retrieved]
    retrieved_text = _format_retrieved_files(retrieved)

    repo_summary = _build_repo_summary(state)

    t = _log(f"🧭 [TaskPlanner · {TASK_PLANNER_MODEL}] 分析任務目標...", time.perf_counter() - t)
    task_plan_text = _chat_once(
        model=TASK_PLANNER_MODEL,
        system=TASK_PLANNER_SYSTEM,
        messages=[{"role": "user", "content": f"## User Request\n{latest_user_content}\n\n## Repository Summary\n{repo_summary}\n\n## Retrieved Files\n{retrieved_text}"}],
        temperature=0.2,
        options={"num_ctx": TASK_PLANNER_CTX},
    )
    state.task_plan = _load_json(task_plan_text, {"task_goal": latest_user_content, "change_type": "unknown", "constraints": [], "success_criteria": [], "repo_assumptions": [], "search_hints": []})
    _save_task_state(state)
    if AUTO_UNLOAD_AFTER_STAGE:
        _unload_model(TASK_PLANNER_MODEL)

    t = _log(f"📋 [FilePlanner · {FILE_PLANNER_MODEL}] 規劃檔案操作...", time.perf_counter() - t)
    file_plan_text = _chat_once(
        model=FILE_PLANNER_MODEL,
        system=FILE_PLANNER_SYSTEM,
        messages=[{"role": "user", "content": f"## User Request\n{latest_user_content}\n\n## Repository Summary\n{repo_summary}\n\n## Retrieved Files\n{retrieved_text}\n\n## Task Plan\n{json.dumps(state.task_plan, ensure_ascii=False)}"}],
        temperature=0.2,
        options={"num_ctx": FILE_PLANNER_CTX},
    )
    state.file_plan = _load_json(file_plan_text, {"must_read": [], "must_edit": [], "may_edit": [], "new_files": [], "edit_strategy": []})
    _save_task_state(state)
    if AUTO_UNLOAD_AFTER_STAGE:
        _unload_model(FILE_PLANNER_MODEL)

    read_paths = list(state.file_plan.get("must_read", []) or [])
    edit_paths = list(state.file_plan.get("must_edit", []) or []) + list(state.file_plan.get("may_edit", []) or [])
    all_paths = list(dict.fromkeys(read_paths + edit_paths))
    selected_files = _read_selected_files(effective_root, all_paths)
    selected_files_text = _build_selected_files_text(selected_files)

    if pipeline_mode == "plan":
        return _run_plan_pipeline_and_stream(
            state, latest_user_content, selected_files_text, repo_summary, request_hash, total_start
        )

    return _run_agent_pipeline_and_stream(
        state, latest_user_content, selected_files_text, request_hash, total_start
    )


# ── 管理端點 ─────────────────────────────────────────────────────────────────

@app.get("/v1/models")
async def list_models():
    """回傳 Continue 可識別的模型清單。"""
    return {
        "object": "list",
        "data": [
            {
                "id": "plan",
                "object": "model",
                "created": 0,
                "owned_by": "local",
                "display_name": "Local Pipeline · Plan (唯讀分析)",
                "description": "TaskPlanner → FilePlanner → PlanReviewer（不寫入任何檔案）",
            },
            {
                "id": "agent",
                "object": "model",
                "created": 0,
                "owned_by": "local",
                "display_name": "Local Pipeline · Agent (Coder → Critic → Reviewer)",
                "description": "完整四階段 pipeline：TaskPlanner → FilePlanner → Coder → Critic → Reviewer",
            },
            {
                "id": "multi-agent",
                "object": "model",
                "created": 0,
                "owned_by": "local",
                "display_name": "Local Pipeline · Agent (legacy alias)",
                "description": "等同於 agent 模式，保留此 ID 以相容舊版 Continue 設定",
            },
        ],
    }


@app.post("/admin/unload")
async def admin_unload(request: Request):
    body = await request.json()
    model = body.get("model")
    if not model:
        return JSONResponse({"error": "model is required"}, status_code=400)
    _unload_model(model)
    return {"ok": True, "model": model}


@app.post("/admin/apply")
async def admin_apply(request: Request):
    body = await request.json()
    task_id = body.get("task_id")
    if not task_id:
        return JSONResponse({"error": "task_id is required"}, status_code=400)
    payload = _load_task_payload(task_id)
    if not payload:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)
    final_files = payload.get("final_files", [])
    repo_root = Path(payload.get("repo_root", ".")).resolve()
    result = _apply_patches(repo_root, task_id, final_files, apply_mode="apply")
    payload["apply_mode"] = "apply"
    payload["apply_result"] = result
    payload["status"] = "applied"
    _update_task_payload(task_id, payload)
    return result


@app.post("/admin/rollback")
async def admin_rollback(request: Request):
    body = await request.json()
    task_id = body.get("task_id")
    if not task_id:
        return JSONResponse({"error": "task_id is required"}, status_code=400)
    payload = _load_task_payload(task_id)
    repo_root = Path(payload.get("repo_root", ".")).resolve() if payload else WORKSPACE_ROOT
    result = _rollback_task(task_id, repo_root)
    if payload:
        payload["status"] = "rolled_back"
        _update_task_payload(task_id, payload)
    return result


@app.get("/admin/task/{task_id}")
async def admin_task_status(task_id: str):
    payload = _load_task_payload(task_id)
    if not payload:
        return JSONResponse({"error": f"task {task_id} not found"}, status_code=404)
    return payload


@app.get("/health")
async def health():
    return {
        "ok": True,
        "pipeline": {
            "plan": {
                "task_planner": TASK_PLANNER_MODEL,
                "file_planner": FILE_PLANNER_MODEL,
                "plan_reviewer": PLAN_REVIEWER_MODEL,
            },
            "agent": {
                "task_planner": TASK_PLANNER_MODEL,
                "file_planner": FILE_PLANNER_MODEL,
                "coder": CODER_MODEL,
                "critic": CRITIC_MODEL,
                "reviewer": REVIEWER_MODEL,
            },
        },
        "model_aliases": {
            "plan": sorted(PLAN_MODEL_ALIASES),
            "agent": sorted(AGENT_MODEL_ALIASES),
        },
        "settings": {
            "workspace_root": str(WORKSPACE_ROOT),
            "default_apply_mode": DEFAULT_APPLY_MODE,
            "auto_unload_after_stage": AUTO_UNLOAD_AFTER_STAGE,
            "enable_pre_apply_check": ENABLE_PRE_APPLY_CHECK,
            "enable_project_runners": ENABLE_PROJECT_RUNNERS,
            "enable_affected_graph_expansion": ENABLE_AFFECTED_GRAPH_EXPANSION,
        },
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=18000)
