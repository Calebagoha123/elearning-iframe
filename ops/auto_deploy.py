#!/usr/bin/env python3
"""Deploy CI-approved main commits on the existing production Docker host."""

import argparse
import fcntl
import json
import logging
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

LOG = logging.getLogger("elearning.deploy")
REPOSITORY = "Calebagoha123/elearning-iframe"


def run(repo, *args, capture=False):
    return subprocess.run(
        args,
        cwd=repo,
        check=True,
        stdin=subprocess.DEVNULL,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    ).stdout


def ci_passed(commit):
    query = urlencode(
        {"branch": "main", "event": "push", "head_sha": commit, "per_page": 10}
    )
    request = Request(
        f"https://api.github.com/repos/{REPOSITORY}/actions/workflows/ci.yml/runs?{query}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "elearning-vm-deploy",
        },
    )
    with urlopen(request, timeout=20) as response:
        runs = json.load(response)["workflow_runs"]
    matching = [
        r
        for r in runs
        if r["head_sha"] == commit
        and r["head_branch"] == "main"
        and r["event"] == "push"
    ]
    if not matching:
        return False
    latest = max(matching, key=lambda r: (r["id"], r.get("run_attempt", 1)))
    return latest["status"] == "completed" and latest["conclusion"] == "success"


def compose(repo, *args, capture=False, override=None):
    command = [
        "docker",
        "compose",
        "--env-file",
        str(repo / ".env.production"),
        "-f",
        str(repo / "docker-compose.prod.yml"),
    ]
    if override:
        command += ["-f", str(override)]
    return run(repo, *command, *args, capture=capture)


def wait_for_web(repo, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        container = compose(repo, "ps", "--all", "-q", "web", capture=True).strip()
        if container:
            state = json.loads(run(repo, "docker", "inspect", container, capture=True))[
                0
            ]["State"]
            if state.get("Health", {}).get("Status") == "healthy":
                return
            if state["Status"] in {"exited", "dead"}:
                break
        time.sleep(3)
    raise RuntimeError(
        "The web container did not become healthy; inspect the web service logs."
    )


def deploy(repo, force=False):
    repo = repo.resolve()
    git_dir = Path(
        run(repo, "git", "rev-parse", "--absolute-git-dir", capture=True).strip()
    )
    with (git_dir / "elearning-deploy.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            LOG.info("Another deployment is already running.")
            return
        if run(repo, "git", "branch", "--show-current", capture=True).strip() != "main":
            raise RuntimeError("Production must be on main; no branch was changed.")
        if run(
            repo, "git", "status", "--porcelain", "--untracked-files=no", capture=True
        ).strip():
            raise RuntimeError(
                "Production has modified tracked files; no changes were overwritten."
            )
        origin = run(repo, "git", "remote", "get-url", "origin", capture=True).strip()
        origin_path = (
            origin.lower()[:-4] if origin.lower().endswith(".git") else origin.lower()
        )
        if not origin_path.endswith(
            ("/" + REPOSITORY.lower(), ":" + REPOSITORY.lower())
        ):
            raise RuntimeError(
                "The origin repository does not match the deployment repository."
            )
        run(repo, "git", "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
        commit = run(repo, "git", "rev-parse", "origin/main", capture=True).strip()
        if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            raise RuntimeError("Git returned an invalid commit identifier.")
        # A fast-forward merge alone also accepts an ahead-of-origin checkout.
        # Refuse that case so locally committed, untested code cannot be deployed.
        run(repo, "git", "merge-base", "--is-ancestor", "HEAD", commit)
        state_file = git_dir / "elearning-deployed-revision"
        if (
            not force
            and state_file.exists()
            and state_file.read_text().strip() == commit
        ):
            LOG.info("Already deployed %s.", commit[:12])
            return
        if not ci_passed(commit):
            LOG.info("Waiting for successful main-branch CI for %s.", commit[:12])
            return
        if not (repo / ".env.production").is_file():
            raise RuntimeError("The existing .env.production file is missing.")
        previous = compose(repo, "ps", "--all", "-q", "web", capture=True).strip()
        rollback_tag = None
        if previous:
            image = json.loads(run(repo, "docker", "inspect", previous, capture=True))[
                0
            ]["Image"]
            rollback_tag = "elearning-rollback:" + image.split(":", 1)[-1][:16]
            run(repo, "docker", "tag", image, rollback_tag)
        run(repo, "git", "merge", "--ff-only", commit)
        LOG.info(
            "Building %s; the current container remains running during the build.",
            commit[:12],
        )
        compose(repo, "build", "web")
        try:
            compose(repo, "up", "-d", "--no-deps", "--no-build", "web")
            wait_for_web(repo)
        except Exception:
            if rollback_tag:
                # Roll back the app image only; never reverse database migrations.
                override = git_dir / "elearning-rollback.json"
                override.write_text(
                    json.dumps({"services": {"web": {"image": rollback_tag}}})
                )
                try:
                    LOG.error("New container failed; restoring the previous app image.")
                    compose(
                        repo,
                        "up",
                        "-d",
                        "--no-deps",
                        "--no-build",
                        "--pull",
                        "never",
                        "web",
                        override=override,
                    )
                    wait_for_web(repo)
                finally:
                    override.unlink(missing_ok=True)
            raise
        temporary = state_file.with_suffix(".tmp")
        temporary.write_text(commit + "\n")
        temporary.replace(state_file)
        LOG.info("Successfully deployed %s.", commit[:12])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("/opt/elearning-iframe"))
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild even when the recorded deployment is current; CI is still required.",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        deploy(args.repo, args.force)
    except Exception:
        LOG.exception("Deployment failed.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
