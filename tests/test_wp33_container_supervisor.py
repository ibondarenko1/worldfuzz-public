"""Fault injection for cleanup only; these mocks are not container acceptance."""
import importlib.util
import json
from pathlib import Path
import signal
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "release_acceptance", Path(__file__).parents[1] / "evaluation" / "wp33_release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class ContainerCleanupTests(unittest.TestCase):
    def test_interruption_reaps_cli_and_cleans_only_owned_containers(self):
        for interrupted_stage in ("initializer", "server_start"):
            with self.subTest(stage=interrupted_stage), tempfile.TemporaryDirectory(prefix="wp33-rc2-") as temp:
                commands, handlers = [], {}
                alive = {"initializer": False, "server": False}

                def register(sig, handler):
                    previous = handlers.get(sig, signal.SIG_DFL)
                    handlers[sig] = handler
                    return previous

                class Child:
                    def __init__(self, command, **kwargs):
                        commands.append(command)
                        self.command = command
                        self.pid, self.returncode = 1000 + len(commands), None

                    def communicate(self, **kwargs):
                        cmd = self.command
                        output = b""
                        if cmd[:2] == ["docker", "run"]:
                            alive["initializer"] = True
                            if interrupted_stage == "initializer": handlers[signal.SIGTERM](signal.SIGTERM, None)
                            alive["initializer"] = False  # --rm
                        elif "up" in cmd:
                            alive["server"] = True
                            handlers[signal.SIGTERM](signal.SIGTERM, None)
                        elif "down" in cmd:
                            alive["server"] = False
                        elif cmd[:2] == ["docker", "ps"]:
                            self_test.assertIn("label=worldfuzz.wp33.context=" + str(Path(temp).resolve()), cmd)
                            output = b"owned-init-id\n" if alive["initializer"] else b""
                        elif cmd[:2] == ["docker", "stop"]:
                            self_test.assertEqual(cmd[-1], "owned-init-id")
                            alive["initializer"] = False
                        self.returncode = 0
                        return output, b""

                    def poll(self): return self.returncode
                    def terminate(self): self.returncode = -15
                    def kill(self): self.returncode = -9
                    def wait(self, **kwargs): return self.returncode

                self_test = self
                with patch.object(release.sys, "platform", "linux"), \
                        patch.object(signal, "SIGHUP", 1, create=True), \
                        patch.object(signal, "signal", side_effect=register), \
                        patch("socket.socket"), patch.object(release.subprocess, "Popen", Child):
                    with self.assertRaisesRegex(TimeoutError, "supervisor interrupted"):
                        release.container_host(SimpleNamespace(workspace=temp, wheel_sha256="unused", version="unused"))
                receipt = json.loads((Path(temp) / "supervisor.json").read_text())
                self.assertEqual(receipt["status"], "failed")
                self.assertFalse(any(alive.values()))
                self.assertEqual(receipt["cleanup"]["remaining_project_containers"], [])
                self.assertTrue(receipt["cleanup"]["all_cli_processes_reaped"])
                self.assertTrue(any("down" in cmd for cmd in commands))


if __name__ == "__main__":
    unittest.main()
