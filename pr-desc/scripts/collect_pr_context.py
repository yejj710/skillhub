#!/usr/bin/env python3
"""Collect PR-template and git evidence for drafting a PR description."""

from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
import sys
from pathlib import Path


TEMPLATE_PATTERNS = [
    ".gitcode/PULL_REQUEST_TEMPLATE.md",
    ".gitcode/pull_request_template.md",
    ".gitcode/PULL_REQUEST_TEMPLATE/*.md",
    ".gitcode/pull_request_template/*.md",
    ".gitcode/merge_request_templates/*.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/pull_request_template.md",
    ".github/PULL_REQUEST_TEMPLATE/*.md",
    ".github/pull_request_template/*.md",
    ".gitee/PULL_REQUEST_TEMPLATE.md",
    ".gitee/pull_request_template.md",
    ".gitee/PULL_REQUEST_TEMPLATE/*.md",
    ".gitee/pull_request_template/*.md",
    ".gitlab/merge_request_templates/*.md",
    "PULL_REQUEST_TEMPLATE.md",
    "pull_request_template.md",
]

DOC_PATTERNS = [
    "*.md",
    "*.mdx",
    "*.rst",
    "*.txt",
    "*.adoc",
    "docs/*",
    "doc/*",
    "documentation/*",
    "README*",
    "CHANGELOG*",
]


def run_git(repo: Path, args: list[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def print_block(title: str, content: str) -> None:
    print(f"\n## {title}\n")
    if content.strip():
        print(content.rstrip())
    else:
        print("(empty)")


def truncate_lines(text: str, max_lines: int) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text.rstrip()
    hidden = len(lines) - max_lines
    return "\n".join(lines[:max_lines]).rstrip() + f"\n... truncated {hidden} lines ..."


def has_parent(repo: Path, commit: str) -> bool:
    result = run_git(repo, ["rev-list", "--parents", "-n", "1", commit])
    if result.returncode != 0:
        return False
    return len(result.stdout.split()) > 1


def empty_tree(repo: Path) -> str:
    result = run_git(repo, ["hash-object", "-t", "tree", "/dev/null"], check=True)
    return result.stdout.strip()


def list_templates(repo: Path) -> list[Path]:
    matches: list[Path] = []
    for pattern in TEMPLATE_PATTERNS:
        if "*" in pattern:
            for path in sorted(repo.glob(pattern)):
                if path.is_file() and path not in matches:
                    matches.append(path)
        else:
            path = repo / pattern
            if path.is_file() and path not in matches:
                matches.append(path)
    return matches


def is_docs_only(paths: list[str]) -> bool:
    if not paths:
        return False
    normalized = [path.replace(os.sep, "/") for path in paths]
    for path in normalized:
        if not any(fnmatch.fnmatch(path, pattern) for pattern in DOC_PATTERNS):
            return False
    return True


def changed_paths_from_name_status(name_status: str) -> list[str]:
    paths: list[str] = []
    for line in name_status.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            paths.append(parts[-1])
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_path", help="Path to the git repository")
    parser.add_argument("start_commit_id", help="First commit included in the PR range")
    parser.add_argument(
        "--max-template-chars",
        type=int,
        default=20000,
        help="Maximum characters to print for each template",
    )
    parser.add_argument(
        "--max-diff-lines",
        type=int,
        default=500,
        help="Maximum lines to print from the patch excerpt",
    )
    args = parser.parse_args()

    repo = Path(args.project_path).expanduser().resolve()
    if not repo.exists():
        print(f"[ERROR] Project path does not exist: {repo}", file=sys.stderr)
        return 2
    if not (repo / ".git").exists():
        result = run_git(repo, ["rev-parse", "--show-toplevel"])
        if result.returncode != 0:
            print(f"[ERROR] Not a git repository: {repo}", file=sys.stderr)
            return 2
        repo = Path(result.stdout.strip()).resolve()

    commit = args.start_commit_id
    verify = run_git(repo, ["rev-parse", "--verify", f"{commit}^{{commit}}"])
    if verify.returncode != 0:
        print(f"[ERROR] Commit not found: {commit}", file=sys.stderr)
        print(verify.stderr.strip(), file=sys.stderr)
        return 2
    commit_full = verify.stdout.strip()

    head = run_git(repo, ["rev-parse", "HEAD"], check=True).stdout.strip()
    parent_base = f"{commit_full}^" if has_parent(repo, commit_full) else None
    include_log_range = f"{commit_full}^..HEAD" if parent_base else "HEAD"

    print("# PR Description Context Pack")
    print(f"\nRepository: `{repo}`")
    print(f"Start commit: `{commit_full}`")
    print(f"HEAD: `{head}`")
    if parent_base:
        print(f"Assumed diff base: parent of start commit `{parent_base}`")
        print(f"Included commit range: `{include_log_range}`")
    else:
        print("Assumed diff base: root commit handling, no parent for start commit")
        print("Included commit range: repository history through HEAD")

    templates = list_templates(repo)
    if templates:
        print_block("PR Templates Found", "\n".join(f"- `{path.relative_to(repo)}`" for path in templates))
        for index, path in enumerate(templates, 1):
            content = path.read_text(encoding="utf-8", errors="replace")
            if len(content) > args.max_template_chars:
                content = content[: args.max_template_chars] + "\n... template truncated ..."
            print_block(f"Template {index}: {path.relative_to(repo)}", content)
    else:
        print_block("PR Templates Found", "No PR template found in common GitHub, GitCode, Gitee, or GitLab locations.")

    log = run_git(repo, ["log", "--reverse", "--format=%h %s", include_log_range])
    print_block("Commits", log.stdout if log.returncode == 0 else log.stderr)

    show = run_git(repo, ["show", "--no-patch", "--format=fuller", commit_full])
    print_block("Start Commit Metadata", show.stdout if show.returncode == 0 else show.stderr)

    if parent_base:
        diff_args = [parent_base, "HEAD"]
    else:
        diff_args = [empty_tree(repo), "HEAD"]

    stat = run_git(repo, ["diff", "--stat", *diff_args])
    print_block("Diff Stat", stat.stdout if stat.returncode == 0 else stat.stderr)

    name_status = run_git(repo, ["diff", "--name-status", *diff_args])
    print_block("Changed Files", name_status.stdout if name_status.returncode == 0 else name_status.stderr)

    paths = changed_paths_from_name_status(name_status.stdout)
    hints = [
        f"changed_file_count: {len(paths)}",
        f"docs_only: {'yes' if is_docs_only(paths) else 'no'}",
    ]
    tests = [path for path in paths if "test" in path.lower()]
    hints.append(f"test_files_changed: {'yes' if tests else 'no'}")
    if tests:
        hints.append("test_file_paths: " + ", ".join(tests))
    print_block("Impact Hints", "\n".join(hints))

    check = run_git(repo, ["diff", "--check", *diff_args])
    check_content = check.stdout + check.stderr
    if check.returncode == 0 and not check_content.strip():
        check_content = "PASS: git diff --check found no whitespace errors."
    else:
        check_content = f"FAIL: git diff --check returned {check.returncode}\n{check_content}"
    print_block("Validation Evidence", check_content)

    patch = run_git(repo, ["diff", "--no-ext-diff", "--unified=80", *diff_args])
    patch_content = patch.stdout if patch.returncode == 0 else patch.stderr
    print_block("Patch Excerpt", truncate_lines(patch_content, args.max_diff_lines))

    print("\n# Drafting Reminder")
    print("Fill the PR template from the evidence above. Preserve template language, headings, and checklist format.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
