"""Exercise the VM updater against real, isolated Git histories; Docker is mocked."""

from contextlib import contextmanager
import io
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ops import auto_deploy


class DeploymentTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.source = self.root / "source"
        self.remote = self.root / "origin.git"
        self.server = self.root / "server"
        self.source.mkdir()
        self.git(self.source, "init", "-b", "main")
        (self.source / "version").write_text("old")
        self.git(self.source, "add", ".")
        self.git(self.source, "commit", "-m", "Initial")
        self.git(self.root, "clone", "--bare", str(self.source), str(self.remote))
        self.git(self.root, "clone", str(self.remote), str(self.server))
        self.git(
            self.server,
            "remote",
            "set-url",
            "origin",
            "git@github.com:Calebagoha123/elearning-iframe.git",
        )
        self.old = self.git(self.server, "rev-parse", "HEAD").strip()
        (self.source / "version").write_text("new")
        self.git(self.source, "commit", "-am", "New release")
        self.git(self.source, "push", str(self.remote), "main")
        self.new = self.git(self.source, "rev-parse", "HEAD").strip()
        (self.server / ".env.production").write_text("LOCAL_TEST=1\n")
        self.state = self.server / ".git/elearning-deployed-revision"
        self.state.write_text(self.old + "\n")
        self.actual_run = auto_deploy.run

    def git(self, repo, *args):
        return subprocess.run(
            [
                "git",
                "-c",
                "user.name=Deployment test",
                "-c",
                "user.email=deploy@example.invalid",
                *args,
            ],
            cwd=repo,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout

    def run_command(self, repo, *args, capture=False):
        if args[:2] == ("git", "fetch"):
            return self.actual_run(
                repo, "git", "fetch", str(self.remote), args[-1], capture=capture
            )
        if args[:2] == ("docker", "inspect"):
            return json.dumps([{"Image": "sha256:" + "a" * 64}])
        if args[:2] == ("docker", "tag"):
            return None
        return self.actual_run(repo, *args, capture=capture)

    @contextmanager
    def environment(self, passed=True):
        with (
            patch.object(auto_deploy, "run", side_effect=self.run_command),
            patch.object(auto_deploy, "ci_passed", return_value=passed) as ci,
            patch.object(auto_deploy, "compose", return_value="") as compose,
            patch.object(auto_deploy, "wait_for_web") as healthy,
        ):
            yield ci, compose, healthy

    def test_unapproved_commit_does_not_change_checkout_or_containers(self):
        with self.environment(passed=False) as (_, compose, _):
            auto_deploy.deploy(self.server)
        self.assertEqual(self.git(self.server, "rev-parse", "HEAD").strip(), self.old)
        self.assertEqual(self.state.read_text().strip(), self.old)
        compose.assert_not_called()

    def test_success_records_deployment_only_after_health_check(self):
        with self.environment() as (ci, compose, healthy):
            auto_deploy.deploy(self.server)
            self.assertEqual(
                self.git(self.server, "rev-parse", "HEAD").strip(), self.new
            )
            self.assertEqual(self.state.read_text().strip(), self.new)
            healthy.assert_called_once_with(self.server)
            ci.assert_called_once_with(self.new)
            self.assertTrue(
                any(
                    call.args[1:] == ("build", "web") for call in compose.call_args_list
                )
            )
            compose.reset_mock()
            auto_deploy.deploy(self.server)
            compose.assert_not_called()

    def test_dirty_or_locally_committed_checkout_is_not_overwritten(self):
        (self.server / "version").write_text("server customization")
        with self.environment() as (_, compose, _):
            with self.assertRaisesRegex(RuntimeError, "modified tracked files"):
                auto_deploy.deploy(self.server)
            compose.assert_not_called()
            self.git(self.server, "commit", "-am", "Local change")
            with self.assertRaises(subprocess.CalledProcessError):
                auto_deploy.deploy(self.server)
            compose.assert_not_called()
        self.assertEqual((self.server / "version").read_text(), "server customization")

    def test_failed_container_restores_old_image_without_recording_success(self):
        with self.environment() as (_, compose, healthy):
            compose.side_effect = lambda repo, *args, **kwargs: (
                "old-container" if args[:1] == ("ps",) else ""
            )
            healthy.side_effect = [RuntimeError("unhealthy"), None]
            with self.assertRaisesRegex(RuntimeError, "unhealthy"):
                auto_deploy.deploy(self.server)
            self.assertEqual(self.state.read_text().strip(), self.old)
            self.assertEqual(healthy.call_count, 2)
            rollback_calls = [
                call for call in compose.call_args_list if call.kwargs.get("override")
            ]
            self.assertEqual(len(rollback_calls), 1)
            self.assertIn("never", rollback_calls[0].args)
        self.assertFalse((self.server / ".git/elearning-rollback.json").exists())

    def test_missing_health_or_build_failure_is_retried(self):
        with self.environment() as (_, compose, healthy):
            compose.side_effect = subprocess.CalledProcessError(1, "docker build")
            with self.assertRaises(subprocess.CalledProcessError):
                auto_deploy.deploy(self.server)
            self.assertEqual(self.state.read_text().strip(), self.old)
            healthy.assert_not_called()

    def test_ci_uses_latest_matching_push_run(self):
        commit = "a" * 40
        good = {
            "id": 1,
            "head_sha": commit,
            "head_branch": "main",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
        }
        newer_failure = {**good, "id": 2, "conclusion": "failure"}
        wrong_branch = {**good, "id": 3, "head_branch": "feature"}
        for runs, expected in [
            ([good], True),
            ([good, newer_failure], False),
            ([wrong_branch], False),
            ([], False),
        ]:
            with self.subTest(runs=runs):
                with patch.object(
                    auto_deploy,
                    "urlopen",
                    return_value=io.StringIO(json.dumps({"workflow_runs": runs})),
                ):
                    self.assertEqual(auto_deploy.ci_passed(commit), expected)
