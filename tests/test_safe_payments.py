"""Invalid deposits are refunded, never stranded, and a payout that did not
leave the contract can be reconciled."""
import datetime
import json
import unittest

from _bootstrap import (
    M, CLOCK, BUYER, SELLER, STRANGER, JURORS, HOUR, DAY, PRICE, BOND, gl, Rejected,
    make_contract, set_caller, call_payable, create, accept, active_deed, deed_of, pending, honest_handover,
    close_window, deliver_transfers, age_jurors, iso, NS, HOSTS,
)


def settle(c, deed_id):
    close_window(c, deed_id)
    set_caller(STRANGER)
    c.close_uncontested(deed_id)


def rejected_result(callable_):
    try:
        callable_()
    except Rejected as err:
        return err.result
    raise AssertionError("the call was not rejected")


class TestInvalidDepositsAreRefunded(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_rejection_is_a_normal_return_with_a_reason_and_the_refund_amount(self):
        gl.message.value = M.u256(777)
        self.c.balance = self.c.balance + M.u256(777)
        set_caller(BUYER)
        out = json.loads(self.c.create_deed(
            SELLER, "bad domain", iso(CLOCK.t + datetime.timedelta(hours=6)), ",".join(NS), HOSTS, "terms", "400", "", 1))
        gl.message.value = M.u256(0)
        self.assertTrue(out["rejected"])
        self.assertEqual(out["refund_wei"], "777")
        self.assertIn("domain", out["reason"])
        self.assertEqual(pending(self.c, BUYER), 777)

    def test_invalid_create_changes_no_state_and_the_value_can_be_withdrawn(self):
        result = rejected_result(lambda: create(self.c, domain="bad domain"))
        self.assertEqual(result["refund_wei"], str(PRICE))
        self.assertEqual(self.c.total_deeds(), 0)
        self.assertEqual(pending(self.c, BUYER), PRICE)
        set_caller(BUYER)
        self.c.withdraw()
        self.assertEqual(gl.evm.transfers[-1], {"to": M.Address(BUYER).__str__(), "value": M.u256(PRICE)})
        self.assertEqual(pending(self.c, BUYER), 0)

    def test_every_invalid_create_input_refunds_the_whole_value(self):
        cases = [
            dict(price=0), dict(bond=0), dict(bond="abc"), dict(domain=""), dict(domain="a b.com"),
            dict(nameservers="one.net"), dict(hosts="dns.google"), dict(proof="x1"), dict(gap_hours=0),
            dict(deadline_in=1), dict(seller=BUYER), dict(terms=""),
        ]
        for kw in cases:
            with self.subTest(kw=kw):
                c = make_contract()
                price = kw.get("price", PRICE)
                try:
                    create(c, **kw)
                except Rejected as err:
                    self.assertEqual(err.result["refund_wei"], str(price))
                    self.assertEqual(pending(c, BUYER), price)
                    self.assertEqual(c.total_deeds(), 0)

    def test_a_malformed_seller_address_is_refunded_not_reverted(self):
        result = rejected_result(lambda: create(self.c, seller="not-an-address"))
        self.assertTrue(result["rejected"])
        self.assertEqual(pending(self.c, BUYER), PRICE)

    def test_wrong_bond_is_refunded_and_the_deed_stays_open(self):
        deed_id = create(self.c)
        for bad in (BOND - 1, BOND + 1, 0):
            with self.subTest(bond=bad):
                before = pending(self.c, SELLER)
                rejected_result(lambda: accept(self.c, deed_id, bond=bad))
                self.assertEqual(pending(self.c, SELLER), before + bad)
                self.assertEqual(deed_of(self.c, deed_id)["status"], "proposed")
        accept(self.c, deed_id)
        self.assertEqual(deed_of(self.c, deed_id)["status"], "active")

    def test_accept_by_a_stranger_or_after_the_deadline_or_twice_is_refunded(self):
        deed_id = create(self.c, deadline_in=2 * HOUR)
        rejected_result(lambda: accept(self.c, deed_id, caller=STRANGER))
        self.assertEqual(pending(self.c, STRANGER), BOND)
        accept(self.c, deed_id)
        rejected_result(lambda: accept(self.c, deed_id))
        self.assertEqual(pending(self.c, SELLER), BOND)
        other = create(self.c, deadline_in=2 * HOUR)
        CLOCK.advance(3 * HOUR)
        rejected_result(lambda: accept(self.c, other))
        self.assertEqual(pending(self.c, SELLER), 2 * BOND)
        self.assertEqual(deed_of(self.c, other)["status"], "proposed")

    def test_juror_stake_below_the_minimum_is_refunded(self):
        set_caller(JURORS[0])
        M.NameDeed.MIN_JUROR_STAKE = M.u256(50)
        try:
            result = rejected_result(lambda: call_payable(self.c, "register_juror", 5))
        finally:
            M.NameDeed.MIN_JUROR_STAKE = M.u256(1)
        self.assertEqual(result["refund_wei"], "5")
        self.assertEqual(pending(self.c, JURORS[0]), 5)
        self.assertEqual(json.loads(self.c.get_juror(JURORS[0]))["stake"], "0")

    def test_invalid_appeals_are_refunded_and_change_nothing(self):
        for i, addr in enumerate(JURORS[:6]):
            set_caller(addr)
            call_payable(self.c, "register_juror", 1000 + i)
        age_jurors(self.c)
        deed_id, _ = honest_handover(self.c)
        bond = PRICE * 2000 // 10000
        set_caller(STRANGER)
        rejected_result(lambda: call_payable(self.c, "contest", bond, deed_id))
        self.assertEqual(pending(self.c, STRANGER), bond)
        set_caller(BUYER)
        rejected_result(lambda: call_payable(self.c, "contest", bond + 1, deed_id))
        self.assertEqual(pending(self.c, BUYER), bond + 1)
        deed = deed_of(self.c, deed_id)
        self.assertEqual(deed["status"], "ruled")
        self.assertIsNone(deed["appeal"])
        for addr in JURORS[:6]:
            self.assertEqual(json.loads(self.c.get_juror(addr)).get("pool_holds", 0), 0)

    def test_appeal_without_enough_jurors_is_refunded(self):
        deed_id, _ = honest_handover(self.c)
        set_caller(BUYER)
        bond = PRICE * 2000 // 10000
        result = rejected_result(lambda: call_payable(self.c, "contest", bond, deed_id))
        self.assertIn("jurors", result["reason"])
        self.assertEqual(pending(self.c, BUYER), bond)

    def test_the_balance_always_matches_the_accounting(self):
        rejected_result(lambda: create(self.c, domain="bad domain"))
        deed_id = active_deed(self.c)
        rejected_result(lambda: accept(self.c, deed_id, caller=STRANGER))
        status = json.loads(self.c.get_withdrawal_status())
        self.assertEqual(status["held"], status["balance"])
        self.assertEqual(int(status["balance"]), PRICE + PRICE + BOND + BOND)


class TestWithdrawReconciliation(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        deed_id, _ = honest_handover(self.c)
        settle(self.c, deed_id)

    def withdraw(self, who):
        set_caller(who)
        return self.c.withdraw()

    def reconcile(self):
        set_caller(STRANGER)
        return self.c.reconcile_withdrawal()

    def status(self):
        return json.loads(self.c.get_withdrawal_status())

    def test_withdraw_records_the_payout_and_the_transfer_is_confirmed_once_it_left(self):
        self.withdraw(SELLER)
        status = self.status()
        self.assertEqual(status["in_flight"]["amount"], str(PRICE + BOND))
        self.assertEqual(self.reconcile(), "waiting")
        deliver_transfers(self.c)
        self.assertEqual(self.reconcile(), "confirmed")
        status = self.status()
        self.assertIsNone(status["in_flight"])
        self.assertEqual(status["held"], status["balance"])
        self.assertEqual(int(status["balance"]), 0)
        self.assertEqual(int(self.c.confirmed_sent), PRICE + BOND)

    def test_a_second_withdrawal_waits_for_the_first_to_be_confirmed(self):
        c = self.c
        deed_id = active_deed(c, price=PRICE, bond=BOND)
        set_caller(BUYER)
        c._credit(BUYER, M.u256(5))
        c.balance = c.balance + M.u256(5)
        c.total_received = M.u256(int(c.total_received) + 5)
        self.withdraw(SELLER)
        with self.assertRaises(Exception):
            self.withdraw(BUYER)
        deliver_transfers(c)
        self.withdraw(BUYER)
        self.assertEqual(gl.evm.transfers[-1]["value"], M.u256(5))
        self.assertEqual(self.status()["in_flight"]["to"].lower(), BUYER)
        self.assertIn(deed_id, ["deed_1"])

    def test_the_next_withdrawal_settles_the_previous_one_by_itself(self):
        self.withdraw(SELLER)
        deliver_transfers(self.c)
        self.c._credit(STRANGER, M.u256(9))
        self.c.balance = self.c.balance + M.u256(9)
        self.c.total_received = M.u256(int(self.c.total_received) + 9)
        self.withdraw(STRANGER)
        self.assertEqual(int(self.c.confirmed_sent), PRICE + BOND)

    def test_a_transfer_that_never_left_is_restored_only_after_the_delay(self):
        self.withdraw(SELLER)
        deliver_transfers(self.c, deliver=False)
        self.assertEqual(pending(self.c, SELLER), 0)
        CLOCK.advance(7 * DAY - 1)
        self.assertEqual(self.reconcile(), "waiting")
        self.assertEqual(pending(self.c, SELLER), 0)
        CLOCK.advance(2)
        self.assertEqual(self.reconcile(), "restored")
        self.assertEqual(pending(self.c, SELLER), PRICE + BOND)
        self.assertIsNone(self.status()["in_flight"])
        self.withdraw(SELLER)
        deliver_transfers(self.c)
        self.assertEqual(self.reconcile(), "confirmed")
        self.assertEqual(int(self.c.balance), 0)

    def test_a_delivered_transfer_is_never_restored(self):
        self.withdraw(SELLER)
        deliver_transfers(self.c)
        CLOCK.advance(30 * DAY)
        self.assertEqual(self.reconcile(), "confirmed")
        self.assertEqual(pending(self.c, SELLER), 0)

    def test_reconcile_with_nothing_in_flight_is_rejected(self):
        with self.assertRaises(Exception):
            self.reconcile()

    def test_an_inconsistent_balance_is_not_guessed_at(self):
        self.withdraw(SELLER)
        self.c.balance = M.u256(int(self.c.balance) - 1)
        with self.assertRaises(Exception):
            self.reconcile()
        self.assertIsNotNone(self.status()["in_flight"])

    def test_withdraw_still_zeroes_the_ledger_before_the_transfer(self):
        self.withdraw(SELLER)
        self.assertEqual(pending(self.c, SELLER), 0)
        with self.assertRaises(Exception):
            self.withdraw(SELLER)

    def test_money_is_conserved_through_a_failed_then_retried_payout(self):
        total_in = int(self.c.total_received)
        self.withdraw(SELLER)
        deliver_transfers(self.c, deliver=False)
        CLOCK.advance(8 * DAY)
        self.reconcile()
        self.withdraw(SELLER)
        deliver_transfers(self.c)
        self.reconcile()
        self.assertEqual(int(self.c.confirmed_sent) + int(self.c.balance), total_in)
        self.assertEqual(pending(self.c, SELLER), 0)


if __name__ == "__main__":
    unittest.main()
