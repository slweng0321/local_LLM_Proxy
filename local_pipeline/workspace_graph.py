from __future__ import annotations

import glob
import json
import subprocess
from collections import deque
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:
    yaml = None

from .config import (
    ENABLE_AFFECTED_GRAPH_EXPANSION,
    ENABLE_PROJECT_RUNNERS,
    PRECHECK_TIMEOUT,
    PROJECT_RUNNER_TIMEOUT,
)

"""
Workspace / affected-graph helpers for segmented project runners.

Responsibilities:
- load workspace / monorepo manifest metadata
- map changed files to repository segments
- expand affected workspace packages via reverse dependency graph
- choose runner candidates per segment
- execute project-level validation commands per segment

Non-responsibilities:
- repository-wide file scanning
- retrieval / prompt formatting
- patch application
- API request handling
"""

SEGMENT_MARKER_SETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("node", ("package.json",)),
    ("python", ("pyproject.toml", "requirements.txt", "pytest.ini")),
    ("go", ("go.mod",)),
    ("rust", ("Cargo.toml",)),
)


def run_subprocess(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: int | None = None,
) -> tuple[bool, str]:
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
    except Exception as exc:
        return False, str(exc)


def _workspace_manifest_base() -> dict[str, Any]:
    return {
        "manager": None,
        "packages": [],
        "workspace_packages": [],
        "dependency_graph": {},
        "reverse_dependency_graph": {},
        "sources": [],
    }


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _normalize_rel_dir(root: Path, path: Path) -> str | None:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return None


def iter_existing_dirs_from_glob(root: Path, pattern: str) -> list[str]:
    raw_pattern = pattern.strip().strip("/")
    if not raw_pattern:
        return []

    matches: set[str] = set()
    abs_pattern = root / raw_pattern

    for hit in glob.glob(str(abs_pattern), recursive=True):
        candidate = Path(hit)
        if not candidate.is_dir():
            continue
        rel = _normalize_rel_dir(root, candidate)
        if rel:
            matches.add(rel)

    return sorted(matches)


def _append_workspace_patterns(
    manifest: dict[str, Any],
    patterns: list[str],
    *,
    source: str,
) -> None:
    for pattern in patterns:
        manifest["packages"].append({"pattern": pattern, "source": source})


def _load_pnpm_workspace_patterns(root: Path, manifest: dict[str, Any]) -> None:
    pnpm_path = root / "pnpm-workspace.yaml"
    if not pnpm_path.exists():
        return

    manifest["manager"] = "pnpm"
    manifest["sources"].append("pnpm-workspace.yaml")

    if yaml is None:
        return

    try:
        data = yaml.safe_load(pnpm_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return

    if not isinstance(data, dict):
        return

    patterns = [item for item in data.get("packages", []) if isinstance(item, str)]
    _append_workspace_patterns(manifest, patterns, source="pnpm")


def _load_package_json_workspaces(root: Path, manifest: dict[str, Any]) -> None:
    package_json_path = root / "package.json"
    if not package_json_path.exists():
        return

    data = _read_json_file(package_json_path)
    workspaces = data.get("workspaces")
    patterns: list[str] = []

    if isinstance(workspaces, list):
        patterns = [item for item in workspaces if isinstance(item, str)]
    elif isinstance(workspaces, dict):
        patterns = [
            item
            for item in workspaces.get("packages", [])
            if isinstance(item, str)
        ]

    if not patterns:
        return

    _append_workspace_patterns(
        manifest,
        patterns,
        source="package.json#workspaces",
    )
    manifest["sources"].append("package.json#workspaces")
    if not manifest["manager"]:
        manifest["manager"] = "npm-workspaces"


def _load_workspace_tool_markers(root: Path, manifest: dict[str, Any]) -> None:
    if (root / "turbo.json").exists():
        manifest["sources"].append("turbo.json")
        if not manifest["manager"]:
            manifest["manager"] = "turbo"

    if (root / "nx.json").exists():
        manifest["sources"].append("nx.json")
        if not manifest["manager"]:
            manifest["manager"] = "nx"


def _build_workspace_package_entry(root: Path, rel_dir: str) -> dict[str, Any]:
    abs_dir = (root / rel_dir).resolve()
    package_json = abs_dir / "package.json"
    package_data = _read_json_file(package_json)

    entry: dict[str, Any] = {
        "relative_root": rel_dir,
        "root": abs_dir.as_posix(),
        "type": "workspace-package",
        "markers": [],
        "name": package_data.get("name") or rel_dir,
        "tooling": {"scripts": []},
        "internal_deps": [],
    }

    if package_json.exists():
        entry["markers"].append("package.json")

    scripts = package_data.get("scripts", {})
    if isinstance(scripts, dict):
        entry["tooling"]["scripts"] = sorted(
            key for key in scripts.keys() if isinstance(key, str)
        )

    if (abs_dir / "project.json").exists():
        entry["markers"].append("project.json")
    if (abs_dir / "tsconfig.json").exists():
        entry["markers"].append("tsconfig.json")

    return entry


def _collect_internal_dependencies(
    package_entries: list[dict[str, Any]],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    name_to_root: dict[str, str] = {
        str(entry["name"]): str(entry["relative_root"])
        for entry in package_entries
        if entry.get("name") and entry.get("relative_root")
    }

    for entry in package_entries:
        package_data = _read_json_file(Path(entry["root"]) / "package.json")
        deps: dict[str, Any] = {}

        for key in (
            "dependencies",
            "devDependencies",
            "peerDependencies",
            "optionalDependencies",
        ):
            block = package_data.get(key, {})
            if isinstance(block, dict):
                deps.update(block)

        internal = [
            name_to_root[dep_name]
            for dep_name in deps.keys()
            if dep_name in name_to_root
        ]
        entry["internal_deps"] = sorted(set(internal))

    dependency_graph = {
        str(entry["relative_root"]): list(entry.get("internal_deps", []))
        for entry in package_entries
    }

    reverse_dependency_graph: dict[str, list[str]] = {
        str(entry["relative_root"]): [] for entry in package_entries
    }

    for pkg_root, deps in dependency_graph.items():
        for dep_root in deps:
            reverse_dependency_graph.setdefault(dep_root, []).append(pkg_root)

    for key in reverse_dependency_graph:
        reverse_dependency_graph[key] = sorted(set(reverse_dependency_graph[key]))

    return dependency_graph, reverse_dependency_graph


def load_workspace_manifest(root: Path) -> dict[str, Any]:
    manifest = _workspace_manifest_base()

    _load_pnpm_workspace_patterns(root, manifest)
    _load_package_json_workspaces(root, manifest)
    _load_workspace_tool_markers(root, manifest)

    expanded_dirs: set[str] = set()
    for pkg in manifest["packages"]:
        pattern = pkg.get("pattern")
        if isinstance(pattern, str):
            expanded_dirs.update(iter_existing_dirs_from_glob(root, pattern))

    package_entries = [
        _build_workspace_package_entry(root, rel_dir)
        for rel_dir in sorted(expanded_dirs)
    ]

    dependency_graph, reverse_dependency_graph = _collect_internal_dependencies(
        package_entries
    )

    manifest["workspace_packages"] = package_entries
    manifest["dependency_graph"] = dependency_graph
    manifest["reverse_dependency_graph"] = reverse_dependency_graph
    return manifest


def find_manifest_package_for_path(
    rel_path: str,
    ws_manifest: dict[str, Any],
) -> dict[str, Any] | None:
    normalized = rel_path.strip("/")
    best_match: dict[str, Any] | None = None
    best_len = -1

    for pkg in ws_manifest.get("workspace_packages", []):
        rel_root = str(pkg.get("relative_root", "")).strip("/")
        if not rel_root:
            continue

        if normalized == rel_root or normalized.startswith(rel_root + "/"):
            if len(rel_root) > best_len:
                best_match = pkg
                best_len = len(rel_root)

    return best_match


def find_marker_root(path: Path, markers: list[str], stop_root: Path) -> Path | None:
    current = path if path.is_dir() else path.parent
    current = current.resolve()
    stop_root = stop_root.resolve()

    while True:
        for marker in markers:
            if (current / marker).exists():
                return current
        if current == stop_root or current.parent == current:
            return None
        current = current.parent


def _build_workspace_segment(
    pkg: dict[str, Any],
    ws_manifest: dict[str, Any],
) -> dict[str, Any]:
    return {
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


def detect_segment_for_path(
    root: Path,
    rel_path: str,
    ws_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ws_manifest = ws_manifest or load_workspace_manifest(root)
    manifest_pkg = find_manifest_package_for_path(rel_path, ws_manifest)

    if manifest_pkg:
        return _build_workspace_segment(manifest_pkg, ws_manifest)

    abs_path = (root / rel_path).resolve()

    for seg_type, markers in SEGMENT_MARKER_SETS:
        seg_root = find_marker_root(abs_path, list(markers), root)
        if seg_root:
            return {
                "type": seg_type,
                "workspace_manager": ws_manifest.get("manager"),
                "root": seg_root.as_posix(),
                "relative_root": (
                    seg_root.relative_to(root).as_posix() if seg_root != root else "."
                ),
                "markers": [m for m in markers if (seg_root / m).exists()],
                "manifest_sources": ws_manifest.get("sources", []),
            }

    return {
        "type": "generic",
        "workspace_manager": ws_manifest.get("manager"),
        "root": root.resolve().as_posix(),
        "relative_root": ".",
        "markers": [],
        "manifest_sources": ws_manifest.get("sources", []),
    }


def detect_repo_profile(root: Path) -> dict[str, Any]:
    ws_manifest = load_workspace_manifest(root)
    profile = detect_segment_for_path(root, ".", ws_manifest)
    profile["workspace_manifest"] = ws_manifest
    return profile


def expand_affected_workspace_segments(
    initial_roots: list[str],
    ws_manifest: dict[str, Any],
) -> list[str]:
    unique_initial = sorted(
        {
            root
            for root in initial_roots
            if isinstance(root, str) and root and root != "."
        }
    )
    if not ENABLE_AFFECTED_GRAPH_EXPANSION:
        return unique_initial

    rev_graph = ws_manifest.get("reverse_dependency_graph", {}) or {}
    visited = set(unique_initial)
    queue = deque(unique_initial)

    while queue:
        current = queue.popleft()
        for dependent in rev_graph.get(current, []) or []:
            if dependent not in visited:
                visited.add(dependent)
                queue.append(dependent)

    return sorted(visited)


def build_runners_for_segment(seg: dict[str, Any]) -> list[dict[str, Any]]:
    seg_root = Path(seg["root"])
    seg_type = str(seg.get("type", "generic"))
    runners: list[dict[str, Any]] = []

    if seg_type in {"workspace-package", "node"}:
        package_json = _read_json_file(seg_root / "package.json")
        scripts = package_json.get("scripts", {}) if isinstance(package_json, dict) else {}

        if "lint" in scripts:
            runners.append(
                {
                    "name": "npm lint",
                    "cmd": ["npm", "run", "lint", "--", "--no-fix"],
                }
            )

        if "typecheck" in scripts:
            runners.append(
                {
                    "name": "npm typecheck",
                    "cmd": ["npm", "run", "typecheck"],
                }
            )
        elif (seg_root / "tsconfig.json").exists():
            runners.append(
                {
                    "name": "tsc project",
                    "cmd": ["tsc", "-p", "tsconfig.json", "--noEmit"],
                }
            )

        if "test" in scripts:
            runners.append(
                {
                    "name": "npm test",
                    "cmd": ["npm", "test", "--", "--runInBand"],
                }
            )

    elif seg_type == "python":
        pyproject = seg_root / "pyproject.toml"
        if pyproject.exists():
            try:
                text = pyproject.read_text(encoding="utf-8")
                if "[tool.ruff" in text:
                    runners.append(
                        {
                            "name": "ruff check",
                            "cmd": ["ruff", "check", "."],
                        }
                    )
            except Exception:
                pass

        if any((seg_root / name).exists() for name in ("pytest.ini", "tests", "conftest.py")):
            runners.append(
                {
                    "name": "pytest",
                    "cmd": ["pytest", "-q"],
                }
            )

    elif seg_type == "go":
        runners.append(
            {
                "name": "go test",
                "cmd": ["go", "test", "./..."],
            }
        )

    elif seg_type == "rust":
        runners.append(
            {
                "name": "cargo check",
                "cmd": ["cargo", "check"],
            }
        )
        if (seg_root / "tests").exists():
            runners.append(
                {
                    "name": "cargo test",
                    "cmd": ["cargo", "test", "--quiet"],
                }
            )

    return runners


def _normalize_changed_paths(files: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    for item in files:
        rel = str(item.get("path", "")).strip()
        if not rel or rel in seen:
            continue
        seen.add(rel)
        out.append(rel)

    return out


def group_changed_files_by_segment(
    root: Path,
    files: list[dict[str, Any]],
    ws_manifest: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    ws_manifest = ws_manifest or load_workspace_manifest(root)
    groups: dict[str, dict[str, Any]] = {}
    directly_changed_workspace_roots: list[str] = []

    for rel in _normalize_changed_paths(files):
        seg = detect_segment_for_path(root, rel, ws_manifest)
        key = f"{seg['type']}::{seg['root']}"

        if key not in groups:
            groups[key] = {
                **seg,
                "changed_files": [],
                "runner_candidates": build_runners_for_segment(seg),
                "trigger_reason": "direct-change",
            }

        groups[key]["changed_files"].append(rel)

        if seg["type"] == "workspace-package":
            directly_changed_workspace_roots.append(seg["relative_root"])

    affected_roots = expand_affected_workspace_segments(
        directly_changed_workspace_roots,
        ws_manifest,
    )

    pkg_index = {
        pkg["relative_root"]: pkg
        for pkg in ws_manifest.get("workspace_packages", [])
    }

    reverse_graph = ws_manifest.get("reverse_dependency_graph", {}) or {}

    for rel_root in affected_roots:
        pkg = pkg_index.get(rel_root)
        if not pkg:
            continue

        seg = _build_workspace_segment(pkg, ws_manifest)
        key = f"{seg['type']}::{seg['root']}"

        if key not in groups:
            groups[key] = {
                **seg,
                "changed_files": [],
                "runner_candidates": build_runners_for_segment(seg),
                "trigger_reason": "affected-dependent",
                "affected_dependents": reverse_graph.get(rel_root, []),
            }
        else:
            groups[key]["affected_dependents"] = reverse_graph.get(rel_root, [])

    return sorted(
        groups.values(),
        key=lambda item: (
            item.get("trigger_reason") != "direct-change",
            item.get("relative_root", "."),
        ),
    )


def run_segmented_project_runners(
    root: Path,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    ws_manifest = load_workspace_manifest(root)
    result: dict[str, Any] = {
        "enabled": ENABLE_PROJECT_RUNNERS,
        "affected_graph_expansion": ENABLE_AFFECTED_GRAPH_EXPANSION,
        "workspace_manifest": ws_manifest,
        "segments": [],
        "errors": [],
    }

    if not ENABLE_PROJECT_RUNNERS:
        return result

    segments = group_changed_files_by_segment(root, files, ws_manifest)

    for seg in segments:
        seg_root = Path(seg["root"])
        seg_result: dict[str, Any] = {
            "type": seg["type"],
            "workspace_manager": seg.get("workspace_manager"),
            "package_name": seg.get("package_name"),
            "root": seg["root"],
            "relative_root": seg["relative_root"],
            "markers": seg.get("markers", []),
            "manifest_sources": seg.get("manifest_sources", []),
            "trigger_reason": seg.get("trigger_reason"),
            "changed_files": seg.get("changed_files", []),
            "internal_deps": seg.get("internal_deps", []),
            "executed": [],
            "errors": [],
        }

        if "affected_dependents" in seg:
            seg_result["affected_dependents"] = seg["affected_dependents"]

        for runner in seg.get("runner_candidates", []):
            ok, output = run_subprocess(
                runner["cmd"],
                cwd=seg_root,
                timeout=PROJECT_RUNNER_TIMEOUT,
            )

            record = {
                "name": runner["name"],
                "cmd": runner["cmd"],
                "ok": ok,
            }
            if output:
                record["output"] = output[:4000]

            seg_result["executed"].append(record)

            if not ok:
                seg_result["errors"].append(
                    {
                        "name": runner["name"],
                        "output": output[:4000],
                    }
                )
                result["errors"].append(
                    {
                        "segment": seg["relative_root"],
                        "runner": runner["name"],
                        "output": output[:4000],
                    }
                )
                break

        result["segments"].append(seg_result)

    return result


__all__ = [
    "run_subprocess",
    "iter_existing_dirs_from_glob",
    "load_workspace_manifest",
    "find_manifest_package_for_path",
    "find_marker_root",
    "detect_segment_for_path",
    "detect_repo_profile",
    "expand_affected_workspace_segments",
    "build_runners_for_segment",
    "group_changed_files_by_segment",
    "run_segmented_project_runners",
]