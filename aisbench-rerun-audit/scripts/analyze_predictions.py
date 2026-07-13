#!/usr/bin/env python3
"""Analyze benchmark result JSON files and optionally clear rerun candidates."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path


ANSWER_RE = re.compile(r"(?im)(?:^|\b)Answer\s*:\s*([ABCD])\b")


def compact_ids(values: list[object]) -> str:
    ids = sorted(x for x in values if isinstance(x, int))
    return ",".join(str(x) for x in ids)


def as_int_id(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def find_result_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix == ".json" else []
    if root.name == "results":
        search_root = root
    else:
        candidate = root / "results"
        search_root = candidate if candidate.exists() else root
    return sorted(p for p in search_root.rglob("*.json") if p.is_file())


def result_root_for(path: Path) -> Path:
    if path.name == "results":
        return path.parent
    if (path / "results").exists():
        return path
    return path.parent if path.is_file() else path


def is_false_case(correct: object) -> bool:
    if isinstance(correct, bool):
        return correct is False
    if isinstance(correct, list):
        return any(item is False for item in correct)
    return False


def string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if value is None:
        return []
    return [str(value)]


def classify_false_case(detail: dict) -> dict:
    origin = detail.get("origin_prediction")
    predictions = string_list(detail.get("predictions"))
    references = string_list(detail.get("references"))

    info = {
        "classification": "wrong_answer",
        "reason": "prediction_mismatch",
        "origin_len": len(origin) if isinstance(origin, str) else None,
        "has_think_close": False,
        "answer_any": None,
        "answer_after": None,
        "prediction": ",".join(predictions),
        "reference": ",".join(references),
    }

    if not isinstance(origin, str) or not origin:
        info["classification"] = "truncated"
        info["reason"] = "no_origin_prediction"
        return info

    answers_any = ANSWER_RE.findall(origin)
    info["answer_any"] = (answers_any or [None])[-1]

    if "</think>" in origin:
        info["has_think_close"] = True
        after = origin.split("</think>", 1)[1]
        answers_after = ANSWER_RE.findall(after)
        info["answer_after"] = (answers_after or [None])[-1]
        if info["answer_after"] is None:
            info["classification"] = "truncated"
            info["reason"] = "no_answer_after_think"
            return info
    elif info["answer_any"] is None:
        info["classification"] = "truncated"
        info["reason"] = "missing_final_answer"
        return info

    if not predictions:
        info["classification"] = "truncated"
        info["reason"] = "no_extracted_prediction"
        return info

    return info


def load_result_records(files: list[Path]) -> tuple[list[dict], list[dict]]:
    records: list[dict] = []
    errors: list[dict] = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            errors.append({"file": str(path), "error": str(exc)})
            continue

        details = data.get("details") if isinstance(data, dict) else None
        if not isinstance(details, dict):
            errors.append({"file": str(path), "error": "missing object field: details"})
            continue

        for key, detail in details.items():
            if not isinstance(detail, dict):
                continue
            item_id = as_int_id(key)
            correct = detail.get("correct")
            record = {
                "file": path,
                "id": item_id,
                "correct": correct,
                "is_false": is_false_case(correct),
            }
            if record["is_false"]:
                record.update(classify_false_case(detail))
            records.append(record)
    return records, errors


def is_tmp_prediction_file(path: Path) -> bool:
    return path.parent.name == "tmp"


def find_prediction_files(run_root: Path) -> tuple[list[Path], list[Path]]:
    predictions_root = run_root if run_root.name == "predictions" else run_root / "predictions"
    if not predictions_root.exists():
        return [], []
    files = sorted(p for p in predictions_root.rglob("*.jsonl") if p.is_file())
    skipped_tmp = [p for p in files if is_tmp_prediction_file(p)]
    return [p for p in files if not is_tmp_prediction_file(p)], skipped_tmp


def delete_prediction_ids(run_root: Path, ids: set[int]) -> tuple[dict[str, dict], list[Path]]:
    files, skipped_tmp = find_prediction_files(run_root)
    summary: dict[str, dict] = {}
    if not ids:
        return summary, skipped_tmp

    for path in files:
        total = kept = removed = 0
        removed_ids = []
        output_lines: list[str] = []
        with path.open(encoding="utf-8") as src:
            for line in src:
                if not line.strip():
                    output_lines.append(line)
                    continue
                total += 1
                try:
                    obj = json.loads(line)
                except Exception:  # noqa: BLE001
                    kept += 1
                    output_lines.append(line)
                    continue

                item_id = as_int_id(obj.get("id"))
                if item_id in ids:
                    removed += 1
                    removed_ids.append(item_id)
                    continue

                kept += 1
                output_lines.append(line)

        if removed:
            with path.open("r+", encoding="utf-8") as dst:
                dst.seek(0)
                dst.writelines(output_lines)
                dst.truncate()
            summary[str(path)] = {
                "action": "rewrote_records_in_place",
                "total": total,
                "removed": removed,
                "kept": kept,
                "removed_ids": sorted(removed_ids),
            }
    return summary, skipped_tmp


def print_report(
    root: Path,
    files: list[Path],
    records: list[dict],
    errors: list[dict],
    deleted: dict[str, dict] | None,
    skipped_tmp: list[Path] | None,
) -> None:
    false_cases = [r for r in records if r["is_false"]]
    truncated = [r for r in false_cases if r.get("classification") == "truncated"]
    wrong = [r for r in false_cases if r.get("classification") == "wrong_answer"]
    class_counts = Counter(r.get("classification", "unknown") for r in false_cases)
    reason_counts = Counter(r.get("reason", "unknown") for r in false_cases)

    print(f"root: {root}")
    print("source: results")
    print(f"files: {len(files)}")
    for path in files:
        print(f"  - {path}")
    print(f"records: {len(records)}")
    print(f"parse_errors: {len(errors)}")
    print(f"false_cases: {len(false_cases)}")
    print(f"classification_counts: {dict(class_counts)}")
    print(f"reason_counts: {dict(reason_counts)}")
    print(f"truncated_ids_sorted: {compact_ids([r['id'] for r in truncated])}")
    print(f"wrong_answer_ids_sorted: {compact_ids([r['id'] for r in wrong])}")

    if errors:
        print("\nPARSE_ERRORS")
        for err in errors:
            print(f"{err['file']} error={err['error']}")

    print("\nDETAIL")
    for rec in sorted(false_cases, key=lambda r: (str(r["file"]), r["id"] if r["id"] is not None else -1)):
        print(
            f"{rec['file']} id={rec['id']} class={rec['classification']} "
            f"reason={rec['reason']} pred={rec['prediction']} ref={rec['reference']} "
            f"origin_len={rec['origin_len']} has_think_close={rec['has_think_close']} "
            f"answer_any={rec['answer_any']} answer_after={rec['answer_after']}"
        )

    if deleted is not None:
        print("\nDELETED")
        if skipped_tmp is not None:
            print(f"skipped_tmp_prediction_files: {len(skipped_tmp)}")
            for path in skipped_tmp:
                print(f"  - {path}")
        if not deleted:
            print("no prediction records deleted")
        for path, item in deleted.items():
            print(
                f"{path}: action={item['action']} total={item['total']} "
                f"removed={item['removed']} kept={item['kept']} "
                f"removed_ids={','.join(map(str, item['removed_ids']))}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Benchmark output directory or results directory")
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete prediction JSONL records for truncated result IDs only",
    )
    args = parser.parse_args()

    root = Path(args.path).expanduser().resolve()
    if not root.exists():
        print(f"error: path does not exist: {root}", file=sys.stderr)
        return 2

    files = find_result_files(root)
    if not files:
        print(f"error: no result JSON files found under {root}", file=sys.stderr)
        return 2

    records, errors = load_result_records(files)
    truncated_ids = {r["id"] for r in records if r.get("classification") == "truncated" and isinstance(r["id"], int)}

    deleted = None
    skipped_tmp = None
    if args.delete:
        deleted, skipped_tmp = delete_prediction_ids(result_root_for(root), truncated_ids)

    print_report(root, files, records, errors, deleted, skipped_tmp)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
