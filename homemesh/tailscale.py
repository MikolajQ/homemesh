"""Tailscale set up for access from away, without typing commands.

The root helper (`homemesh-helper tailscale`) installs Tailscale, runs it at
boot with whatever init system the computer has, and makes the person its
operator. Logging in then runs as the person: `tailscale up` prints the
address of a login page, which opens in their browser, and returns once they
have logged in. Tailscale itself keeps the computer connected from then on.
"""

import json
import os
import re
import shutil
import subprocess
import time

from . import vpn
from .i18n import _
from .util import username

PLACES = ("/usr/bin/tailscale", "/usr/local/bin/tailscale")
URL_RE = re.compile(r"https://[^\s\"']+")
ADDRESS_WAIT = 8


def cli():
    """The tailscale command, or None. HOMEMESH_TAILSCALE overrides it (tests)."""
    override = os.environ.get("HOMEMESH_TAILSCALE")
    if override is not None:
        return override or None
    return shutil.which("tailscale") or next((path for path in PLACES if os.access(path, os.X_OK)), None)


def backend_state():
    """"Running", "NeedsLogin", "Stopped", "NeedsMachineAuth"…; "missing" when
    Tailscale is not installed and "down" when its service does not answer."""
    tool = cli()
    if not tool:
        return "missing"
    try:
        done = subprocess.run([tool, "status", "--json"], capture_output=True, text=True, timeout=10)
        return json.loads(done.stdout).get("BackendState") or "down"
    except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
        return "down"


def _denied(output):
    text = output.lower()
    return "access denied" in text or "--operator" in text


def _first_line(output):
    for line in output.splitlines():
        if line.strip() and not URL_RE.search(line):
            return line.strip()
    return ""


class Setup:
    """One run of setting Tailscale up; run() blocks, so call it in a thread.

    run_helper(args) runs the root helper and returns a system.HelperResult.
    report(stage, value) hears how it goes: "admin" while the root helper
    runs (it may ask for the password), "connect", and "login" with the
    address of a page to open in the browser.
    """

    def __init__(self, run_helper, report):
        self.run_helper = run_helper
        self.report = report
        self.helper_result = None
        self.addresses = []
        self.cancelled = False
        self.process = None

    def cancel(self):
        self.cancelled = True
        process = self.process
        if process and process.poll() is None:
            process.terminate()

    def run(self):
        """None once this computer is on Tailscale, otherwise what went wrong."""
        helped = False
        current = backend_state()
        if current in ("missing", "down"):
            error = self._helper()
            if error:
                return error
            helped = True
            current = backend_state()
        while current != "Running":
            if self.cancelled:
                return _("Cancelled.")
            code, output = self._up()
            if code == 0:
                break
            if self.cancelled:
                return _("Cancelled.")
            if _denied(output) and not helped:
                error = self._helper()  # this person is not Tailscale's operator yet
                if error:
                    return error
                helped = True
                continue
            return _first_line(output) or _("Tailscale did not connect.")
        self.addresses = self._wait_for_address()
        return None

    def _helper(self):
        self.report("admin", None)
        result = self.run_helper(["tailscale"])
        self.helper_result = result
        if result.ok:
            return None
        return result.error or _("Not everything worked")

    def _up(self):
        tool = cli()
        if not tool:
            return 1, _("Tailscale is not installed.")
        self.report("connect", None)
        # `tailscale up` keeps the operator setting only if USER names them.
        env = dict(os.environ, USER=username())
        lines, seen = [], set()
        try:
            with subprocess.Popen([tool, "up"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, env=env) as process:
                self.process = process
                for line in process.stdout:
                    lines.append(line.rstrip())
                    for url in URL_RE.findall(line):
                        if url not in seen:
                            seen.add(url)
                            self.report("login", url)
        except OSError as error:
            return 1, str(error)
        return process.returncode, "\n".join(lines)

    def _wait_for_address(self):
        """Tailscale's interface gets its address a moment after logging in."""
        deadline = time.monotonic() + ADDRESS_WAIT
        while True:
            found = vpn.addresses()
            if found or time.monotonic() > deadline or self.cancelled:
                return found
            time.sleep(0.5)


def approval_page(url):
    """Is this the admin page where a new computer must be approved (not a login)?"""
    return "/admin" in url
