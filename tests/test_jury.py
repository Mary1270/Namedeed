import datetime
import json
import unittest
from unittest.mock import patch

from _bootstrap import (
    M, CLOCK, BUYER, SELLER, STRANGER, JURORS, HOUR, DAY, MIN, PRICE, BOND, gl, parse,
    make_contract, set_caller, call_payable, deed_of, world_for, honest_handover, seller_never_acts,
    age_jurors, seat_panel, commit_hash, tamper, pending, active_deed,
)

APPEAL_BOND = PRICE * 2000 // 10000


class JuryBase(unittest.TestCase):
    flow = staticmethod(honest_handover)

    def setUp(self):
        self.c = make_contract()
        self.jurors = JURORS[:6]
        for i, addr in enumerate(self.jurors):
            set_caller(addr)
            call_payable(self.c, "register_juror", 1000 + i * 100)
        age_jurors(self.c)
        self.deed_id, self.world = self.flow(self.c)

    def contest(self, appellant=BUYER, bond=APPEAL_BOND):
        set_caller(appellant)
        call_payable(self.c, "contest", bond, self.deed_id)

    def panel(self, appellant=BUYER):
        self.contest(appellant)
        seat_panel(self.c, self.deed_id)
        return deed_of(self.c, self.deed_id)["jury"]["selected_jurors"]

    def vote(self, selected, votes):
        salts = {a: f"salt-{i}" for i, a in enumerate(selected)}
        for addr in selected:
            if votes.get(addr.lower()):
                set_caller(addr)
                self.c.commit_vote(self.deed_id, commit_hash(votes[addr.lower()], salts[addr], self.deed_id, addr))
        CLOCK.advance(2 * DAY + 1)
        for addr in selected:
            if votes.get(addr.lower()):
                set_caller(addr)
                self.c.reveal_vote(self.deed_id, votes[addr.lower()], salts[addr])
        CLOCK.advance(DAY + 1)

    def close(self, plausible=True):
        self.world.plausible = plausible
        self.world.llm_calls = 0
        set_caller(STRANGER)
        with self.world.active():
            self.c.close_panel(self.deed_id)
        return deed_of(self.c, self.deed_id)


class TestContestAndSelection(JuryBase):
    def test_panel_of_five_and_replayable(self):
        selected = self.panel()
        self.assertEqual(len({s.lower() for s in selected}), 5)
        self.assertTrue(json.loads(self.c.verify_jury_selection(self.deed_id))["match"])

    def test_selection_depends_on_the_beacon(self):
        self.panel()
        pool = deed_of(self.c, self.deed_id)["jury"]["pool"]
        picks = {tuple(M.NameDeed._select_from_pool(self.c, self.deed_id, f"b{i}", pool)[0]) for i in range(12)}
        self.assertGreater(len(picks), 1)

    def test_bond_is_twenty_percent_of_the_price(self):
        for bond in (1, APPEAL_BOND - 1, APPEAL_BOND + 1, BOND):
            with self.assertRaises(Exception):
                self.contest(bond=bond)
        self.contest()

    def test_only_parties_contest_within_the_window(self):
        with self.assertRaises(Exception):
            self.contest(appellant=STRANGER)
        CLOCK.advance(2 * DAY + 1)
        with self.assertRaises(Exception):
            self.contest()

    def test_contest_needs_enough_jurors(self):
        c2 = make_contract()
        for addr in JURORS[:2]:
            set_caller(addr)
            call_payable(c2, "register_juror", 500)
        age_jurors(c2)
        deed_id, _ = honest_handover(c2)
        set_caller(BUYER)
        with self.assertRaises(Exception):
            call_payable(c2, "contest", APPEAL_BOND, deed_id)

    def test_parties_are_excluded(self):
        c2 = make_contract()
        for addr in JURORS[:6] + [BUYER, SELLER]:
            set_caller(addr)
            call_payable(c2, "register_juror", 5000)
        age_jurors(c2)
        deed_id, _ = honest_handover(c2)
        set_caller(BUYER)
        call_payable(c2, "contest", APPEAL_BOND, deed_id)
        pool = {a.lower() for a, _ in deed_of(c2, deed_id)["appeal"]["eligible_pool"]}
        self.assertNotIn(BUYER, pool)
        self.assertNotIn(SELLER, pool)

    def test_jurors_must_be_registered_a_week_before_acceptance(self):
        c2 = make_contract()
        for addr in JURORS[:5]:
            set_caller(addr)
            call_payable(c2, "register_juror", 500)
        age_jurors(c2)
        accepted = CLOCK.t
        for addr, age_days in ((JURORS[6], 6), (JURORS[7], 8)):
            set_caller(addr)
            call_payable(c2, "register_juror", 500)
            rec = json.loads(c2.jurors[addr.lower()])
            stamp = (accepted - datetime.timedelta(days=age_days)).isoformat()
            rec["registered_at"] = rec["stake_updated_at"] = stamp
            c2.jurors[addr.lower()] = json.dumps(rec, sort_keys=True)
        deed_id = active_deed(c2)
        pool = {a for a, _ in c2._eligible_pool(deed_of(c2, deed_id))}
        self.assertNotIn(JURORS[6].lower(), pool)
        self.assertIn(JURORS[7].lower(), pool)

    def test_stake_topup_after_acceptance_makes_juror_ineligible(self):
        set_caller(self.jurors[0])
        call_payable(self.c, "register_juror", 10)
        rec = json.loads(self.c.jurors[self.jurors[0].lower()])
        rec["registered_at"] = "2000-01-01T00:00:00+00:00"
        self.c.jurors[self.jurors[0].lower()] = json.dumps(rec, sort_keys=True)
        self.assertEqual(self.c.count_eligible_jurors(self.deed_id), 5)

    def test_seat_panel_rules(self):
        with self.assertRaises(Exception):
            self.c.seat_panel(self.deed_id)
        self.contest()
        with patch.object(gl.nondet.web, "render", return_value=json.dumps({"round": 1, "randomness": "x"})):
            with self.assertRaises(Exception):
                self.c.seat_panel(self.deed_id)
        deed = deed_of(self.c, self.deed_id)
        CLOCK.set(parse(deed["appeal_deadline"]) + datetime.timedelta(seconds=120))
        with patch.object(gl.nondet.web, "render", side_effect=RuntimeError("down")):
            with self.assertRaises(Exception):
                self.c.seat_panel(self.deed_id)
        bad = json.dumps({"round": deed["appeal"]["draw_round"] + 3, "randomness": "x"})
        with patch.object(gl.nondet.web, "render", return_value=bad):
            with self.assertRaises(Exception):
                self.c.seat_panel(self.deed_id)
        self.assertEqual(deed_of(self.c, self.deed_id)["status"], "contested")

    def test_unstake_locks(self):
        self.contest()
        set_caller(self.jurors[0])
        with self.assertRaises(Exception):
            self.c.unstake_juror("1")
        seat_panel(self.c, self.deed_id)
        selected = deed_of(self.c, self.deed_id)["jury"]["selected_jurors"]
        set_caller(selected[0])
        with self.assertRaises(Exception):
            self.c.unstake_juror("1")

    def test_unstake_bounds_when_idle(self):
        set_caller(self.jurors[0])
        for bad in ("999999", "0", "-1"):
            with self.assertRaises(Exception):
                self.c.unstake_juror(bad)
        self.c.unstake_juror("100")
        self.assertEqual(pending(self.c, self.jurors[0]), 100)


class TestCommitReveal(JuryBase):
    def setUp(self):
        super().setUp()
        self.selected = self.panel()

    def test_only_selected_jurors_commit_valid_hashes(self):
        outsider = [a for a in JURORS if a.lower() not in [s.lower() for s in self.selected]][0]
        set_caller(outsider)
        with self.assertRaises(Exception):
            self.c.commit_vote(self.deed_id, commit_hash("PARTIAL_HANDOVER", "s", self.deed_id, outsider))
        set_caller(self.selected[0])
        for bad in ("abc", "z" * 64, "", "0" * 63):
            with self.assertRaises(Exception):
                self.c.commit_vote(self.deed_id, bad)

    def test_commit_once_and_within_window(self):
        juror = self.selected[0]
        set_caller(juror)
        self.c.commit_vote(self.deed_id, commit_hash("PARTIAL_HANDOVER", "a", self.deed_id, juror))
        with self.assertRaises(Exception):
            self.c.commit_vote(self.deed_id, commit_hash("HANDOVER_COMPLETE", "b", self.deed_id, juror))
        CLOCK.advance(2 * DAY + 1)
        set_caller(self.selected[1])
        with self.assertRaises(Exception):
            self.c.commit_vote(self.deed_id, commit_hash("PARTIAL_HANDOVER", "a", self.deed_id, self.selected[1]))

    def test_reveal_rules(self):
        juror = self.selected[0]
        set_caller(juror)
        self.c.commit_vote(self.deed_id, commit_hash("PARTIAL_HANDOVER", "a", self.deed_id, juror))
        with self.assertRaises(Exception):
            self.c.reveal_vote(self.deed_id, "PARTIAL_HANDOVER", "a")
        CLOCK.advance(2 * DAY + 1)
        for vote, salt in (("PARTIAL_HANDOVER", "wrong"), ("HANDOVER_COMPLETE", "a"), ("MAYBE", "a")):
            with self.assertRaises(Exception):
                self.c.reveal_vote(self.deed_id, vote, salt)
        self.c.reveal_vote(self.deed_id, "PARTIAL_HANDOVER", "a")
        with self.assertRaises(Exception):
            self.c.reveal_vote(self.deed_id, "PARTIAL_HANDOVER", "a")

    def test_copied_commit_cannot_be_revealed(self):
        victim, copier = self.selected[0], self.selected[1]
        stolen = commit_hash("PARTIAL_HANDOVER", "secret", self.deed_id, victim)
        for addr in (victim, copier):
            set_caller(addr)
            self.c.commit_vote(self.deed_id, stolen)
        CLOCK.advance(2 * DAY + 1)
        with self.assertRaises(Exception):
            self.c.reveal_vote(self.deed_id, "PARTIAL_HANDOVER", "secret")

    def test_commit_from_another_deed_is_useless(self):
        juror = self.selected[0]
        set_caller(juror)
        self.c.commit_vote(self.deed_id, commit_hash("PARTIAL_HANDOVER", "a", "deed_7", juror))
        CLOCK.advance(2 * DAY + 1)
        with self.assertRaises(Exception):
            self.c.reveal_vote(self.deed_id, "PARTIAL_HANDOVER", "a")

    def test_overlong_salt_and_late_reveal_rejected(self):
        juror = self.selected[0]
        set_caller(juror)
        self.c.commit_vote(self.deed_id, commit_hash("PARTIAL_HANDOVER", "a" * 300, self.deed_id, juror))
        CLOCK.advance(2 * DAY + 1)
        with self.assertRaises(Exception):
            self.c.reveal_vote(self.deed_id, "PARTIAL_HANDOVER", "a" * 300)
        CLOCK.advance(DAY)
        with self.assertRaises(Exception):
            self.c.reveal_vote(self.deed_id, "PARTIAL_HANDOVER", "a")


class TestClosePanelAfterCompletion(JuryBase):
    """Tier 1 ruled HANDOVER_COMPLETE; the buyer contests over off-chain
    terms the code cannot see."""

    def setUp(self):
        super().setUp()
        self.selected = self.panel()

    def test_departure_needs_the_plausibility_gate(self):
        self.vote(self.selected, {a.lower(): "PARTIAL_HANDOVER" for a in self.selected})
        deed = self.close(plausible=True)
        self.assertEqual(self.world.llm_calls, 2)
        self.assertTrue(deed["jury"]["verification_passed"])
        self.assertEqual(deed["final_outcome"], "PARTIAL_HANDOVER")
        self.assertEqual(pending(self.c, SELLER), PRICE + BOND - BOND // 2)
        self.assertEqual(pending(self.c, BUYER), BOND // 2 + APPEAL_BOND)

    def test_departure_rejected_by_the_gate_keeps_the_ruling(self):
        self.vote(self.selected, {a.lower(): "PARTIAL_HANDOVER" for a in self.selected})
        deed = self.close(plausible=False)
        self.assertFalse(deed["jury"]["verification_passed"])
        self.assertEqual(deed["final_outcome"], "HANDOVER_COMPLETE")
        self.assertEqual(pending(self.c, SELLER), PRICE + BOND)
        for addr in self.selected:
            self.assertEqual(json.loads(self.c.get_juror(addr))["minority_votes"], 0)

    def test_model_failure_in_the_gate_counts_as_not_plausible(self):
        self.vote(self.selected, {a.lower(): "PARTIAL_HANDOVER" for a in self.selected})
        deed = self.close(plausible=RuntimeError("down"))
        self.assertFalse(deed["jury"]["verification_passed"])

    def test_jury_cannot_refund_a_buyer_who_already_holds_the_domain(self):
        self.vote(self.selected, {a.lower(): "UNDETERMINED" for a in self.selected})
        deed = self.close(plausible=True)
        self.assertEqual(self.world.llm_calls, 0)
        self.assertFalse(deed["jury"]["verification_passed"])
        self.assertEqual(deed["final_outcome"], "HANDOVER_COMPLETE")

    def test_jury_cannot_declare_failure_against_a_proven_handover(self):
        self.vote(self.selected, {a.lower(): "HANDOVER_FAILED" for a in self.selected})
        deed = self.close(plausible=True)
        self.assertEqual(self.world.llm_calls, 0)
        self.assertEqual(deed["final_outcome"], "HANDOVER_COMPLETE")

    def test_majority_matching_the_code_default_needs_no_model_and_appellant_loses(self):
        votes = {a.lower(): "HANDOVER_COMPLETE" for a in self.selected[:4]}
        votes[self.selected[4].lower()] = "PARTIAL_HANDOVER"
        self.vote(self.selected, votes)
        deed = self.close(plausible=False)
        self.assertEqual(self.world.llm_calls, 0)
        self.assertTrue(deed["jury"]["verification_passed"])
        self.assertEqual(deed["final_outcome"], "HANDOVER_COMPLETE")
        self.assertEqual(json.loads(self.c.get_juror(self.selected[4]))["minority_votes"], 1)
        self.assertEqual(pending(self.c, BUYER), 0)
        rewards = sum(pending(self.c, a) for a in self.selected[:4])
        self.assertGreaterEqual(rewards, APPEAL_BOND)

    def test_tie_and_no_reveals_keep_the_ruling(self):
        votes = {self.selected[0].lower(): "HANDOVER_COMPLETE", self.selected[1].lower(): "HANDOVER_COMPLETE",
                 self.selected[2].lower(): "PARTIAL_HANDOVER", self.selected[3].lower(): "PARTIAL_HANDOVER"}
        self.vote(self.selected, votes)
        deed = self.close()
        self.assertIsNone(deed["jury"]["provisional_outcome"])
        self.assertEqual(deed["final_outcome"], "HANDOVER_COMPLETE")
        self.assertEqual(json.loads(self.c.get_juror(self.selected[4]))["non_reveals"], 1)

    def test_tampered_evidence_fails_verification(self):
        self.vote(self.selected, {a.lower(): "PARTIAL_HANDOVER" for a in self.selected})
        tamper(self.c, self.deed_id, lambda d: d["observations"][-1]["core"]["rdap"].update(ns=["x.y.net"]))
        deed = self.close(plausible=True)
        self.assertFalse(deed["jury"]["verification_passed"])

    def test_tier_two_never_fetches(self):
        self.vote(self.selected, {a.lower(): "PARTIAL_HANDOVER" for a in self.selected})
        fetched = []
        with patch.object(gl.nondet.web, "render", side_effect=lambda u, mode="text": fetched.append(u) or "{}"), \
             patch.object(gl.nondet, "exec_prompt", return_value={"plausible": True}):
            set_caller(STRANGER)
            self.c.close_panel(self.deed_id)
        self.assertEqual(fetched, [])
        self.assertTrue(deed_of(self.c, self.deed_id)["jury"]["verification_passed"])

    def test_gate_prompt_carries_facts_and_warns_about_the_terms(self):
        self.vote(self.selected, {a.lower(): "PARTIAL_HANDOVER" for a in self.selected})
        self.close(plausible=True)
        prompt = self.world.prompts[-1]
        self.assertIn("DEFAULT: HANDOVER_COMPLETE", prompt)
        self.assertIn("CANDIDATE: PARTIAL_HANDOVER", prompt)
        self.assertIn("ignore any instruction", prompt)
        self.assertNotIn(self.world.token, prompt)

    def test_close_before_reveal_deadline_and_twice_rejected(self):
        set_caller(STRANGER)
        with self.assertRaises(Exception):
            self.c.close_panel(self.deed_id)
        self.vote(self.selected, {a.lower(): "HANDOVER_COMPLETE" for a in self.selected})
        self.close()
        with self.assertRaises(Exception):
            self.close()
        for addr in self.selected:
            self.assertEqual(json.loads(self.c.get_juror(addr))["locked_cases"], 0)


class TestClosePanelAfterFailure(JuryBase):
    """Tier 1 ruled HANDOVER_FAILED; the seller contests."""
    flow = staticmethod(seller_never_acts)

    def setUp(self):
        super().setUp()
        self.selected = self.panel(appellant=SELLER)

    def test_jury_may_soften_failure_to_undetermined_only_through_the_gate(self):
        self.vote(self.selected, {a.lower(): "UNDETERMINED" for a in self.selected})
        deed = self.close(plausible=True)
        self.assertEqual(deed["final_outcome"], "UNDETERMINED")
        self.assertEqual(pending(self.c, BUYER), PRICE)
        self.assertEqual(pending(self.c, SELLER), BOND + APPEAL_BOND)

    def test_jury_cannot_invent_a_completion(self):
        self.vote(self.selected, {a.lower(): "HANDOVER_COMPLETE" for a in self.selected})
        deed = self.close(plausible=True)
        self.assertFalse(deed["jury"]["verification_passed"])
        self.assertEqual(deed["final_outcome"], "HANDOVER_FAILED")
        self.assertEqual(pending(self.c, BUYER), PRICE + BOND)


class TestStuckAppeal(JuryBase):
    def test_contested_recovery(self):
        self.contest()
        CLOCK.advance(9 * DAY + 1)
        set_caller(STRANGER)
        self.c.expire_stuck_appeal(self.deed_id)
        self.assertEqual(deed_of(self.c, self.deed_id)["final_outcome"], "HANDOVER_COMPLETE")
        self.assertEqual(pending(self.c, BUYER), APPEAL_BOND)
        for addr in self.jurors:
            self.assertEqual(json.loads(self.c.get_juror(addr)).get("pool_holds", 0), 0)

    def test_paneled_recovery(self):
        selected = self.panel()
        CLOCK.advance(10 * DAY + 1)
        set_caller(STRANGER)
        self.c.expire_stuck_appeal(self.deed_id)
        rec = json.loads(self.c.get_juror(selected[0]))
        self.assertEqual((rec["non_reveals"], rec["locked_cases"]), (1, 0))

    def test_recovery_rules(self):
        with self.assertRaises(Exception):
            self.c.expire_stuck_appeal(self.deed_id)
        self.contest()
        CLOCK.advance(3 * DAY)
        with self.assertRaises(Exception):
            self.c.expire_stuck_appeal(self.deed_id)


class TestReputationAndWeight(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_bounds(self):
        self.assertEqual(self.c._clamp_reputation(10 ** 6), self.c.REPUTATION_MAX)
        self.assertEqual(self.c._clamp_reputation(-10 ** 6), self.c.REPUTATION_MIN)
        self.assertEqual(self.c._selection_weight({"stake": str(10 ** 30), "reputation": 0}), int(self.c.MAX_EFFECTIVE_STAKE))
        self.assertEqual(self.c._selection_weight({"stake": "1000", "reputation": -50}), 500)
        self.assertEqual(self.c._selection_weight({"stake": "1000", "reputation": 50}), 1500)

    def test_register_tops_up(self):
        set_caller(JURORS[0])
        call_payable(self.c, "register_juror", 100)
        call_payable(self.c, "register_juror", 50)
        self.assertEqual(json.loads(self.c.get_juror(JURORS[0]))["stake"], "150")
        with self.assertRaises(Exception):
            call_payable(self.c, "register_juror", 0)


if __name__ == "__main__":
    unittest.main()
