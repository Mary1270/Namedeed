import datetime
import hashlib
import json
import unittest

from _bootstrap import (
    M, CLOCK, BUYER, SELLER, STRANGER, DOMAIN, NS, HOUR, DAY, PRICE, BOND, BUYER_REGISTRAR,
    make_contract, set_caller, create, accept, active_deed, deed_of, pending,
)


class TestCreateValidation(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_create_records_all_anchors(self):
        deed = deed_of(self.c, create(self.c))
        self.assertEqual(deed["deed_id"], "deed_0")
        self.assertEqual(deed["buyer"].lower(), BUYER)
        self.assertEqual(deed["seller"].lower(), SELLER)
        self.assertEqual(deed["domain"], DOMAIN)
        self.assertEqual(deed["record_type"], "TXT")
        self.assertEqual(deed["nameservers"], sorted(NS))
        self.assertEqual(deed["evidence_hosts"], ["dns.google", "rdap.verisign.com"])
        self.assertEqual((deed["price"], deed["seller_bond"]), (str(PRICE), str(BOND)))
        self.assertEqual(deed["min_gap_seconds"], 3600)
        self.assertEqual(deed["mode"], "delegation")
        self.assertEqual(deed["registrar_id"], "")
        self.assertEqual(deed["status"], "proposed")
        self.assertEqual(deed["observations"], [])
        self.assertEqual(deed["challenge_name"], "_namedeed.example.com")

    def test_registrar_id_selects_transfer_mode(self):
        deed = deed_of(self.c, create(self.c, registrar=BUYER_REGISTRAR))
        self.assertEqual((deed["mode"], deed["registrar_id"]), ("transfer", BUYER_REGISTRAR))

    def test_invalid_registrar_ids_rejected(self):
        for bad in ("0", "01", "abc", "12a", "1" * 11, "-5", "1.5"):
            with self.subTest(registrar=bad):
                with self.assertRaises(Exception):
                    create(self.c, registrar=bad)

    def test_ids_increment(self):
        self.assertEqual(create(self.c), "deed_0")
        self.assertEqual(create(self.c), "deed_1")
        self.assertEqual(self.c.total_deeds(), 2)

    def test_price_and_bond_are_independent(self):
        deed = deed_of(self.c, create(self.c, price=10_000, bond=500))
        self.assertEqual((deed["price"], deed["seller_bond"]), ("10000", "500"))

    def test_zero_price_rejected(self):
        with self.assertRaises(Exception):
            create(self.c, price=0)

    def test_bad_bonds_rejected(self):
        for bad in (0, -1, "1.5", "abc", "", "1" * 32):
            with self.subTest(bond=bad):
                with self.assertRaises(Exception):
                    create(self.c, bond=bad)

    def test_seller_must_differ_from_buyer(self):
        with self.assertRaises(Exception):
            create(self.c, seller=BUYER)

    def test_seller_must_be_valid_address(self):
        with self.assertRaises(Exception):
            create(self.c, seller="not-an-address")

    def test_deadline_bounds(self):
        with self.assertRaises(Exception):
            create(self.c, deadline_in=600)
        with self.assertRaises(Exception):
            create(self.c, deadline_in=31 * DAY)
        create(self.c, deadline_in=3600)
        create(self.c, deadline_in=30 * DAY)

    def test_domain_normalisation(self):
        self.assertEqual(deed_of(self.c, create(self.c, domain="Example.COM"))["domain"], "example.com")
        self.assertEqual(deed_of(self.c, create(self.c, domain="example.com."))["domain"], "example.com")

    def test_invalid_domains_rejected(self):
        for bad in ("localhost", "exa mple.com", "example..com", "-bad.com", "bad-.com", "http://example.com",
                    "example.com/path", "1.2.3.4", "_dmarc.example.com", "", "a" * 64 + ".com",
                    "ex_ample.com", "*.example.com"):
            with self.subTest(domain=bad):
                with self.assertRaises(Exception):
                    create(self.c, domain=bad)

    def test_punycode_domain_accepted(self):
        self.assertEqual(deed_of(self.c, create(self.c, domain="xn--bcher-kva.com"))["domain"], "xn--bcher-kva.com")

    def test_nameservers_normalised(self):
        deed = deed_of(self.c, create(self.c, nameservers="NS2.Buyer-DNS.net., ns1.buyer-dns.net,ns2.buyer-dns.net"))
        self.assertEqual(deed["nameservers"], ["ns1.buyer-dns.net", "ns2.buyer-dns.net"])

    def test_nameserver_count_bounds(self):
        with self.assertRaises(Exception):
            create(self.c, nameservers="ns1.buyer-dns.net")
        with self.assertRaises(Exception):
            create(self.c, nameservers=",".join(f"ns{i}.buyer-dns.net" for i in range(9)))
        create(self.c, nameservers=",".join(f"ns{i}.buyer-dns.net" for i in range(8)))

    def test_invalid_nameserver_rejected(self):
        for bad in ("ns1.buyer-dns.net,not a host", "ns1.buyer-dns.net,http://x.com", "ns1.buyer-dns.net,1.2.3.4"):
            with self.subTest(ns=bad):
                with self.assertRaises(Exception):
                    create(self.c, nameservers=bad)

    def test_evidence_host_rules(self):
        for bad in ("dns.google", "dns.google,rdap.verisign.com,rdap.publicinterestregistry.org", "",
                    "rdap.verisign.com,rdap.publicinterestregistry.org", "dns.google,rdap.evil.example",
                    "dns.google,x.dns.google"):
            with self.subTest(hosts=bad):
                with self.assertRaises(Exception):
                    create(self.c, hosts=bad)
        deed = deed_of(self.c, create(self.c, hosts="rdap.verisign.com, DNS.GOOGLE"))
        self.assertEqual(deed["evidence_hosts"], ["dns.google", "rdap.verisign.com"])

    def test_rdap_host_must_serve_the_tld(self):
        with self.assertRaises(Exception):
            create(self.c, domain="example.org")
        with self.assertRaises(Exception):
            create(self.c, hosts="dns.google,rdap.publicinterestregistry.org")
        create(self.c, domain="example.org", hosts="dns.google,rdap.publicinterestregistry.org")
        create(self.c, domain="example.net")

    def test_terms_bounds(self):
        with self.assertRaises(Exception):
            create(self.c, terms="   ")
        with self.assertRaises(Exception):
            create(self.c, terms="x" * 2001)
        create(self.c, terms="x" * 2000)

    def test_gap_hours_bounds(self):
        create(self.c, gap_hours=1)
        create(self.c, gap_hours=72)
        for bad in (0, 73, -1):
            with self.subTest(gap=bad):
                with self.assertRaises(Exception):
                    create(self.c, gap_hours=bad)

    def test_failed_create_does_not_consume_an_id_or_keep_value(self):
        with self.assertRaises(Exception):
            create(self.c, domain="bad domain")
        self.assertEqual(self.c.total_deeds(), 0)
        self.assertEqual(int(self.c.balance), 0)


class TestAcceptAndCancel(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_accept_activates_and_derives_token(self):
        deed_id = create(self.c)
        accept(self.c, deed_id)
        deed = deed_of(self.c, deed_id)
        self.assertEqual(deed["status"], "active")
        material = f"{deed_id}:{DOMAIN}:{BUYER}:{SELLER}"
        self.assertEqual(deed["challenge_token"], hashlib.sha256(material.encode()).hexdigest())

    def test_token_binds_deed_domain_and_parties(self):
        base = M._challenge_token("deed_0", "example.com", BUYER, SELLER)
        for other in (M._challenge_token("deed_0", "example.net", BUYER, SELLER),
                      M._challenge_token("deed_0", "example.com", SELLER, BUYER),
                      M._challenge_token("deed_0", "example.com", BUYER, STRANGER),
                      M._challenge_token("deed_1", "example.com", BUYER, SELLER)):
            self.assertNotEqual(base, other)

    def test_token_differs_per_deed(self):
        a, b = active_deed(self.c), active_deed(self.c)
        self.assertNotEqual(deed_of(self.c, a)["challenge_token"], deed_of(self.c, b)["challenge_token"])

    def test_accept_only_by_designated_seller(self):
        deed_id = create(self.c)
        for caller in (BUYER, STRANGER):
            with self.assertRaises(Exception):
                accept(self.c, deed_id, caller=caller)

    def test_accept_requires_exact_bond(self):
        deed_id = create(self.c)
        for value in (BOND - 1, BOND + 1, PRICE, 0):
            with self.assertRaises(Exception):
                accept(self.c, deed_id, bond=value)
        accept(self.c, deed_id)

    def test_accept_twice_and_after_deadline_rejected(self):
        deed_id = active_deed(self.c)
        with self.assertRaises(Exception):
            accept(self.c, deed_id)
        late = create(self.c)
        CLOCK.advance(7 * HOUR)
        with self.assertRaises(Exception):
            accept(self.c, late)

    def test_evidence_deadline_is_window_after_deadline(self):
        deed = deed_of(self.c, active_deed(self.c))
        gap = datetime.datetime.fromisoformat(deed["evidence_deadline"]) - datetime.datetime.fromisoformat(deed["deadline"])
        self.assertEqual(gap.total_seconds(), self.c.EVIDENCE_WINDOW_SECONDS)

    def test_cancel_refunds_price(self):
        deed_id = create(self.c)
        set_caller(BUYER)
        self.c.cancel_deed(deed_id)
        self.assertEqual(deed_of(self.c, deed_id)["status"], "cancelled")
        self.assertEqual(pending(self.c, BUYER), PRICE)

    def test_cancel_rules(self):
        deed_id = create(self.c)
        for caller in (SELLER, STRANGER):
            set_caller(caller)
            with self.assertRaises(Exception):
                self.c.cancel_deed(deed_id)
        set_caller(BUYER)
        self.c.cancel_deed(deed_id)
        with self.assertRaises(Exception):
            self.c.cancel_deed(deed_id)
        with self.assertRaises(Exception):
            accept(self.c, deed_id)
        accepted = active_deed(self.c)
        set_caller(BUYER)
        with self.assertRaises(Exception):
            self.c.cancel_deed(accepted)

    def test_get_challenge(self):
        deed_id = create(self.c)
        with self.assertRaises(Exception):
            self.c.get_challenge(deed_id)
        accept(self.c, deed_id)
        info = json.loads(self.c.get_challenge(deed_id))
        self.assertEqual(info, {"name": "_namedeed.example.com", "type": "TXT",
                                "value": deed_of(self.c, deed_id)["challenge_token"]})


if __name__ == "__main__":
    unittest.main()
