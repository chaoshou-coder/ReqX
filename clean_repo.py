from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys


def _rm(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            path.unlink()
        except Exception:
            return


def _collect_targets(repo_root: Path) -> list[Path]:
    patterns = [
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".coverage",
        "build",
        "dist",
        "*.egg-info",
        "*.pyc",
        "*.pyo",
    ]
    targets: list[Path] = []
    for pat in patterns:
        for p in repo_root.rglob(pat):
            parts = {x.lower() for x in p.parts}
            if any(x in parts for x in {".venv", "venv", ".tox"}):
                continue
            targets.append(p)
    return sorted(set(targets))


def clean(repo_root: Path) -> dict:
    removed: list[str] = []
    for p in _collect_targets(repo_root):
        _rm(p)
        removed.append(str(p))
    return {"removed_count": len(removed), "removed": removed}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="清理项目缓存与构建产物")
    p.add_argument("--root", default=None, help="要清理的根目录（默认脚本所在目录）")
    p.add_argument("--dry-run", action="store_true", help="只列出将删除的路径，不执行删除")
    p.add_argument("--json", action="store_true", help="输出 JSON（便于 CI 解析）")
    args = p.parse_args(argv)

    repo_root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent
    targets = _collect_targets(repo_root)
    if args.dry_run:
        payload = {"root": str(repo_root), "dry_run": True, "removed_count": len(targets), "removed": [str(x) for x in targets]}
    else:
        result = clean(repo_root)
        payload = {"root": str(repo_root), "dry_run": False, **result}

    if args.json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(f"清理完成：删除 {payload['removed_count']} 项\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
