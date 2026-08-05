#!/usr/bin/env python3
"""Analyze benchmark result JSON files and optionally clear rerun candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


FINAL_ANSWER_RE = re.compile(
    r"(?is)(?:answer\s*[:=]|final answer\s*[:=]|\\boxed\s*\{)([^\n}]{1,120})"
)
TOKEN_RE = re.compile(r"\\[A-Za-z]+|[A-Za-z0-9]+|[^\sA-Za-z0-9]")
ALNUM_RE = re.compile(r"[A-Za-z0-9]+")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
CHOICE_RE = re.compile(r"\b[ABCD]\b", re.IGNORECASE)


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


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def tokenize(value: str) -> list[str]:
    return TOKEN_RE.findall(value)


def truncated_sample(value: str | None, limit: int = 180) -> str | None:
    if value is None:
        return None
    value = normalize_text(value)
    return value[:limit]


def find_consecutive_repeats(
    tokens: list[str],
    block_sizes: tuple[int, ...] = (20, 40, 80, 160),
    min_repeats: int = 3,
) -> list[dict]:
    hits: list[dict] = []
    token_count = len(tokens)
    for block_size in block_sizes:
        index = 0
        while index + block_size * min_repeats <= token_count:
            block = tuple(tokens[index : index + block_size])
            repeat_count = 1
            next_index = index + block_size
            while (
                next_index + block_size <= token_count
                and tuple(tokens[next_index : next_index + block_size]) == block
            ):
                repeat_count += 1
                next_index += block_size
            if repeat_count >= min_repeats:
                hits.append(
                    {
                        "kind": "consecutive_ngram",
                        "block_tokens": block_size,
                        "occurrences": repeat_count,
                        "position": index,
                        "sample": " ".join(tokens[index : index + min(block_size, 60)]),
                    }
                )
                index = next_index
            else:
                index += 1
    return hits


def find_repeated_long_ngrams(
    tokens: list[str],
    block_tokens: int,
    min_occurrences: int,
    limit: int = 3,
) -> list[dict]:
    if len(tokens) < block_tokens:
        return []

    positions_by_hash: dict[bytes, list[int]] = defaultdict(list)
    for index in range(0, len(tokens) - block_tokens + 1):
        text = "\x1f".join(tokens[index : index + block_tokens])
        digest = hashlib.blake2b(text.encode("utf-8"), digest_size=12).digest()
        positions_by_hash[digest].append(index)

    hits: list[dict] = []
    for positions in positions_by_hash.values():
        if len(positions) < min_occurrences:
            continue
        first = tuple(tokens[positions[0] : positions[0] + block_tokens])
        exact_positions = [
            pos for pos in positions if tuple(tokens[pos : pos + block_tokens]) == first
        ]
        if len(exact_positions) >= min_occurrences:
            hits.append(
                {
                    "kind": "repeated_ngram_anywhere",
                    "block_tokens": block_tokens,
                    "occurrences": len(exact_positions),
                    "positions": exact_positions[:8],
                    "sample": " ".join(first[:60]),
                }
            )

    hits.sort(key=lambda item: (item["occurrences"], item["block_tokens"]), reverse=True)
    return hits[:limit]


def find_repeated_text_units(
    origin: str,
    *,
    unit: str,
    min_chars: int,
    min_occurrences: int,
    limit: int = 3,
) -> list[dict]:
    if unit == "line":
        parts = origin.splitlines()
    elif unit == "paragraph":
        parts = re.split(r"\n\s*\n+", origin)
    else:
        raise ValueError(f"unknown repeat unit: {unit}")

    normalized = [normalize_text(part) for part in parts]
    counts = Counter(part for part in normalized if len(part) >= min_chars)
    hits = []
    for text, count in counts.most_common():
        if count < min_occurrences:
            continue
        hits.append(
            {
                "kind": f"repeated_{unit}",
                "occurrences": count,
                "sample": text[:260],
            }
        )
        if len(hits) >= limit:
            break
    return hits


def detect_large_repetition(origin: object) -> dict:
    info = {
        "large_repeat": False,
        "large_repeat_reasons": "",
        "large_repeat_summary": "",
    }
    if not isinstance(origin, str) or not origin:
        return info

    tokens = tokenize(origin)
    hits = []
    hits.extend(find_consecutive_repeats(tokens))
    hits.extend(find_repeated_long_ngrams(tokens, block_tokens=80, min_occurrences=3))
    hits.extend(find_repeated_long_ngrams(tokens, block_tokens=160, min_occurrences=2))
    hits.extend(
        find_repeated_text_units(
            origin,
            unit="paragraph",
            min_chars=180,
            min_occurrences=3,
        )
    )
    hits.extend(
        find_repeated_text_units(
            origin,
            unit="line",
            min_chars=100,
            min_occurrences=3,
        )
    )

    if not hits:
        return info

    reasons = Counter(hit["kind"] for hit in hits)
    summary_parts = []
    for hit in hits[:4]:
        if hit["kind"] in {"consecutive_ngram", "repeated_ngram_anywhere"}:
            summary_parts.append(
                f"{hit['kind']}:block={hit['block_tokens']}:occ={hit['occurrences']}:"
                f"sample={truncated_sample(str(hit.get('sample')))}"
            )
        else:
            summary_parts.append(
                f"{hit['kind']}:occ={hit['occurrences']}:"
                f"sample={truncated_sample(str(hit.get('sample')))}"
            )

    info["large_repeat"] = True
    info["large_repeat_reasons"] = ",".join(f"{key}:{value}" for key, value in sorted(reasons.items()))
    info["large_repeat_summary"] = " | ".join(summary_parts)
    return info


def repeat_review_status(record: dict) -> str:
    if record.get("is_false"):
        return f"false/{record.get('classification', 'unknown')}"
    return "correct"


def repeat_review_snippet(record: dict, limit: int = 260) -> str:
    summary = str(record.get("large_repeat_summary") or "")
    if not summary:
        return ""
    first = summary.split(" | ", 1)[0]
    return truncated_sample(first, limit=limit) or ""


def alnum_tokens(values: list[str]) -> set[str]:
    tokens = set()
    for value in values:
        tokens.update(token.lower() for token in ALNUM_RE.findall(value))
    return tokens


def number_tokens(values: list[str]) -> set[str]:
    numbers = set()
    for value in values:
        numbers.update(NUMBER_RE.findall(value))
    return numbers


def choice_tokens(values: list[str]) -> set[str]:
    choices = set()
    for value in values:
        choices.update(choice.upper() for choice in CHOICE_RE.findall(value))
    return choices


def predictions_unrelated_to_references(predictions: list[str], references: list[str]) -> bool:
    if not predictions or not references:
        return False

    nonempty_predictions = [prediction.strip() for prediction in predictions if prediction.strip()]
    nonempty_references = [reference.strip() for reference in references if reference.strip()]
    if not nonempty_predictions or not nonempty_references:
        return False

    reference_numbers = number_tokens(nonempty_references)
    prediction_numbers = number_tokens(nonempty_predictions)
    if reference_numbers:
        return not prediction_numbers

    reference_choices = choice_tokens(nonempty_references)
    if reference_choices:
        return not choice_tokens(nonempty_predictions)

    reference_tokens = alnum_tokens(nonempty_references)
    prediction_tokens = alnum_tokens(nonempty_predictions)
    if reference_tokens & prediction_tokens:
        return False

    prediction_text = " ".join(nonempty_predictions)
    return len(prediction_text) > 16 or len(prediction_tokens) > 4


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
        "prediction_reference_relation": "not_checked",
        "prediction": ",".join(predictions),
        "reference": ",".join(references),
    }

    if not isinstance(origin, str) or not origin:
        info["classification"] = "truncated"
        info["reason"] = "no_origin_prediction"
        return info

    answers_any = FINAL_ANSWER_RE.findall(origin)
    info["answer_any"] = truncated_sample((answers_any or [None])[-1], limit=80)

    if "</think>" in origin:
        info["has_think_close"] = True
        after = origin.split("</think>", 1)[1]
        answers_after = FINAL_ANSWER_RE.findall(after)
        info["answer_after"] = truncated_sample((answers_after or [None])[-1], limit=80)
    else:
        info["classification"] = "truncated"
        info["reason"] = "missing_think_close"
        return info

    if not predictions:
        info["classification"] = "truncated"
        info["reason"] = "no_extracted_prediction"
        return info

    if predictions_unrelated_to_references(predictions, references):
        info["classification"] = "truncated"
        info["reason"] = "prediction_reference_unrelated"
        info["prediction_reference_relation"] = "unrelated"
        return info

    info["prediction_reference_relation"] = "related"
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
            origin = detail.get("origin_prediction")
            record = {
                "file": path,
                "id": item_id,
                "correct": correct,
                "is_false": is_false_case(correct),
                "origin_len": len(origin) if isinstance(origin, str) else None,
                "prediction": ",".join(string_list(detail.get("predictions"))),
                "reference": ",".join(string_list(detail.get("references"))),
            }
            record.update(detect_large_repetition(origin))
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
    large_repeats = [r for r in records if r.get("large_repeat")]
    class_counts = Counter(r.get("classification", "unknown") for r in false_cases)
    reason_counts = Counter(r.get("reason", "unknown") for r in false_cases)
    repeat_reason_counts = Counter()
    for rec in large_repeats:
        for item in str(rec.get("large_repeat_reasons", "")).split(","):
            if not item:
                continue
            key, _, value = item.partition(":")
            repeat_reason_counts[key] += int(value or 1)

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
    print(f"large_repeat_cases: {len(large_repeats)}")
    print(f"large_repeat_reason_counts: {dict(repeat_reason_counts)}")
    print(f"large_repeat_ids_sorted: {compact_ids([r['id'] for r in large_repeats])}")

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
            f"answer_any={rec['answer_any']} answer_after={rec['answer_after']} "
            f"pred_ref_relation={rec['prediction_reference_relation']} "
            f"large_repeat={rec.get('large_repeat', False)}"
        )

    if large_repeats:
        print("\nLARGE_REPEAT_DETAIL")
        for rec in sorted(large_repeats, key=lambda r: (str(r["file"]), r["id"] if r["id"] is not None else -1)):
            print(
                f"{rec['file']} id={rec['id']} correct={rec['correct']} "
                f"is_false={rec['is_false']} reasons={rec['large_repeat_reasons']} "
                f"summary={rec['large_repeat_summary']}"
            )

        print("\nLARGE_REPEAT_SNIPPET_SUMMARY")
        for rec in sorted(large_repeats, key=lambda r: (str(r["file"]), r["id"] if r["id"] is not None else -1)):
            print(
                f"id={rec['id']} status={repeat_review_status(rec)} "
                f"pred={rec.get('prediction', '')} ref={rec.get('reference', '')} "
                f"origin_len={rec.get('origin_len')} reasons={rec['large_repeat_reasons']} "
                f"snippet={repeat_review_snippet(rec)}"
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
