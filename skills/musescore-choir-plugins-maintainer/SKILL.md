---
name: musescore-choir-plugins-maintainer
description: Maintain, implement, test, and review changes in eerovil/musescore-choir-plugins from ChatGPT Chat using the connected GitHub repository and its GitHub Actions CI. Use for issues, pull requests, code review, bug fixes, features, and repository maintenance in this exact repository. Do not use for unrelated repositories or when the user explicitly requests Codex or local-only work.
---

# MuseScore Choir Plugins Maintainer

## Mission

Take repository work from initial inspection through a review-ready pull request in
**ChatGPT Chat**, using the connected GitHub repository and GitHub Actions. Do not
depend on Codex. Continue through ordinary engineering decisions, implementation,
tests, and CI failures. Stop only when a genuine product decision or inaccessible
external dependency blocks safe progress.

## Canonical repository

Work only in:

```text
eerovil/musescore-choir-plugins
```

Never assume repository state from an earlier chat. Fetch the current default branch,
issue, pull request, branch head, comments, reviews, and CI status before acting.

## Required orientation

Before changing code:

1. Read the current `CLAUDE.md`. It is the repository's authoritative agent guide.
2. Read `docs/CI.md` for the ChatGPT-operated GitHub Actions procedure.
3. Read the task source:
   - for an issue, read the issue and all comments;
   - for a pull request, read the PR, changed files, review discussion, and current
     head SHA;
   - for an untracked request, inspect the relevant code, tests, `README.md`,
     `DESIGN.md`, and other nearby documentation.
4. Convert the request into concrete acceptance criteria. Resolve normal technical
   details from the repository rather than asking the user to design the
   implementation.
5. Check for an existing branch or PR before creating a competing one.

For an existing PR, continue on its current head branch. Do not open a duplicate PR.
For a new task, branch from the latest `main`; use a descriptive name such as
`chatgpt/issue-42-fix-lyrics` or `chatgpt/improve-render-progress`.

## Working rules

- Use the GitHub connector for repository reads and writes.
- Do not write directly to `main`.
- Do not depend on a Codex execution environment, Codex comments, agent labels, or a
  local checkout.
- Do not claim that local tests ran when only GitHub Actions ran.
- Preserve the existing architecture and reuse established musical-processing logic;
  do not create parallel implementations merely to make a change easier.
- Keep root wrappers thin and follow the execution and environment rules in
  `CLAUDE.md`.
- Add or update tests for behavior changes. Prefer the repository's existing helpers,
  fixtures, and end-to-end paths over shallow mock-only coverage.
- Never commit secrets, `.env`, credentials, tokens, generated personal song data, or
  other ignored runtime state.
- Treat `songs/` as real shared user data. Do not mutate it unless the task explicitly
  concerns that song and the effect is understood. Repository tests should use their
  own temporary data and checked-in fixtures.
- Make focused commits with clear messages. Keep unrelated cleanup out of the PR.
- Do not merge a PR unless the user explicitly asks for a merge.

If a local shell or worktree later becomes available, follow the worktree setup in
`CLAUDE.md` before running anything, including linking `.venv`, `.env`, and `songs/`.
The GitHub Actions result remains the authoritative PR verification.

## Implementation workflow

1. Inspect the current state and identify the smallest coherent solution.
2. Change production code and tests together when behavior changes.
3. Review the resulting diff for accidental scope, generated files, secrets, and
   architectural duplication.
4. Push the branch and open or update a pull request with:
   - the user-visible and technical outcome;
   - important design decisions;
   - tests added or changed;
   - any known limitation that is genuinely relevant.
5. Let GitHub Actions run automatically.
6. Verify CI against the PR's **current head SHA**, not an earlier green commit.
7. On a test failure, inspect the failed job log, diagnose it, fix the code or test,
   and push a new commit.
8. Re-run a failed job without a code change only when the failure is clearly transient
   infrastructure, such as a download or runner outage.
9. Repeat until the current PR head is green or a genuine blocker is reached.

## CI contract

A review-ready PR requires both checks to pass:

```text
Python and integration tests
Browser tests
```

For every verification report:

- state the exact tested PR head SHA;
- state both job conclusions;
- report test counts and skips/deselections from the logs;
- ensure browser modules skipped by the non-browser job were actually selected and
  passed by the browser job;
- do not call the work complete while either job is pending, failed, or attached only
  to an older SHA.

Use `docs/CI.md` as the detailed source of truth if the workflow changes.

## When to ask a blocking question

Continue without asking about ordinary implementation choices, naming, refactoring,
test repair, error diagnosis, or details inferable from repository conventions.

Stop and ask only when at least one of these is true:

- two materially different user-facing product behaviors are both reasonable and the
  issue, code, tests, and docs do not choose between them;
- the requested action would destructively modify real shared song data;
- required credentials, accounts, private files, hardware, or external services are
  unavailable and no safe substitute exists;
- the user must authorize a merge, release, publication, deletion, or similarly
  consequential action that they did not already request.

When blocked, finish every non-blocked part first, then ask one precise question that
states the alternatives and their consequences.

## Existing PR mode

When the user asks to continue a PR:

1. Fetch the current PR metadata and head SHA.
2. Read all review and conversation comments.
3. Inspect the changed files and existing CI for that exact SHA.
4. Continue on the same branch.
5. Address review findings, implementation gaps, and CI failures until the PR is ready
   or a genuine product question blocks progress.

Do not discard or rewrite unrelated work already present on the branch.

## Code review mode

When the user asks only for review, do not modify the branch unless they also ask for
fixes.

- Inspect the complete diff and relevant surrounding code.
- Check the current-head CI results.
- Put findings first, ordered by severity.
- For each finding, identify the path and line or narrow code region, explain the
  concrete failure mode, and suggest the smallest suitable correction.
- Prioritize correctness, regressions, unsafe data changes, architectural duplication,
  and missing behavioral tests over style preferences.
- State explicitly when no actionable findings remain, while noting any verification
  gap that CI cannot cover.

## Communication contract

Give short progress updates after meaningful milestones: orientation, first important
finding, implementation, PR creation, and CI diagnosis. Do not narrate every API call.

A completion report should include:

```text
Outcome
PR and branch
Exact tested SHA
Python and integration tests: conclusion and count
Browser tests: conclusion and count
Remaining blocker or limitation, if any
```

Never substitute a confident summary for evidence from the current repository and
current-head CI.

## Starter prompts

Use the copy-ready prompts in `references/starter-prompts.md`. The general prompt is
suitable for most new chats; the issue, PR, and review variants make the starting mode
unambiguous.
