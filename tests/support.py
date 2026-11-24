"""Shared test setup: a throwaway home directory per test."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# Messages in English whatever the system language (also for the child processes).
os.environ["LANGUAGE"] = "en"

KEY_PREFIX = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI"


def fake_key(char):
    return KEY_PREFIX + char * 43


def make_host_key(directory):
    key = Path(directory) / "host_ed25519"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    return key


class HomeTestCase(unittest.TestCase):
    """Runs with HOMEMESH_HOME in a temporary directory; never touches the real home."""

    name = "alfa"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="homemesh-test-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.saved_env = dict(os.environ)
        host_key = make_host_key(self.tmp)
        os.environ.update({"HOMEMESH_HOME": str(self.home), "HOMEMESH_NAME": self.name,
                           "HOMEMESH_HOSTKEY": f"{host_key}.pub"})
        os.environ.pop("XDG_CONFIG_HOME", None)
        os.environ.pop("XDG_DATA_HOME", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved_env)
        shutil.rmtree(self.tmp, ignore_errors=True)


def card(pid_char, name, updated, key_char=None, user="ania"):
    key = fake_key(key_char or pid_char)
    return {"id": pid_char * 32, "name": name, "user": user, "home": f"/home/{user}",
            "userkey": key, "hostkey": key, "port": 22, "updated": updated, "version": "0.1.0"}
