---
name: aisbench-rerun-audit
description: Analyze benchmark result JSON files, especially ais_bench/OpenICL outputs with accuracy and details fields, to separate correct=false cases into direct wrong answers versus truncated or unusable model outputs. Use when Codex needs to inspect results/**/*.json, classify failed cases from origin_prediction, predictions, references, and correct, report rerun candidate IDs, wait for user instruction, and optionally clear prediction JSONL records for truncated cases before --reuse reruns.
---

# AisBench Rerun Audit

## Workflow

Run the bundled script from the repository root:

```bash
python3 .codex/skills/aisbench-rerun-audit/scripts/analyze_predictions.py <output-dir>
```

`<output-dir>` is either a timestamp output directory that contains `results/` or the `results/` directory itself.

The script recursively reads `results/**/*.json` files in this shape:

```json
{
  "accuracy": 92.4,
  "details": {
    "0": {
      "origin_prediction": "model raw output",
      "predictions": ["D"],
      "references": ["D"],
      "correct": [true]
    }
  },
  "type": "GEN"
}
```

For every detail entry where `correct` contains `false`, classify the case as:

- `wrong_answer`: the model produced a parseable final answer, but it does not match the reference.
- `truncated`: the raw output is missing, empty, lacks a final `Answer: A|B|C|D`, lacks an answer after `</think>` when a think section closes, or produced no extracted prediction.

Treat `truncated` IDs as rerun candidates. Treat `wrong_answer` IDs as real model mistakes unless the user asks for deeper manual review.

## Confirmation Before Deletion

After analysis, report the conclusion and wait for the user's instruction. Do not delete records during the first analysis pass.

Only delete after explicit confirmation. Run:

```bash
python3 .codex/skills/aisbench-rerun-audit/scripts/analyze_predictions.py <output-dir> --delete
```

Deletion removes prediction JSONL records whose IDs were classified as `truncated` from non-`tmp` files under `predictions/`. It does not delete or inspect `predictions/**/tmp/*.jsonl`.

When rewriting prediction JSONL files, preserve the original file owner/group relationship. Prefer in-place truncation and rewrite of the existing file after preparing the filtered content, rather than replacing the file with a new temporary file.

This cleanup is useful before `--reuse` reruns because ais_bench appends new prediction rows; old truncated rows can otherwise remain and affect later evaluation.

If writing outside the workspace requires approval, request escalation and explain that the command rewrites prediction JSONL files for truncated result IDs.

## Reporting

Keep the final response concise:

- State whether deletion was performed.
- Include total result records, `correct=false` count, classification counts, and reason counts.
- List `truncated` IDs separately from `wrong_answer` IDs.
- Explain the conclusion: which IDs should be rerun and which look like direct wrong answers.
- If deletion was performed, mention modified prediction files and removed IDs.
- If only analysis was performed, say deletion is pending user confirmation.

## Notes

- Use `results/` as the source of truth for deciding which cases failed.
- Do not use `predictions/` to decide failure status unless the user explicitly asks.
- Do not analyze files under `predictions/**/tmp/`.
- Prefer relative paths in examples so this skill works after moving the repository.
