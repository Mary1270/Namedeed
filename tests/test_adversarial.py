"""Attacks by the buyer and by the seller against the handover proof. Each
test plays one party as the adversary inside the World model, where the
zone at _namedeed.<domain> is run by whoever the registry delegates to."""
import json
import unittest

from _bootstrap import (
    M, CLOCK, BUYER, SELLER, STRANGER, NS, SELLER_NS, OTHER_NS, BUYER_REGISTRAR, OTHER_REGISTRAR, SELLER_REGISTRAR,
    HOUR, DAY, MIN, PRICE, BOND,
    make_contract, set_caller, active_deed, deed_of, world_for, observe, rule, honest_handover,
    close_window, pending,
)


def settle(c, deed_id):
    close_window(c, deed_id)
    set_caller(STRANGER)
    c.close_uncontested(deed_id)
    return deed_of(c, deed_id)


class TestBuyerAsAdversary(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_buyer_withholding_the_token_after_the_switch_cannot_block_completion(self):
        deed_id, world = honest_handover(self.c)
        self.assertNotIn(world.token, world.zones["buyer"]["txt"])
        self.assertEqual(deed_of(self.c, deed_id)["ruling"], "HANDOVER_COMPLETE")
        settle(self.c, deed_id)
        self.assertEqual(pending(self.c, SELLER), PRICE + BOND)
        self.assertEqual(pending(self.c, BUYER), 0)

    def test_buyer_withholding_the_token_cannot_win_failed(self):
        deed_id, world = honest_handover(self.c, deadline_in=HOUR + 30 * MIN)
        self.assertNotEqual(deed_of(self.c, deed_id)["ruling"], "HANDOVER_FAILED")

    def test_buyer_publishing_apex_ns_noise_changes_nothing(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        world.seller_publishes()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        world.switch_delegation()
        world.zones["buyer"]["apex_ns"] = OTHER_NS
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(HOUR)
        rule(self.c, deed_id, world)
        self.assertEqual(deed_of(self.c, deed_id)["ruling"], "HANDOVER_COMPLETE")

    def test_buyer_cannot_rule_before_the_sellers_handover_matures(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        world.seller_publishes()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        world.switch_delegation()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(HOUR - 1)
        with self.assertRaises(Exception):
            rule(self.c, deed_id, world, caller=BUYER)
        CLOCK.advance(1)
        rule(self.c, deed_id, world, caller=BUYER)
        self.assertEqual(deed_of(self.c, deed_id)["ruling"], "HANDOVER_COMPLETE")

    def test_ruling_on_an_unobserved_fresh_handover_waits_instead_of_settling(self):
        deed_id = active_deed(self.c, deadline_in=2 * HOUR)
        world = world_for(self.c, deed_id)
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world, caller=BUYER)
        CLOCK.advance(3 * HOUR)
        world.switch_delegation()
        with self.assertRaises(Exception) as ctx:
            rule(self.c, deed_id, world, caller=BUYER)
        self.assertIn("not yet stable", str(ctx.exception))
        self.assertEqual(deed_of(self.c, deed_id)["status"], "observing")

    def test_buyer_observation_quota_is_bounded(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        for _ in range(3):
            CLOCK.advance(MIN)
            observe(self.c, deed_id, world, caller=BUYER)
        with self.assertRaises(Exception):
            observe(self.c, deed_id, world, caller=BUYER)
        world.seller_publishes()
        observe(self.c, deed_id, world, caller=SELLER)
        self.assertEqual(deed_of(self.c, deed_id)["observation_counts"], {"buyer": 3, "seller": 1})

    def test_buyer_moving_the_domain_away_after_a_transfer_cannot_frame_the_seller(self):
        deed_id, world = honest_handover(self.c, transfer=True, deadline_in=30 * DAY)
        self.assertEqual(deed_of(self.c, deed_id)["ruling"], "HANDOVER_COMPLETE")

        c2 = make_contract()
        deed2 = active_deed(c2, registrar=BUYER_REGISTRAR)
        w2 = world_for(c2, deed2)
        w2.seller_publishes()
        CLOCK.advance(10 * MIN)
        observe(c2, deed2, w2)
        w2.transfer_registrar()
        CLOCK.advance(10 * MIN)
        observe(c2, deed2, w2)
        CLOCK.advance(HOUR + MIN)
        observe(c2, deed2, w2)
        w2.transfer_registrar(OTHER_REGISTRAR)
        w2.switch_delegation(OTHER_NS)
        CLOCK.advance(8 * HOUR)
        rule(c2, deed2, w2, caller=BUYER)
        deed = deed_of(c2, deed2)
        self.assertEqual(deed["ruling"], "HANDOVER_COMPLETE")
        self.assertTrue(deed["facts"]["buyer_holds"])

    def test_buyer_publishing_a_token_in_their_own_zone_does_not_change_an_honest_completion(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        world.seller_publishes()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        world.switch_delegation()
        world.buyer_publishes()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(HOUR)
        rule(self.c, deed_id, world)
        self.assertEqual(deed_of(self.c, deed_id)["ruling"], "HANDOVER_COMPLETE")

    def test_buyer_publishing_a_token_after_the_switch_is_not_a_baseline(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        world.switch_delegation()
        world.buyer_publishes()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(7 * HOUR)
        rule(self.c, deed_id, world)
        deed = deed_of(self.c, deed_id)
        self.assertFalse(deed["facts"]["baseline_seen"])
        self.assertEqual(deed["ruling"], "UNDETERMINED")


class TestSellerAsAdversary(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_forged_apex_ns_in_the_sellers_own_zone_is_not_a_handover(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        world.seller_publishes()
        world.zones["seller"]["apex_ns"] = list(NS)
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(HOUR + MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(6 * HOUR)
        rule(self.c, deed_id, world)
        deed = deed_of(self.c, deed_id)
        self.assertFalse(deed["facts"]["handover_ever"])
        self.assertEqual(deed["ruling"], "PARTIAL_HANDOVER")
        settle(self.c, deed_id)
        self.assertEqual(pending(self.c, BUYER), PRICE + BOND // 2)
        self.assertEqual(pending(self.c, SELLER), BOND - BOND // 2)

    def test_forged_apex_ns_cannot_produce_complete_even_before_the_deadline(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        world.seller_publishes()
        world.zones["seller"]["apex_ns"] = list(NS)
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(HOUR)
        with self.assertRaises(Exception):
            rule(self.c, deed_id, world)

    def test_delegation_flashed_for_one_observation_does_not_count(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        world.switch_delegation()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        world.switch_delegation(SELLER_NS)
        CLOCK.advance(7 * HOUR)
        rule(self.c, deed_id, world)
        deed = deed_of(self.c, deed_id)
        self.assertTrue(deed["facts"]["handover_ever"])
        self.assertFalse(deed["facts"]["handover_stable"])
        self.assertEqual(deed["ruling"], "HANDOVER_FAILED")
        settle(self.c, deed_id)
        self.assertEqual(pending(self.c, BUYER), PRICE + BOND)

    def test_transfer_match_flashed_once_then_reverted_is_a_failure(self):
        deed_id = active_deed(self.c, registrar=BUYER_REGISTRAR)
        world = world_for(self.c, deed_id)
        world.seller_publishes()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        world.seller_unpublishes()
        world.transfer_registrar()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        world.transfer_registrar(SELLER_REGISTRAR)
        CLOCK.advance(7 * HOUR)
        rule(self.c, deed_id, world)
        deed = deed_of(self.c, deed_id)
        self.assertTrue(deed["facts"]["handover_ever"])
        self.assertFalse(deed["facts"]["handover_stable"])
        self.assertEqual(deed["ruling"], "HANDOVER_FAILED")
        settle(self.c, deed_id)
        self.assertEqual(pending(self.c, BUYER), PRICE + BOND)

    def test_transfer_match_seen_twice_but_closer_than_the_gap_then_reverted_is_a_failure(self):
        deed_id = active_deed(self.c, registrar=BUYER_REGISTRAR, gap_hours=2)
        world = world_for(self.c, deed_id)
        world.seller_publishes()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        world.seller_unpublishes()
        world.transfer_registrar()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(30 * MIN)
        observe(self.c, deed_id, world, caller=BUYER)
        world.transfer_registrar(SELLER_REGISTRAR)
        CLOCK.advance(7 * HOUR)
        rule(self.c, deed_id, world)
        deed = deed_of(self.c, deed_id)
        self.assertFalse(deed["facts"]["handover_stable"])
        self.assertEqual(deed["ruling"], "HANDOVER_FAILED")

    def test_delegation_flash_before_the_baseline_is_ignored(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        world.switch_delegation()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world, caller=BUYER)
        world.switch_delegation(SELLER_NS)
        world.seller_publishes()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        world.switch_delegation()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(HOUR)
        rule(self.c, deed_id, world)
        deed = deed_of(self.c, deed_id)
        self.assertTrue(deed["facts"]["baseline_seen"])
        self.assertEqual(deed["ruling"], "HANDOVER_COMPLETE")

    def test_stable_handover_then_revert_in_delegation_mode_is_the_sellers_failure(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        world.seller_publishes()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        world.seller_unpublishes()
        world.switch_delegation()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(HOUR + MIN)
        observe(self.c, deed_id, world)
        world.switch_delegation(SELLER_NS)
        CLOCK.advance(6 * HOUR)
        rule(self.c, deed_id, world)
        self.assertEqual(deed_of(self.c, deed_id)["ruling"], "HANDOVER_FAILED")

    def test_stable_handover_then_revert_with_the_token_back_in_the_sellers_zone_is_partial(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        world.seller_publishes()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        world.switch_delegation()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(HOUR + MIN)
        observe(self.c, deed_id, world)
        world.switch_delegation(SELLER_NS)
        CLOCK.advance(6 * HOUR)
        rule(self.c, deed_id, world)
        deed = deed_of(self.c, deed_id)
        self.assertTrue(deed["facts"]["control_in_time"])
        self.assertEqual(deed["ruling"], "PARTIAL_HANDOVER")

    def test_flash_right_before_ruling_cannot_force_a_quick_settlement(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world, caller=BUYER)
        CLOCK.advance(7 * HOUR)
        world.switch_delegation()
        with self.assertRaises(Exception):
            rule(self.c, deed_id, world)
        world.switch_delegation(SELLER_NS)
        rule(self.c, deed_id, world)
        self.assertEqual(deed_of(self.c, deed_id)["ruling"], "HANDOVER_FAILED")

    def test_a_token_flashed_once_is_not_control(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        world.seller_publishes()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        world.seller_unpublishes()
        CLOCK.advance(7 * HOUR)
        rule(self.c, deed_id, world)
        deed = deed_of(self.c, deed_id)
        self.assertFalse(deed["facts"]["control_in_time"])
        self.assertEqual(deed["ruling"], "HANDOVER_FAILED")

    def test_token_twice_but_closer_than_the_gap_is_not_control(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        world.seller_publishes()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(30 * MIN)
        observe(self.c, deed_id, world)
        world.seller_unpublishes()
        CLOCK.advance(7 * HOUR)
        rule(self.c, deed_id, world)
        self.assertEqual(deed_of(self.c, deed_id)["ruling"], "HANDOVER_FAILED")

    def test_control_shown_only_after_the_deadline_is_not_partial(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        CLOCK.advance(7 * HOUR)
        world.seller_publishes()
        observe(self.c, deed_id, world)
        CLOCK.advance(HOUR + MIN)
        observe(self.c, deed_id, world)
        world.seller_unpublishes()
        CLOCK.advance(2 * HOUR)
        rule(self.c, deed_id, world)
        self.assertEqual(deed_of(self.c, deed_id)["ruling"], "HANDOVER_FAILED")

    def test_another_deeds_token_is_not_control(self):
        other = active_deed(self.c)
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        world.seller_publishes(deed_of(self.c, other)["challenge_token"])
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(HOUR + MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(7 * HOUR)
        rule(self.c, deed_id, world)
        self.assertEqual(deed_of(self.c, deed_id)["ruling"], "HANDOVER_FAILED")

    def test_delegation_to_a_third_party_is_not_a_handover(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        world.switch_delegation(OTHER_NS)
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(HOUR + MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(7 * HOUR)
        rule(self.c, deed_id, world)
        self.assertEqual(deed_of(self.c, deed_id)["ruling"], "HANDOVER_FAILED")

    def test_partial_delegation_is_not_a_handover(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        world.switch_delegation([NS[0], SELLER_NS[0]])
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(HOUR + MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(7 * HOUR)
        rule(self.c, deed_id, world)
        self.assertFalse(deed_of(self.c, deed_id)["facts"]["handover_ever"])

    def test_transfer_mode_ignores_a_delegation_only_switch(self):
        deed_id = active_deed(self.c, registrar=BUYER_REGISTRAR)
        world = world_for(self.c, deed_id)
        world.switch_delegation()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(HOUR + MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(7 * HOUR)
        rule(self.c, deed_id, world)
        self.assertEqual(deed_of(self.c, deed_id)["ruling"], "HANDOVER_FAILED")

    def test_transfer_to_the_wrong_registrar_is_not_a_handover(self):
        deed_id = active_deed(self.c, registrar=BUYER_REGISTRAR)
        world = world_for(self.c, deed_id)
        world.transfer_registrar(OTHER_REGISTRAR)
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(HOUR + MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(7 * HOUR)
        rule(self.c, deed_id, world)
        self.assertEqual(deed_of(self.c, deed_id)["ruling"], "HANDOVER_FAILED")

    def test_wrong_rdap_object_is_not_a_handover(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        world.switch_delegation()
        world.rdap_name = "other.com"
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(HOUR + MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(7 * HOUR)
        rule(self.c, deed_id, world)
        deed = deed_of(self.c, deed_id)
        self.assertFalse(deed["facts"]["handover_ever"])
        self.assertFalse(deed["facts"]["failure_shown"])
        self.assertEqual(deed["ruling"], "UNDETERMINED")


class TestHonestPaths(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_delegation_handover_completes_before_the_deadline(self):
        deed_id, _ = honest_handover(self.c)
        deed = deed_of(self.c, deed_id)
        self.assertEqual(deed["ruling"], "HANDOVER_COMPLETE")
        self.assertFalse(deed["facts"]["past_deadline"])

    def test_registrar_transfer_completes(self):
        deed_id, _ = honest_handover(self.c, transfer=True)
        deed = deed_of(self.c, deed_id)
        self.assertEqual(deed["mode"], "transfer")
        self.assertEqual(deed["ruling"], "HANDOVER_COMPLETE")

    def test_handover_without_a_control_proof_before_it_is_never_complete(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        world.switch_delegation()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(HOUR)
        with self.assertRaises(Exception):
            rule(self.c, deed_id, world)
        CLOCK.advance(7 * HOUR)
        rule(self.c, deed_id, world)
        deed = deed_of(self.c, deed_id)
        self.assertEqual(deed["ruling"], "UNDETERMINED")
        self.assertTrue(deed["facts"]["handover_unbaselined"])
        settle(self.c, deed_id)
        self.assertEqual((pending(self.c, BUYER), pending(self.c, SELLER)), (PRICE, BOND))

    def test_domain_already_at_the_buyers_registrar_cannot_pay_the_seller(self):
        deed_id = active_deed(self.c, registrar=BUYER_REGISTRAR)
        world = world_for(self.c, deed_id)
        world.transfer_registrar()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(HOUR + MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(7 * HOUR)
        rule(self.c, deed_id, world)
        deed = deed_of(self.c, deed_id)
        self.assertEqual(deed["ruling"], "UNDETERMINED")
        self.assertFalse(deed["facts"]["complete_proven"])
        settle(self.c, deed_id)
        self.assertEqual((pending(self.c, BUYER), pending(self.c, SELLER)), (PRICE, BOND))

    def test_seller_who_shows_control_after_the_domain_already_matches_still_cannot_complete(self):
        deed_id = active_deed(self.c, registrar=BUYER_REGISTRAR)
        world = world_for(self.c, deed_id)
        world.transfer_registrar()
        world.seller_publishes()
        CLOCK.advance(10 * MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(HOUR + MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(7 * HOUR)
        rule(self.c, deed_id, world)
        deed = deed_of(self.c, deed_id)
        self.assertEqual(deed["ruling"], "UNDETERMINED")
        self.assertFalse(deed["facts"]["baseline_seen"])

    def test_late_but_stable_handover_is_partial_and_pays_the_seller_less_the_penalty(self):
        deed_id = active_deed(self.c, deadline_in=2 * HOUR)
        world = world_for(self.c, deed_id)
        world.seller_publishes()
        observe(self.c, deed_id, world)
        CLOCK.advance(3 * HOUR)
        world.switch_delegation()
        observe(self.c, deed_id, world)
        CLOCK.advance(HOUR)
        rule(self.c, deed_id, world)
        deed = deed_of(self.c, deed_id)
        self.assertTrue(deed["facts"]["late_handover"])
        self.assertEqual(deed["ruling"], "PARTIAL_HANDOVER")
        settle(self.c, deed_id)
        self.assertEqual(pending(self.c, SELLER), PRICE + BOND - BOND // 2)
        self.assertEqual(pending(self.c, BUYER), BOND // 2)

    def test_handover_started_in_time_and_matured_after_the_deadline_is_complete(self):
        deed_id = active_deed(self.c, deadline_in=HOUR + 5 * MIN)
        world = world_for(self.c, deed_id)
        world.seller_publishes()
        observe(self.c, deed_id, world)
        world.switch_delegation()
        CLOCK.advance(50 * MIN)
        observe(self.c, deed_id, world)
        CLOCK.advance(HOUR)
        rule(self.c, deed_id, world)
        deed = deed_of(self.c, deed_id)
        self.assertTrue(deed["facts"]["past_deadline"])
        self.assertEqual(deed["ruling"], "HANDOVER_COMPLETE")

    def test_seller_who_does_nothing_loses_price_and_bond_to_the_buyer(self):
        from _bootstrap import seller_never_acts
        deed_id, _ = seller_never_acts(self.c)
        self.assertEqual(deed_of(self.c, deed_id)["ruling"], "HANDOVER_FAILED")
        settle(self.c, deed_id)
        self.assertEqual(pending(self.c, BUYER), PRICE + BOND)


if __name__ == "__main__":
    unittest.main()
