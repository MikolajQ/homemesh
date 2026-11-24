import os
import signal
import socket
import subprocess
import sys
import time
import unittest

from support import ROOT, HomeTestCase, card

from homemesh import paths, presence, render, state, system, watch

LAN = system.Reach("lan", "192.168.0.5", True)


class SeenTest(unittest.TestCase):
    """When a computer counts as off, and when it is checked next."""

    def setUp(self):
        self.seen = watch.Seen(1000)

    def test_two_misses_before_it_is_off(self):
        self.seen.checked(LAN, 1000)
        self.assertTrue(self.seen.on)
        self.assertEqual(self.seen.next(), 1000 + watch.ASK)
        self.seen.checked(system.NOWHERE, 1060)
        self.assertTrue(self.seen.on)
        self.assertEqual(self.seen.next(), 1060 + watch.AGAIN)
        self.seen.checked(system.NOWHERE, 1068)
        self.assertIs(self.seen.on, False)
        self.assertEqual(self.seen.next(), 1068 + watch.LOOK)
        self.seen.checked(LAN, 1100)
        self.assertTrue(self.seen.on)

    def test_at_the_start_the_second_look_comes_sooner(self):
        self.seen.checked(system.NOWHERE, 1000)
        self.assertIsNone(self.seen.on)
        self.assertEqual(self.seen.next(), 1000 + watch.AGAIN / 4)
        self.seen.checked(system.NOWHERE, 1002)
        self.assertIs(self.seen.on, False)

    def test_one_lost_answer_is_forgiven(self):
        for now in (1000, 1120, 1240):
            self.seen.checked(LAN, now)
            self.seen.checked(system.NOWHERE, now + 60)
            self.assertTrue(self.seen.on)

    def test_ssh_connections_and_vpns_are_checked_less_often(self):
        for reach in (LAN._replace(answers=False), LAN._replace(how="vpn")):
            self.seen = watch.Seen(1000)
            self.seen.checked(reach, 1000)
            self.assertEqual(self.seen.next(), 1000 + watch.CONNECT)

    def test_being_asked_is_a_sign_of_life(self):
        self.seen.checked(LAN, 1000)
        self.seen.heard(LAN.address, 1030)
        self.assertGreater(self.seen.next(), 1030 + watch.ASK)  # the other one asks first

    def test_a_computer_that_is_off_is_checked_as_soon_as_it_asks(self):
        for now in (1000, 1008):
            self.seen.checked(system.NOWHERE, now)
        self.seen.heard("192.168.0.9", 1020)
        self.assertEqual((self.seen.next(), self.seen.hint), (1020, "192.168.0.9"))

    def test_leaving_is_followed_by_a_few_looks(self):
        self.seen.checked(LAN, 1000)
        self.seen.hurry(1010, watch.LEAVING)
        looks = []
        for _ in range(len(watch.LEAVING)):
            looks.append(self.seen.next())
            self.seen.checked(LAN, self.seen.next())
        self.assertEqual(looks, [1010 + wait for wait in watch.LEAVING])


class WatcherCase(HomeTestCase):
    """A watcher in a throwaway home, with made-up answers of the two computers it watches."""

    def setUp(self):
        super().setUp()
        os.environ["HOMEMESH_ADVERTISE_ADDRESS"] = "127.0.0.1"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.1", 0))
            os.environ["HOMEMESH_PORT"] = str(sock.getsockname()[1])
        with state.transaction() as st:
            state.merge(st, [card("b", "beta", 10), card("c", "gamma", 11)], {}, via="direct")
            render.apply(st)
        self.reach = {"beta": LAN, "gamma": system.NOWHERE}
        self.synced, self.sync_errors = [], []
        self.watcher = watch.Watcher(check=lambda peer, me, hint: self.reach[peer["name"]], sync_all=self.sync_all)
        self.addCleanup(self.close)
        self.assertTrue(self.watcher.refresh())

    def close(self):
        for sock in (self.watcher.wake_in, self.watcher.wake_out, *self.watcher.sockets.values()):
            sock.close()
        self.watcher.selector.close()

    def sync_all(self, only=None):
        self.synced.append(only)
        errors = self.sync_errors.pop(0) if self.sync_errors else {}
        return {pid: errors.get(pid) for pid in (only or ("b" * 32, "c" * 32))}

    def labels(self):
        return [line.split(" ", 1)[1] for line in paths.gtk_bookmarks().read_text().splitlines()]

    def settle(self, done, seconds=5):
        deadline = time.monotonic() + seconds
        while not done() and time.monotonic() < deadline:
            for seen in self.watcher.seen.values():  # no waiting in tests
                seen.due = min(seen.due, time.monotonic() + 0.02)
            self.watcher.step(0.05)
        self.assertTrue(done(), self.labels())


class WatcherTest(WatcherCase):
    def test_marks_follow_the_computers(self):
        self.settle(lambda: self.labels() == ["beta", "(off) gamma"])
        self.reach = {"beta": system.NOWHERE, "gamma": LAN}
        self.settle(lambda: self.labels() == ["(off) beta", "gamma"])
        self.watcher.leave()
        self.assertEqual(self.labels(), ["beta", "gamma"])

    def test_nothing_is_written_while_nothing_changes(self):
        self.settle(lambda: self.labels() == ["beta", "(off) gamma"])
        before = os.stat(paths.gtk_bookmarks()), os.stat(paths.state_file())
        for _ in range(5):
            self.settle(lambda: False if any(s.busy for s in self.watcher.seen.values()) else True)
            self.watcher.step(0.05)
        after = os.stat(paths.gtk_bookmarks()), os.stat(paths.state_file())
        self.assertEqual([(s.st_ino, s.st_mtime_ns) for s in before], [(s.st_ino, s.st_mtime_ns) for s in after])

    def test_stops_when_there_is_nothing_to_watch(self):
        with state.transaction() as st:
            st["login"] = False
        self.assertFalse(self.watcher.refresh())
        with state.transaction() as st:
            st["login"] = True
            for pid in list(st["peers"]):
                state.remove_peer(st, pid)
        self.assertFalse(self.watcher.refresh())
        paths.state_file().unlink()
        self.assertFalse(self.watcher.refresh())
        self.watcher.leave()
        self.assertFalse(paths.state_file().exists())  # nothing comes back after "homemesh reset"

    def test_answers_paired_computers_also_with_the_window_open(self):
        from homemesh.discovery import Discovery
        self.watcher.listen()
        window = Discovery(lambda: {"id": state.load()["id"], "name": "alfa", "user": "x", "port": 1, "ready": True},
                           targets=[("127.0.0.1", 9)])
        self.addCleanup(window.close)
        self.settle(lambda: all(seen.on is not None for seen in self.watcher.seen.values()))
        answers = []
        import threading
        for sender in ("b" * 32, "d" * 32):
            thread = threading.Thread(target=lambda s=sender: answers.append(
                presence.ask(s, self.watcher.st["id"], "127.0.0.1", tries=(0.4,))))
            thread.start()
            while thread.is_alive():
                self.settle(lambda: True)
                self.watcher.step(0.05)
        self.assertIn(answers[0]["ready"], (True, False))
        self.assertIsNone(answers[1])  # a stranger

    def test_one_watcher_and_a_new_build_replaces_the_old_one(self):
        env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}

        def spawn():
            return subprocess.Popen([sys.executable, str(ROOT / "bin" / "homemesh"), "watch"], env=env)

        def wait_for(process):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                holder = watch._holder()
                if holder and holder[0] == process.pid:
                    return
                time.sleep(0.05)
            self.fail("the watcher did not start")
        first = spawn()
        try:
            wait_for(first)
            self.assertEqual(spawn().wait(5), 0)
            self.assertIsNone(first.poll())
            paths.watch_lock().write_text(f"{first.pid} older-build\n")
            second = spawn()
            try:
                self.assertEqual(first.wait(5), 0)
                wait_for(second)
                watch.stop()
                self.assertEqual(second.wait(5), 0)
            finally:
                second.kill()
                second.wait()
        finally:
            first.kill()
            first.wait()

    def own_card(self, address):
        os.environ["HOMEMESH_TEST_VPN"] = address
        with state.transaction() as st:
            state.own_card(st)

    def test_the_group_is_told_about_another_vpn_address(self):
        self.own_card("127.0.0.1")
        self.watcher.told = None
        self.watcher.stamp = None
        self.watcher.refresh()
        waits, sleep = [], time.sleep
        self.addCleanup(setattr, watch.time, "sleep", sleep)
        watch.time.sleep = waits.append
        for address in ("127.0.0.1", ""):  # nothing new, and down for a moment
            os.environ["HOMEMESH_TEST_VPN"] = address
            self.watcher._tell()
        self.assertEqual(self.synced, [])
        os.environ["HOMEMESH_TEST_VPN"] = "127.0.0.2"
        self.sync_errors = [{"c" * 32: "timeout"}, {"c" * 32: "timeout"}]
        self.watcher._tell()
        self.assertEqual(self.synced, [None, {"c" * 32}, {"c" * 32}])  # again, but only the one missed
        self.assertEqual(waits, list(watch.NETWORK))
        self.watcher._tell()
        self.assertEqual(len(self.synced), 3)

    def test_the_group_is_told_even_if_another_process_saw_the_change_first(self):
        self.own_card("127.0.0.1")
        self.own_card("127.0.0.2")  # such as "homemesh sync-serve" for one of the others
        self.own_card("127.0.0.1")
        self.watcher._tell()
        self.assertEqual(self.synced, [None])

    def test_a_change_of_the_network_has_the_card_checked(self):
        self.own_card("127.0.0.1")
        self.watcher.settle = time.monotonic()
        self.watcher.step(0)
        self.settle(lambda: self.synced == [None])

    def test_nothing_is_told_after_reset(self):
        paths.state_file().unlink()
        self.watcher._tell()
        self.assertEqual(self.synced, [])
        self.assertFalse(paths.state_file().exists())

    def test_a_crashing_check_does_not_stop_the_rest(self):
        def check(peer, me, hint):
            if peer["name"] == "gamma":
                raise ValueError("boom")
            return LAN
        self.watcher.check = check
        stderr, sys.stderr = sys.stderr, open(os.devnull, "w")
        try:
            self.settle(lambda: self.labels() == ["beta", "(off) gamma"])
        finally:
            sys.stderr.close()
            sys.stderr = stderr

    def test_signal_ends_it(self):
        self.watcher.stop(signal.SIGTERM, None)
        self.assertTrue(self.watcher.stopping)


if __name__ == "__main__":
    unittest.main()
