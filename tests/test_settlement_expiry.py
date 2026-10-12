import json
import unittest

from _bootstrap import (
    M, CLOCK, BUYER, SELLER, STRANGER, HOUR, DAY, MIN, PRICE, BOND, gl,
    make_contract, set_caller, create, active_deed, deed_of, world_for, observe, rule, honest_handover,
    seller_never_acts, control_without_switch, close_window, tamper, pending,
)


def settle(c, deed_id):
    close_window(c, deed_id)
    set_caller(STRANGER)
    c.close_uncontested(deed_id)
    return deed_of(c, deed_id)


class TestSettlement(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_complete_pays_seller_price_plus_bond(self):
        deed_id, _ = honest_handover(self.c)
        self.assertEqual(settle(self.c, deed_id)["final_outcome"], "HANDOVER_COMPLETE")
        self.assertEqual((pending(self.c, SELLER), pending(self.c, BUYER)), (PRICE + BOND, 0))

    def test_failed_pays_buyer_price_plus_bond(self):
        deed_id, _ = seller_never_acts(self.c)
        settle(self.c, deed_id)
        self.assertEqual((pending(self.c, BUYER), pending(self.c, SELLER)), (PRICE + BOND, 0))

    def test_partial_without_handover_refunds_buyer_plus_penalty(self):
        deed_id, _ = control_without_switch(self.c)
        settle(self.c, deed_id)
        self.assertEqual(pending(self.c, BUYER), PRICE + BOND // 2)
        self.assertEqual(pending(self.c, SELLER), BOND - BOND // 2)

    def test_partial_with_late_handover_pays_seller_less_penalty(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        CLOCK.advance(7 * HOUR)
        world.switch_delegation()
        observe(self.c, deed_id, world)
        CLOCK.advance(HOUR)
        rule(self.c, deed_id, world)
        settle(self.c, deed_id)
        self.assertEqual(pending(self.c, SELLER), PRICE + BOND - BOND // 2)
        self.assertEqual(pending(self.c, BUYER), BOND // 2)

    def test_undetermined_refunds_price_and_bond(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        observe(self.c, deed_id, world, caller=BUYER)
        CLOCK.advance(7 * HOUR)
        world.txt_status_override = 2
        rule(self.c, deed_id, world)
        settle(self.c, deed_id)
        self.assertEqual((pending(self.c, BUYER), pending(self.c, SELLER)), (PRICE, BOND))

    def test_odd_amounts_are_conserved(self):
        deed_id, _ = control_without_switch(self.c, price=1001, bond=333)
        settle(self.c, deed_id)
        self.assertEqual(pending(self.c, BUYER) + pending(self.c, SELLER), 1334)

    def test_every_outcome_reconciles_with_the_balance(self):
        for flow in (honest_handover, seller_never_acts, control_without_switch):
            c = make_contract()
            deed_id, _ = flow(c)
            settle(c, deed_id)
            self.assertEqual(pending(c, BUYER) + pending(c, SELLER), int(c.balance), flow.__name__)

    def test_close_before_window_and_twice_rejected(self):
        deed_id, _ = honest_handover(self.c)
        set_caller(STRANGER)
        with self.assertRaises(Exception):
            self.c.close_uncontested(deed_id)
        settle(self.c, deed_id)
        with self.assertRaises(Exception):
            self.c.close_uncontested(deed_id)
        self.c._settle(deed_of(self.c, deed_id))
        self.assertEqual(pending(self.c, SELLER), PRICE + BOND)

    def test_withdraw_transfers_and_zeroes(self):
        deed_id, _ = honest_handover(self.c)
        settle(self.c, deed_id)
        set_caller(SELLER)
        self.c.withdraw()
        self.assertEqual(gl.evm.transfers[-1]["to"], SELLER)
        self.assertEqual(int(gl.evm.transfers[-1]["value"]), PRICE + BOND)
        self.assertEqual(pending(self.c, SELLER), 0)
        with self.assertRaises(Exception):
            self.c.withdraw()

    def test_withdraw_with_nothing_rejected(self):
        set_caller(STRANGER)
        with self.assertRaises(Exception):
            self.c.withdraw()


class TestExpiry(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_expire_unobserved_refunds_both(self):
        deed_id = active_deed(self.c)
        CLOCK.advance(6 * HOUR + DAY)
        set_caller(STRANGER)
        with self.assertRaises(Exception):
            self.c.expire_unobserved(deed_id)
        CLOCK.advance(2 * DAY + 1)
        self.c.expire_unobserved(deed_id)
        self.assertEqual(deed_of(self.c, deed_id)["final_outcome"], "UNDETERMINED")
        self.assertEqual((pending(self.c, BUYER), pending(self.c, SELLER)), (PRICE, BOND))

    def test_expire_unobserved_rejected_once_observing(self):
        deed_id = active_deed(self.c)
        observe(self.c, deed_id, world_for(self.c, deed_id))
        CLOCK.advance(30 * DAY)
        with self.assertRaises(Exception):
            self.c.expire_unobserved(deed_id)

    def test_expire_unruled_after_timeout(self):
        deed_id = active_deed(self.c)
        observe(self.c, deed_id, world_for(self.c, deed_id))
        CLOCK.advance(6 * HOUR + 3 * DAY + 6 * DAY)
        set_caller(STRANGER)
        with self.assertRaises(Exception):
            self.c.expire_unruled(deed_id)
        CLOCK.advance(DAY + 1)
        self.c.expire_unruled(deed_id)
        self.assertEqual((pending(self.c, BUYER), pending(self.c, SELLER)), (PRICE, BOND))

    def test_expire_unruled_rejected_after_ruling(self):
        deed_id, _ = honest_handover(self.c)
        CLOCK.advance(30 * DAY)
        with self.assertRaises(Exception):
            self.c.expire_unruled(deed_id)

    def test_every_non_final_status_has_an_exit(self):
        exits = {"proposed": "cancel_deed", "active": "expire_unobserved", "observing": "expire_unruled",
                 "ruled": "close_uncontested", "contested": "expire_stuck_appeal", "paneled": "expire_stuck_appeal"}
        for status, method in exits.items():
            self.assertTrue(callable(getattr(self.c, method)), f"{status} -> {method}")


class TestEvidenceIntegrity(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        self.deed_id, _ = honest_handover(self.c)

    def test_intact_and_preview(self):
        info = json.loads(self.c.check_evidence(self.deed_id))
        self.assertTrue(info["intact"])
        self.assertEqual(info["default"], "HANDOVER_COMPLETE")
        self.assertEqual(info["allowed"], ["HANDOVER_COMPLETE", "PARTIAL_HANDOVER"])

    def test_tampering_is_detected(self):
        mutations = [
            lambda d: d["observations"][0]["core"]["txt"].update(token=False),
            lambda d: d["observations"][-1]["core"]["rdap"].update(ns=["evil.example.net"]),
            lambda d: d["observations"].pop(0),
            lambda d: d.update(evidence_root="00" * 32),
            lambda d: d["observations"].reverse(),
        ]
        for mutate in mutations:
            c = make_contract()
            deed_id, _ = honest_handover(c)
            tamper(c, deed_id, mutate)
            self.assertFalse(json.loads(c.check_evidence(deed_id))["intact"])

    def test_check_evidence_needs_an_observation(self):
        with self.assertRaises(Exception):
            self.c.check_evidence(active_deed(self.c))

    def test_intact_handles_missing_parts(self):
        self.assertFalse(M._evidence_intact({}))
        self.assertFalse(M._evidence_intact({"observations": []}))
        self.assertFalse(M._evidence_intact({"observations": [{}]}))


if __name__ == "__main__":
    unittest.main()
