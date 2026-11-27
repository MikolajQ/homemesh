"""Members telling each other what they know, over the SSH access they share.

`homemesh sync` logs in to every reachable member and runs
`homemesh sync-serve` there: each side sends its view of the group and takes
in the other's. This is how an introduced computer reaches members that were
switched off at the time of pairing: whoever talks to them next tells them.
"""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from . import paths, render, state
from .util import canonical

MARK = "HOMEMESH-SYNC "
MAX_INPUT = 4 * 1024 * 1024


def remote_command():
    return os.environ.get("HOMEMESH_REMOTE_COMMAND", "homemesh") + " sync-serve"


def ssh_command(alias, *extra):
    return ["ssh", "-F", str(paths.ssh_config()), "-o", "BatchMode=yes", *extra, alias]


def exchange(alias, payload, timeout=40):
    """Send our view to one peer; returns (their view, None) or (None, error)."""
    try:
        done = subprocess.run(ssh_command(alias) + [remote_command()],
                              input=canonical(payload) + b"\n", capture_output=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError as error:
        return None, str(error)
    if done.returncode != 0:
        lines = done.stderr.decode(errors="replace").strip().splitlines()
        return None, lines[-1] if lines else f"ssh exit {done.returncode}"
    # Ignore whatever a login script printed before our line.
    for line in reversed(done.stdout.decode(errors="replace").splitlines()):
        if line.startswith(MARK):
            try:
                return json.loads(line[len(MARK):]), None
            except ValueError:
                break
    return None, "no answer"


def _take_in(pid, answer):
    """Merge a peer's answer. Returns True if the group changed."""
    if not isinstance(answer, dict):
        return False
    their_card = answer.get("from")
    if not isinstance(their_card, dict) or their_card.get("id") != pid:
        return False  # not who we meant to talk to
    cards = [their_card] + [c for c in answer.get("peers", []) if isinstance(c, dict)]
    removed = answer.get("removed") if isinstance(answer.get("removed"), dict) else {}
    with state.transaction() as st:
        if state.merge(st, cards, removed, via=pid):
            render.apply(st)
            return True
    return False


def sync_all(only=None, rounds=2):
    """Sync with all peers (or the ids in `only`). Returns {peer id: error or None}."""
    report = {}
    done = set()
    for _round in range(rounds):
        with state.transaction() as st:
            payload = state.view(st)
            names = render.aliases(st)
            todo = [(pid, names[pid]) for pid in st["peers"]
                    if pid not in done and (only is None or pid in only or _round > 0)]
        if not todo:
            break
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda item: (item[0], exchange(item[1], payload)), todo))
        changed = False
        for pid, (answer, error) in results:
            done.add(pid)
            report[pid] = error
            if answer is not None:
                changed |= _take_in(pid, answer)
        if not changed:
            break  # nobody new to tell
    return report


def serve(stdin=None, stdout=None):
    """The far end of `exchange`, run by SSH on the peer."""
    # Not as default values: those are taken at import, when there may be no
    # standard input at all.
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout
    data = json.loads(stdin.read(MAX_INPUT) or b"{}")
    sender = data.get("from") if isinstance(data, dict) else None
    with state.transaction() as st:
        if isinstance(sender, dict):
            cards = [sender] + [c for c in data.get("peers", []) if isinstance(c, dict)]
            removed = data.get("removed") if isinstance(data.get("removed"), dict) else {}
            if state.merge(st, cards, removed, via=str(sender.get("id"))):
                render.apply(st)
        answer = state.view(st)
    stdout.write(MARK + json.dumps(answer, ensure_ascii=False) + "\n")
    stdout.flush()
    return 0
