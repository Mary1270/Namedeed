"""Lifecycle of the synthetic-evidence test variant. Only meaningful when
ND_CONTRACT points at contract_evidence_deploy.py; skipped otherwise."""
import unittest

from _bootstrap import (
    M, CLOCK, BUYER, SELLER, STRANGER, NS, SELLER_NS, HOUR, MIN, PRICE, BOND,
    make_contract, set_caller, active_deed, deed_of, observe, rule, close_window, pending, World,
)


@unittest.skipUnless(hasattr(M.NameDeed, "set_fixture"), "synthetic-evidence variant only")
class TestSyntheticEvidenceVariant(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        self.world = World()

    def fixture(self, deed_id, ns, token, proof):
        set_caller(STRANGER)
        self.c.set_fixture(deed_of(self.c, deed_id)["domain"], ",".join(ns), token, proof)

    def test_it_declares_that_its_evidence_is_synthetic(self):
        self.assertIn("SYNTHETIC", self.c.evidence_source())

    def test_a_complete_handover_runs_through_the_normal_logic(self):
        deed_id = active_deed(self.c)
        self.fixture(deed_id, SELLER_NS, True, False)
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, self.world)
        self.fixture(deed_id, NS, False, True)
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, self.world)
        CLOCK.advance(HOUR + MIN)
        rule(self.c, deed_id, self.world)
        self.assertEqual(deed_of(self.c, deed_id)["ruling"], "HANDOVER_COMPLETE")
        self.assertEqual(self.world.calls, [])

    def test_a_partial_handover_is_ruled_and_paid(self):
        deed_id = active_deed(self.c)
        self.fixture(deed_id, SELLER_NS, True, False)
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, self.world)
        CLOCK.advance(HOUR + MIN)
        observe(self.c, deed_id, self.world)
        CLOCK.advance(7 * HOUR)
        rule(self.c, deed_id, self.world)
        deed = deed_of(self.c, deed_id)
        self.assertEqual(deed["ruling"], "PARTIAL_HANDOVER")
        close_window(self.c, deed_id)
        set_caller(STRANGER)
        self.c.close_uncontested(deed_id)
        self.assertEqual(pending(self.c, BUYER), PRICE + BOND // 2)
        self.assertEqual(pending(self.c, SELLER), BOND - BOND // 2)
