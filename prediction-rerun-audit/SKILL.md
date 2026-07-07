---
name: prediction-rerun-audit
description: Analyze benchmark output directories that contain predictions JSONL files, especially ais_bench/OpenICL prediction outputs, to decide which cases should be rerun with --reuse. Use when Codex needs to inspect model prediction text for failed requests, missing prediction fields, missing think-close markers, or missing final answers after the think section, report rerun candidate IDs, ask the user for confirmation, and optionally delete old prediction JSONL records so rerun results can append cleanly.
---

# Prediction Rerun Audit

## Workflow

Use the bundled script for deterministic JSONL handling:

```bash
python3 /home/yejj/.codex/skills/prediction-rerun-audit/scripts/analyze_predictions.py <output-dir>
```

`<output-dir>` is the timestamp directory that contains `predictions/`, for example:

```bash
python3 /home/yejj/.codex/skills/prediction-rerun-audit/scripts/analyze_predictions.py /home/yejj/scripts/outputs/default/20260707_104226
```

The script recursively reads `predictions/**/*.jsonl` and classifies rerun candidates:

- `no_prediction`: record has no string `prediction`
- `missing_think_close`: `prediction` exists but does not contain `</think>`
- `no_answer_after_think`: `</think>` exists but no `Answer: A|B|C|D` appears after it

Report the counts, IDs, file/line details, and any warnings. Do not inspect `results/` to decide rerun candidates unless the user explicitly asks; this workflow is based on model output in `predictions/`.

## Confirmation Before Deletion

After analysis, ask the user whether to delete the old prediction records for the rerun candidate IDs.

Only delete after explicit confirmation. Run:

```bash
python3 /home/yejj/.codex/skills/prediction-rerun-audit/scripts/analyze_predictions.py <output-dir> --delete
```

Deletion rewrites each prediction JSONL file without candidate records. This is preferred over setting `success=false` because ais_bench writes rerun results in append mode; old rows can otherwise remain in the file and interfere with later evaluation.

If writing outside the workspace requires approval, request escalation and explain that the command rewrites prediction JSONL files in the benchmark output directory.

## Reporting

Keep the final response concise:

- State whether deletion was performed.
- Include total records analyzed, rerun candidate count, and reason counts.
- List candidate IDs sorted numerically.
- Mention which files were modified and how many rows were removed.
- If only analysis was performed, say that deletion is pending user confirmation.

## Notes

- Treat empty failed JSONL files as valid.
- Preserve non-candidate lines byte-for-byte when deleting.
- If duplicate IDs exist, delete all candidate records for that ID.
- If the user provides a directory that is already `predictions/`, pass it directly; the script accepts either the run directory or a predictions directory.
