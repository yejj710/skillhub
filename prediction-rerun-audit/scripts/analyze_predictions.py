#!/usr/bin/env python3
"""Analyze benchmark prediction JSONL files and optionally delete rerun candidates."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from statistics import mean, median


ANSWER_RE = re.compile(r"(?im)(?:^|\b)Answer\s*:\s*([ABCD])\b")


def find_prediction_files(root: Path) -> list[Path]:
    if root.name == "predictions":
        search_root = root
    else:
        candidate = root / "predictions"
        search_root = candidate if candidate.exists() else root
    return sorted(p for p in search_root.rglob("*.jsonl") if p.is_file())


def classify(obj: dict) -> tuple[list[str], dict]:
    pred = obj.get("prediction")
    info = {
        "has_prediction": isinstance(pred, str),
        "length": len(pred) if isinstance(pred, str) else None,
        "answer_after": None,
        "answer_any": None,
    }
    if not isinstance(pred, str):
        return ["no_prediction"], info

    info["answer_any"] = (ANSWER_RE.findall(pred) or [None])[-1]
    if "</think>" not in pred:
        return ["missing_think_close"], info

    after = pred.split("</think>", 1)[1]
    info["answer_after"] = (ANSWER_RE.findall(after) or [None])[-1]
    if info["answer_after"] is None:
        return ["no_answer_after_think"], info
    return [], info


def load_records(files: list[Path]) -> tuple[list[dict], list[dict]]:
    records: list[dict] = []
    errors: list[dict] = []
    for path in files:
        with path.open(encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except Exception as exc:  # noqa: BLE001
                    errors.append({"file": str(path), "line": line_no, "error": str(exc)})
                    continue
                reasons, info = classify(obj)
                records.append(
                    {
                        "file": path,
                        "line": line_no,
                        "id": obj.get("id"),
                        "reasons": reasons,
                        **info,
                    }
                )
    return records, errors


def delete_candidates(records: list[dict]) -> dict[str, dict]:
    by_file: dict[Path, set[int]] = {}
    for rec in records:
        if rec["reasons"]:
            by_file.setdefault(rec["file"], set()).add(rec["line"])

    summary: dict[str, dict] = {}
    for path, lines in sorted(by_file.items()):
        tmp = path.with_suffix(path.suffix + ".tmp")
        total = kept = removed = 0
        removed_ids = []
        with path.open(encoding="utf-8") as src, tmp.open("w", encoding="utf-8") as dst:
            for line_no, line in enumerate(src, 1):
                if not line.strip():
                    dst.write(line)
                    continue
                total += 1
                if line_no in lines:
                    removed += 1
                    try:
                        removed_ids.append(json.loads(line).get("id"))
                    except Exception:  # noqa: BLE001
                        removed_ids.append(None)
                    continue
                kept += 1
                dst.write(line)
        tmp.replace(path)
        summary[str(path)] = {
            "total": total,
            "removed": removed,
            "kept": kept,
            "removed_ids": sorted(x for x in removed_ids if x is not None),
        }
    return summary


def compact_ids(values: list[object]) -> str:
    ids = sorted(x for x in values if isinstance(x, int))
    return ",".join(str(x) for x in ids)


def print_report(root: Path, files: list[Path], records: list[dict], errors: list[dict], deleted: dict | None) -> None:
    candidates = [r for r in records if r["reasons"]]
    reason_counts = Counter("+".join(r["reasons"]) for r in candidates)
    lengths = [r["length"] for r in records if isinstance(r["length"], int)]
    candidate_lengths = [r["length"] for r in candidates if isinstance(r["length"], int)]

    print(f"root: {root}")
    print(f"files: {len(files)}")
    for path in files:
        print(f"  - {path}")
    print(f"records: {len(records)}")
    print(f"parse_errors: {len(errors)}")
    print(f"rerun_candidates: {len(candidates)}")
    print(f"reason_counts: {dict(reason_counts)}")
    print(f"candidate_ids_sorted: {compact_ids([r['id'] for r in candidates])}")
    if lengths:
        print(f"lengths: min={min(lengths)} median={median(lengths)} max={max(lengths)} avg={round(mean(lengths), 1)}")
    if candidate_lengths:
        print(
            "candidate_lengths: "
            f"min={min(candidate_lengths)} median={median(candidate_lengths)} "
            f"max={max(candidate_lengths)} avg={round(mean(candidate_lengths), 1)}"
        )

    if errors:
        print("\nPARSE_ERRORS")
        for err in errors:
            print(f"{err['file']} line={err['line']} error={err['error']}")

    print("\nDETAIL")
    for rec in sorted(candidates, key=lambda r: (str(r["file"]), r["line"])):
        print(
            f"{rec['file']} line={rec['line']} id={rec['id']} "
            f"reason={'+'.join(rec['reasons'])} len={rec['length']} "
            f"answer_any={rec['answer_any']} answer_after={rec['answer_after']}"
        )

    if deleted is not None:
        print("\nDELETED")
        for path, item in deleted.items():
            print(
                f"{path}: total={item['total']} removed={item['removed']} "
                f"kept={item['kept']} removed_ids={','.join(map(str, item['removed_ids']))}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Benchmark output directory or predictions directory")
    parser.add_argument("--delete", action="store_true", help="Delete candidate records from JSONL files")
    args = parser.parse_args()

    root = Path(args.path).expanduser().resolve()
    if not root.exists():
        print(f"error: path does not exist: {root}", file=sys.stderr)
        return 2

    files = find_prediction_files(root)
    if not files:
        print(f"error: no prediction JSONL files found under {root}", file=sys.stderr)
        return 2

    records, errors = load_records(files)
    deleted = delete_candidates(records) if args.delete else None
    print_report(root, files, records, errors, deleted)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
