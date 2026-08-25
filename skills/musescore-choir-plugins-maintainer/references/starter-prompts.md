# Starter prompts

Replace the bracketed fields, then paste one of these into a new ChatGPT chat. Select
or mention `@musescore-choir-plugins-maintainer` when the skill is installed.

## General repository task

```text
Use @musescore-choir-plugins-maintainer.

Work in eerovil/musescore-choir-plugins. Read the current CLAUDE.md and docs/CI.md,
inspect the latest repository state, and complete this task:

[TASK]

Use the connected GitHub repository, not Codex. Create or continue the appropriate
branch and pull request, add or update tests, and keep fixing the implementation and CI
until both "Python and integration tests" and "Browser tests" pass for the PR's current
head SHA. Stop only for a genuinely blocking product decision or inaccessible external
dependency. Do not merge unless I explicitly ask.
```

## Implement a GitHub issue

```text
Use @musescore-choir-plugins-maintainer and take issue #[NUMBER] in
eerovil/musescore-choir-plugins from investigation through a review-ready pull request.
Read CLAUDE.md, docs/CI.md, the issue, and all comments first. Check that no existing PR
already owns the work. Implement the smallest coherent solution with behavioral tests,
then keep iterating on current-head GitHub Actions until both CI jobs are green. Stop
only for a genuine product question. Do not merge unless I ask.
```

## Continue an existing pull request

```text
Use @musescore-choir-plugins-maintainer and continue PR #[NUMBER] in
eerovil/musescore-choir-plugins. Read CLAUDE.md, docs/CI.md, the full PR diff, all review
and conversation comments, and CI for the current head SHA. Continue on the existing PR
branch; do not create a competing PR. Address implementation gaps, review findings, and
CI failures until both checks are green or a genuine product decision blocks progress.
Do not merge unless I ask.
```

## Code review only

```text
Use @musescore-choir-plugins-maintainer to review PR #[NUMBER] in
eerovil/musescore-choir-plugins. Read CLAUDE.md, docs/CI.md, the complete diff, relevant
surrounding code, comments, and CI for the current head SHA. Do not change the branch.
Report actionable findings first, ordered by severity, with precise file locations,
concrete failure modes, and the smallest suitable fixes. State clearly if no actionable
findings remain and identify any verification gap not covered by CI.
```

## Broad autonomous maintenance task

```text
Use @musescore-choir-plugins-maintainer.

Work in eerovil/musescore-choir-plugins and complete the following maintenance goal:

[GOAL]

Read CLAUDE.md and docs/CI.md first, then inspect the relevant code, tests, issues, and
recent PR history. Make reasonable engineering decisions yourself, preserve the existing
architecture, and continue through implementation, tests, PR creation, and CI repair.
Stop only when a user-facing product choice cannot be inferred safely. Leave a green,
review-ready PR and do not merge unless I ask.
```
