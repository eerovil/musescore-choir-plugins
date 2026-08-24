"""What the unattended deploy is allowed to call a dirty checkout.

`scripts/deploy-song-app.sh` refuses to deploy when `git status --porcelain` says
anything, on the reasoning that someone is editing the live checkout. The poller
checks each issue out into `.worktrees/issue-N` inside this repo, so that folder
must be ignored or every deploy made while a card is open refuses — quietly, since
the app keeps serving the old code and only the timer's journal says why.
"""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
    )


def test_agent_worktrees_are_ignored():
    result = _git("check-ignore", "-q", ".worktrees/issue-1")
    assert result.returncode == 0, (
        "A poller worktree under .worktrees/ shows up as an untracked file, and the "
        "unattended deploy reads any untracked file as a dirty checkout and refuses."
    )


def test_the_deploy_refuses_on_a_dirty_checkout():
    """The refusal itself is deliberate — this pins that we did not weaken it."""
    script = (REPO / "scripts" / "deploy-song-app.sh").read_text()
    assert 'git status --porcelain' in script
    assert 'checkout is dirty' in script
