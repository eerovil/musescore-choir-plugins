# Pull-request CI

The repository's CI is ordinary **GitHub Actions** defined in
[`.github/workflows/ci.yml`](../.github/workflows/ci.yml). It is intended to be
usable from **ChatGPT Chat through the GitHub connector**. It does not depend on
Codex, a Codex comment, an agent label, a GitHub App, or a repository secret.

## When it runs

The workflow starts automatically when a pull request is opened or updated,
when a commit reaches `main`, and when a person starts it with
`workflow_dispatch`. A newer run for the same pull request cancels the older
one, so the result attached to the current PR head is the result that matters.

## Checks

A pull request gets two independent checks on Ubuntu 24.04 with Python 3.13:

### Python and integration tests

This job installs the normal Python requirements plus FFmpeg, Poppler, Xvfb,
and the project's MuseScore 3.6.2 AppImage. The AppImage is pinned by version
and verified against its SHA-256 digest before execution. It runs:

```bash
python -m pytest \
  src/clean_score/tests \
  src/song_app/tests \
  src/scrollvideo/tests \
  -m "not browser" \
  --strict-markers \
  --durations=10 \
  -ra
```

That includes tests which otherwise skip when MuseScore, FFmpeg, or Poppler is
missing.

### Browser tests

This job installs `pytest-playwright` and Playwright's Chromium build, proves
that Chromium can start, and runs:

```bash
python -m pytest \
  src/song_app/tests \
  -m browser \
  --strict-markers \
  --durations=10 \
  -ra
```

Keeping browser tests separate makes browser setup failures distinguishable
from Python or music-rendering failures.

## Procedure for ChatGPT Chat

When verifying a code change in a pull request:

1. Push the proposed commit to the PR branch. The `pull_request` `synchronize`
   event starts CI automatically.
2. Read the workflow run associated with the PR's **current head SHA**. A green
   run for an older commit is not evidence for a newer one.
3. Require both `Python and integration tests` and `Browser tests` to finish
   successfully.
4. On failure, inspect the failed job log. Fix a code or test failure and push a
   new commit. Re-run only the failed job when the failure is clearly transient
   infrastructure, such as a download outage.
5. Report the exact tested SHA, both job conclusions, test counts, and any
   skips. Browser modules skipped by the non-browser job are expected only when
   they are selected and passed by the browser job.

The GitHub connector available to ChatGPT Chat can inspect PRs, workflow runs,
job steps and logs, and can re-run failed jobs. No Codex execution environment
is involved.

## Security and maintenance

The workflow grants the GitHub token only read access to repository contents,
does not persist checkout credentials, pins third-party actions to commit
SHAs, and verifies the downloaded MuseScore binary. Dependabot is configured
to propose updates to the pinned GitHub Actions revisions.