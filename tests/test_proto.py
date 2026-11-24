import socket
import threading
import time
import unittest

from support import card

from homemesh import proto
from homemesh.proto import Channel, PairingError


def offer(char, name):
    return {"card": card(char, name, 10), "members": [card("m", "member", 5)]}


def run_pair(asker_offer, answerer_offer, tamper=None):
    """Both ends over a socket pair; `tamper` may rewrite messages in transit."""
    a, b = socket.socketpair()
    results = {}
    sockets = [a, b]
    if tamper:
        a, left = socket.socketpair()
        right, b = socket.socketpair()
        sockets += [a, left, right, b]

        def relay(src, dst, direction):
            chan_in, buffer = src, b""
            while True:
                try:
                    data = chan_in.recv(65536)
                except OSError:
                    break
                if not data:
                    break
                buffer += data
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    dst.sendall(tamper(direction, line) + b"\n")
            try:
                dst.shutdown(socket.SHUT_WR)
            except OSError:
                pass
        threading.Thread(target=relay, args=(left, right, "up"), daemon=True).start()
        threading.Thread(target=relay, args=(right, left, "down"), daemon=True).start()

    def answer():
        try:
            chan = Channel(b)
            results["answerer"] = proto.respond(chan, proto.read_hello(chan), answerer_offer)
        except PairingError as error:
            results["answerer"] = error
    thread = threading.Thread(target=answer)
    thread.start()
    try:
        results["asker"] = proto.initiate(Channel(a), asker_offer)
    except PairingError as error:
        results["asker"] = error
    a.close()
    thread.join(5)
    for sock in sockets:
        sock.close()
    return results


class PairingTest(unittest.TestCase):
    def test_both_ends_see_the_same_code_and_each_other(self):
        mine, theirs = offer("a", "alfa"), offer("b", "beta")
        results = run_pair(mine, theirs)
        (got_theirs, code_a), (got_mine, code_b) = results["asker"], results["answerer"]
        self.assertEqual(code_a, code_b)
        self.assertRegex(code_a, r"^\d{3} \d{3}$")
        self.assertEqual(got_theirs, theirs)
        self.assertEqual(got_mine, mine)

    def test_codes_differ_between_attempts(self):
        codes = {run_pair(offer("a", "alfa"), offer("b", "beta"))["asker"][1] for _ in range(5)}
        self.assertGreater(len(codes), 1)

    def test_swapped_answer_is_caught_by_the_commitment(self):
        def swap(direction, line):
            if direction == "down" and b'"t":"hello"' in line:
                return line.replace(b'"name":"beta"', b'"name":"evil"')
            return line
        results = run_pair(offer("a", "alfa"), offer("b", "beta"), swap)
        self.assertIsInstance(results["asker"], PairingError)

    def test_swapped_question_gives_different_codes(self):
        def swap(direction, line):
            if direction == "up" and b'"t":"hello"' in line:
                return line.replace(b'"name":"alfa"', b'"name":"evil"')
            return line
        results = run_pair(offer("a", "alfa"), offer("b", "beta"), swap)
        self.assertNotEqual(results["asker"][1], results["answerer"][1])

    def test_refusal_reaches_the_asker(self):
        a, b = socket.socketpair()

        def answer():
            chan = Channel(b)
            chan.recv()
            proto.refuse(chan, "not-ready")
        threading.Thread(target=answer).start()
        with self.assertRaisesRegex(PairingError, "Sharing is off"):
            proto.initiate(Channel(a), offer("a", "alfa"))
        a.close()
        b.close()


class ConfirmationTest(unittest.TestCase):
    def pair(self, decide_a, decide_b, delay_b=0.0):
        a, b = socket.socketpair()
        conf_a, conf_b = proto.Confirmation(Channel(a)), proto.Confirmation(Channel(b))
        outcome = {}

        def side(conf, decision, delay, key):
            time.sleep(delay)
            conf.decide(decision)
            try:
                remote = conf.wait_remote(5)
                outcome[key] = remote and conf.wait_local(5)
            except PairingError:
                outcome[key] = False
        threads = [threading.Thread(target=side, args=(conf_a, decide_a, 0, "a")),
                   threading.Thread(target=side, args=(conf_b, decide_b, delay_b, "b"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        a.close()
        b.close()
        return outcome

    def test_both_yes(self):
        self.assertEqual(self.pair(True, True, 0.2), {"a": True, "b": True})

    def test_one_no(self):
        self.assertEqual(self.pair(True, False, 0.2), {"a": False, "b": False})


if __name__ == "__main__":
    unittest.main()
