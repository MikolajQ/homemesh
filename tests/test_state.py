import os
import unittest

from support import HomeTestCase, card

from homemesh import state


class CardTest(HomeTestCase):
    def test_own_card_is_valid_and_stable(self):
        with state.transaction() as st:
            first = state.own_card(st)
        with state.transaction() as st:
            second = state.own_card(st)
        self.assertEqual(state.validate_card(first)["name"], "alfa")
        self.assertEqual(first["updated"], second["updated"])  # nothing changed, no bump
        with state.transaction() as st:
            third = state.own_card(st, bump=True)
        self.assertGreater(third["updated"], second["updated"])

    def test_a_vpn_that_is_down_stays_on_the_card(self):
        def own_card(address):
            os.environ["HOMEMESH_TEST_VPN"] = address
            with state.transaction() as st:
                return state.own_card(st)
        self.assertNotIn("vpn", own_card(""))
        self.assertNotIn("vpn", state.load()["self"])
        up = own_card("127.0.0.1")
        self.assertEqual(state.validate_card(up)["vpn"], [{"addr": "127.0.0.1", "kind": "VPN"}])
        self.assertEqual(own_card(""), up)  # not even a newer `updated`
        self.assertEqual(own_card("127.0.0.1"), up)
        other = own_card("127.0.0.2")
        self.assertEqual([item["addr"] for item in other["vpn"]], ["127.0.0.2"])
        self.assertGreater(other["updated"], up["updated"])

    def test_state_survives_reload(self):
        with state.transaction() as st:
            pid = st["id"]
        self.assertEqual(state.load()["id"], pid)

    def test_validate_refuses_anything_odd(self):
        good = card("a", "beta", 10)
        state.validate_card(good)
        for field, value in (("name", "Beta"), ("name", "be ta"), ("name", "beta\nHost *"),
                             ("user", "ania\n"), ("home", "relative"), ("home", '/home/"x'),
                             ("userkey", "ssh-rsa AAAA"), ("port", "22"), ("port", True),
                             ("updated", -1), ("id", "x" * 32)):
            bad = dict(good, **{field: value})
            with self.assertRaises(ValueError, msg=f"{field}={value!r}"):
                state.validate_card(bad)
        with self.assertRaises(ValueError):
            state.validate_card(dict(good, address="1.2.3.4\nHost *"))


class MergeTest(HomeTestCase):
    def test_newer_card_wins_and_local_fields_stay(self):
        st = state.new_state()
        self.assertTrue(state.merge(st, [card("b", "beta", 10)], {}, via="direct"))
        pid = "b" * 32
        st["peers"][pid]["automount"] = True
        self.assertFalse(state.merge(st, [card("b", "old-name", 5)], {}, via="x"))
        self.assertTrue(state.merge(st, [card("b", "beta2", 20)], {}, via="c" * 32))
        peer = st["peers"][pid]
        self.assertEqual(peer["name"], "beta2")
        self.assertTrue(peer["automount"])
        self.assertEqual(peer["via"], "direct")  # who introduced it does not change

    def test_own_card_is_never_a_peer(self):
        st = state.new_state()
        mine = dict(card("b", "beta", 10), id=st["id"])
        self.assertFalse(state.merge(st, [mine], {st["id"]: 99}, via="x"))
        self.assertEqual(st["peers"], {})
        self.assertEqual(st["removed"], {})

    def test_tombstone_removes_and_blocks_older_cards(self):
        st = state.new_state()
        state.merge(st, [card("b", "beta", 10)], {}, via="direct")
        state.merge(st, [], {"b" * 32: 15}, via="x")
        self.assertNotIn("b" * 32, st["peers"])
        self.assertFalse(state.merge(st, [card("b", "beta", 12)], {}, via="x"))
        self.assertTrue(state.merge(st, [card("b", "beta", 16)], {}, via="x"))

    def test_pairing_again_revives_a_removed_computer(self):
        st = state.new_state()
        state.merge(st, [card("b", "beta", 10)], {}, via="direct")
        state.remove_peer(st, "b" * 32)
        self.assertNotIn("b" * 32, st["peers"])
        # Pairing bumps the card on the other side; here we pass an old one on purpose:
        # the direct pairing still wins locally.
        state.pairing_apply(st, card("b", "beta", 11), [card("c", "gamma", 3)])
        self.assertEqual(st["peers"]["b" * 32]["via"], "direct")
        self.assertEqual(st["peers"]["c" * 32]["via"], "b" * 32)

    def test_removal_time_beats_a_card_from_the_future(self):
        st = state.new_state()
        far = 4_000_000_000  # a peer whose clock is way ahead
        state.merge(st, [card("b", "beta", far)], {}, via="direct")
        state.remove_peer(st, "b" * 32)
        self.assertGreater(st["removed"]["b" * 32], far)

    def test_bad_cards_are_skipped(self):
        st = state.new_state()
        self.assertFalse(state.merge(st, [{"id": "z"}, "nonsense", dict(card("b", "x y", 1))], {}, "d"))


if __name__ == "__main__":
    unittest.main()
