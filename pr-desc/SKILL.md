---
name: pr-desc
description: Generate PR descriptions from a repository path and starting commit id by reading upstream PR templates such as GitHub, GitCode, Gitee, or GitLab templates, collecting git change evidence, and returning a template-compliant PR body. Use when asked to draft, fill, or summarize a pull request or merge request description from commits.
---

# PR Desc

## Overview

Use this skill to generate a PR description from two inputs: project path and starting commit id. Treat the starting commit as the first commit included in the PR unless the user explicitly says it is the base commit.

## Workflow

1. Run the collector from this skill directory:

```bash
python3 scripts/collect_pr_context.py PROJECT_PATH START_COMMIT_ID
```

2. Read the collector output before drafting. It includes discovered PR templates, commit range, commit messages, changed files, diff stats, a limited patch excerpt, and `git diff --check` results.

3. Fill the discovered PR template directly. Preserve the template's section order, headings, checklist wording, and local language. If the template is Chinese, write the PR description in Chinese.

4. Base every claim on the collected evidence. Summarize changes by concept and behavior, not by restating every line of the diff.

5. Return only the completed PR description unless the user asks for analysis or intermediate details.

## Template Discovery

Rely on the collector first. It searches common upstream locations, including:

- `.gitcode/PULL_REQUEST_TEMPLATE.md`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `.github/PULL_REQUEST_TEMPLATE/*.md`
- `.gitee/PULL_REQUEST_TEMPLATE.md`
- `.gitlab/merge_request_templates/*.md`
- repository-root `PULL_REQUEST_TEMPLATE.md`

If multiple templates are found, use the first listed template unless the user named another one or the collector output makes a better match obvious.

## Drafting Rules

- For interface, output artifact, compatibility, security, and test impact sections, write `none`, `N/A`, or the template's existing equivalent only when the diff supports it.
- For docs-only changes, usually mark API, CLI, export artifacts, compatibility, runtime safety, and unit tests as not applicable. Still mention any documentation validation actually performed.
- For code changes, inspect changed files enough to identify public API, CLI, config, behavior, persistence, security, and testing impact.
- For validation, include only commands that were actually run or evidence from CI mentioned by the user. If no functional test was run, state that clearly in the template's expected style.
- For checklist items, preserve Markdown checkbox syntax. Mark an item checked only when evidence supports it; otherwise use unchecked or `N/A` according to the template.
- If no PR template is found, produce a concise generic PR body with impact, change summary, validation, and risks.

## Range Handling

Default range: include `START_COMMIT_ID` through `HEAD`. The collector uses the parent of `START_COMMIT_ID` as the diff base when available.

If the user explicitly provides a base commit rather than the first included commit, collect evidence manually with `git log BASE..HEAD`, `git diff --stat BASE HEAD`, and `git diff --name-status BASE HEAD`, then draft from that evidence.

## Manual Fallback

If the collector cannot run, gather the same evidence manually:

```bash
git -C PROJECT_PATH log --reverse --format='%h %s' START_COMMIT_ID^..HEAD
git -C PROJECT_PATH diff --stat START_COMMIT_ID^ HEAD
git -C PROJECT_PATH diff --name-status START_COMMIT_ID^ HEAD
git -C PROJECT_PATH diff --check START_COMMIT_ID^ HEAD
```

Then read the repository PR template and fill it using the drafting rules above.
