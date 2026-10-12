import json
import unittest

from _bootstrap import M, dns_json, rdap_json

TOKEN = "ab" * 32
QNAME = "_namedeed.example.com"


def txt(answers, status=0):
    return dns_json(QNAME, 16, status, answers)


class TestTxtParser(unittest.TestCase):
    def test_live_dns_google_unquoted_shape(self):
        raw = json.dumps({"Status": 0, "Answer": [
            {"name": QNAME + ".", "type": 16, "TTL": 300, "data": "google-site-verification=abc"},
            {"name": QNAME + ".", "type": 16, "TTL": 300, "data": TOKEN}]})
        self.assertTrue(M._observe_txt(raw, QNAME, TOKEN)["token"])

    def test_exact_token_present(self):
        out = M._observe_txt(txt([(QNAME + ".", 16, '"%s"' % TOKEN)]), QNAME, TOKEN)
        self.assertEqual(out, {"err": False, "status": 0, "token": True})

    def test_unquoted_token_present(self):
        out = M._observe_txt(txt([(QNAME + ".", 16, TOKEN)]), QNAME, TOKEN)
        self.assertTrue(out["token"])

    def test_escaped_quotes_stripped(self):
        out = M._observe_txt(txt([(QNAME + ".", 16, '\"%s\"' % TOKEN)]), QNAME, TOKEN)
        self.assertTrue(out["token"])

    def test_multi_chunk_value_joined(self):
        data = '"%s" "%s"' % (TOKEN[:30], TOKEN[30:])
        out = M._observe_txt(txt([(QNAME + ".", 16, data)]), QNAME, TOKEN)
        self.assertTrue(out["token"])

    def test_token_with_extra_text_rejected(self):
        out = M._observe_txt(txt([(QNAME + ".", 16, '"%s extra"' % TOKEN)]), QNAME, TOKEN)
        self.assertFalse(out["token"])

    def test_prefix_form_rejected(self):
        out = M._observe_txt(txt([(QNAME + ".", 16, '"namedeed=%s"' % TOKEN)]), QNAME, TOKEN)
        self.assertFalse(out["token"])

    def test_token_substring_rejected(self):
        out = M._observe_txt(txt([(QNAME + ".", 16, '"%s"' % TOKEN[:-1])]), QNAME, TOKEN)
        self.assertFalse(out["token"])

    def test_different_case_rejected(self):
        out = M._observe_txt(txt([(QNAME + ".", 16, '"%s"' % TOKEN.upper())]), QNAME, TOKEN)
        self.assertFalse(out["token"])

    def test_other_deed_token_rejected(self):
        out = M._observe_txt(txt([(QNAME + ".", 16, '"%s"' % ("cd" * 32))]), QNAME, TOKEN)
        self.assertFalse(out["token"])

    def test_token_among_other_records(self):
        answers = [(QNAME + ".", 16, '"v=spf1 -all"'), (QNAME + ".", 16, '"%s"' % TOKEN)]
        self.assertTrue(M._observe_txt(txt(answers), QNAME, TOKEN)["token"])

    def test_wrong_record_type_ignored(self):
        out = M._observe_txt(txt([(QNAME + ".", 1, '"%s"' % TOKEN)]), QNAME, TOKEN)
        self.assertFalse(out["token"])

    def test_wrong_name_ignored(self):
        out = M._observe_txt(txt([("other.example.com.", 16, '"%s"' % TOKEN)]), QNAME, TOKEN)
        self.assertFalse(out["token"])

    def test_name_match_ignores_case_and_trailing_dot(self):
        out = M._observe_txt(txt([("_NAMEDEED.Example.COM", 16, '"%s"' % TOKEN)]), QNAME, TOKEN)
        self.assertTrue(out["token"])

    def test_nxdomain_is_an_answer_without_token(self):
        out = M._observe_txt(dns_json(QNAME, 16, 3), QNAME, TOKEN)
        self.assertEqual(out, {"err": False, "status": 3, "token": False})

    def test_nonzero_status_never_counts_even_with_answers(self):
        out = M._observe_txt(txt([(QNAME + ".", 16, '"%s"' % TOKEN)], status=2), QNAME, TOKEN)
        self.assertFalse(out["token"])
        self.assertEqual(out["status"], 2)

    def test_garbage_is_an_error(self):
        for raw in ("", "<html>", "[]", "null", "42", '{"no": "status"}', '{"Status": "x"}'):
            with self.subTest(raw=raw):
                self.assertTrue(M._observe_txt(raw, QNAME, TOKEN)["err"])

    def test_ttl_does_not_change_the_result(self):
        a = M._observe_txt(dns_json(QNAME, 16, 0, [(QNAME + ".", 16, '"%s"' % TOKEN)], ttl=10), QNAME, TOKEN)
        b = M._observe_txt(dns_json(QNAME, 16, 0, [(QNAME + ".", 16, '"%s"' % TOKEN)], ttl=9999), QNAME, TOKEN)
        self.assertEqual(a, b)

    def test_non_dict_answers_ignored(self):
        raw = json.dumps({"Status": 0, "Answer": ["junk", 5, None, {"name": QNAME, "type": 16, "data": TOKEN}]})
        self.assertTrue(M._observe_txt(raw, QNAME, TOKEN)["token"])

    def test_answer_cap(self):
        answers = [{"name": QNAME, "type": 16, "data": "x"}] * 60 + [{"name": QNAME, "type": 16, "data": TOKEN}]
        raw = json.dumps({"Status": 0, "Answer": answers})
        self.assertFalse(M._observe_txt(raw, QNAME, TOKEN)["token"])


class TestRdapParser(unittest.TestCase):
    def rdap(self, **kw):
        body = json.loads(rdap_json("example.com", ["NS1.B.NET", "ns2.b.net."], "292",
                                    changed="2026-05-30T08:00:00Z"))
        body.update(kw)
        return json.dumps(body)

    def test_parses_the_live_verisign_shape(self):
        out = M._observe_rdap(self.rdap(), "example.com")
        self.assertEqual(out, {"err": False, "name_ok": True, "ns": ["ns1.b.net", "ns2.b.net"], "registrar": "292",
                               "registered": "2015-03-01T00:00:00+00:00", "changed": "2026-05-30T08:00:00+00:00"})

    def test_name_mismatch(self):
        self.assertFalse(M._observe_rdap(self.rdap(ldhName="OTHER.COM"), "example.com")["name_ok"])

    def test_nameservers_sorted_deduplicated_and_validated(self):
        servers = [{"ldhName": "B.X.NET"}, {"ldhName": "a.x.net."}, {"ldhName": "a.x.net"}, {"ldhName": "bad host"},
                   "junk", {"ldhName": "1.2.3.4"}]
        self.assertEqual(M._observe_rdap(self.rdap(nameservers=servers), "example.com")["ns"], ["a.x.net", "b.x.net"])

    def test_missing_or_bad_nameservers(self):
        self.assertEqual(M._observe_rdap(self.rdap(nameservers="oops"), "example.com")["ns"], [])
        body = json.loads(self.rdap())
        del body["nameservers"]
        self.assertEqual(M._observe_rdap(json.dumps(body), "example.com")["ns"], [])

    def test_registrar_id_only_from_the_registrar_role(self):
        entities = [{"roles": ["registrant"], "publicIds": [{"type": "IANA Registrar ID", "identifier": "9"}]},
                    {"roles": ["Registrar"], "publicIds": [{"type": "iana registrar id", "identifier": " 777 "}]}]
        self.assertEqual(M._observe_rdap(self.rdap(entities=entities), "example.com")["registrar"], "777")

    def test_registrar_id_must_be_digits(self):
        for ident in ("abc", "0", "", None, "12x"):
            entities = [{"roles": ["registrar"], "publicIds": [{"type": "IANA Registrar ID", "identifier": ident}]}]
            self.assertEqual(M._observe_rdap(self.rdap(entities=entities), "example.com")["registrar"], "")

    def test_registrar_without_public_ids(self):
        entities = [{"roles": ["registrar"]}, "junk", {"roles": "registrar"}]
        self.assertEqual(M._observe_rdap(self.rdap(entities=entities), "example.com")["registrar"], "")

    def test_not_found_body_is_not_an_error_but_not_ok(self):
        out = M._observe_rdap('{"errorCode": 404, "title": "Not Found"}', "example.com")
        self.assertEqual((out["err"], out["name_ok"], out["ns"], out["registrar"]), (False, False, [], ""))

    def test_garbage_is_error(self):
        for raw in ("", "<html>", "[]"):
            self.assertTrue(M._observe_rdap(raw, "example.com")["err"])

    def test_event_handling(self):
        events = [{"eventAction": "registration", "eventDate": "2016-01-01T00:00:00Z"},
                  {"eventAction": "registration", "eventDate": "2015-01-01T00:00:00Z"},
                  {"eventAction": "last changed", "eventDate": "2026-01-01T00:00:00Z"},
                  {"eventAction": "last changed", "eventDate": "2026-02-01T10:00:00+02:00"},
                  {"eventAction": "last changed", "eventDate": "yesterday"},
                  {"eventAction": "last update of RDAP database", "eventDate": "2030-01-01T00:00:00Z"}]
        out = M._observe_rdap(self.rdap(events=events), "example.com")
        self.assertEqual(out["registered"], "2015-01-01T00:00:00+00:00")
        self.assertEqual(out["changed"], "2026-02-01T08:00:00+00:00")


class TestHostHelpers(unittest.TestCase):
    def test_valid_hostname(self):
        for good in ("example.com", "a.b.c.example.co", "xn--bcher-kva.com", "ns1.buyer-dns.net"):
            self.assertTrue(M._valid_hostname(good), good)
        for bad in ("", "localhost", "a..b", "-a.com", "a-.com", "1.2.3.4", "a b.com", "A.com", "a.com/x", "_x.com"):
            self.assertFalse(M._valid_hostname(bad), bad)

    def test_evidence_host(self):
        self.assertEqual(M._evidence_host("https://dns.google/resolve?x=1"), "dns.google")
        self.assertEqual(M._evidence_host("http://dns.google/"), "")
        self.assertEqual(M._evidence_host("https://dns.google@evil.example/"), "")
        self.assertEqual(M._evidence_host("https://user:pw@dns.google/"), "")
        self.assertEqual(M._evidence_host("https://dns.google:99999/"), "")
        self.assertEqual(M._evidence_host("ftp://dns.google/"), "")
        self.assertEqual(M._evidence_host("not a url"), "")

    def test_host_allowed_exact_or_subdomain_only(self):
        allowed = ["dns.google"]
        self.assertTrue(M._host_allowed("dns.google", allowed))
        self.assertTrue(M._host_allowed("a.dns.google", allowed))
        self.assertFalse(M._host_allowed("notdns.google", allowed))
        self.assertFalse(M._host_allowed("dns.google.evil.example", allowed))
        self.assertFalse(M._host_allowed("", allowed))

    def test_registrable(self):
        self.assertEqual(M._registrable("a.b.example.com"), "example.com")
        self.assertEqual(M._registrable("dns.google"), "dns.google")

    def test_build_urls_use_only_anchored_hosts(self):
        urls = M._build_urls("example.com", "dns.google", "rdap.verisign.com")
        self.assertEqual(set(urls), {"txt", "rdap"})
        self.assertEqual(urls["txt"], "https://dns.google/resolve?name=_namedeed.example.com&type=TXT")
        self.assertEqual(urls["rdap"], "https://rdap.verisign.com/com/v1/domain/example.com")
        pir = M._build_urls("example.org", "dns.google", "rdap.publicinterestregistry.org")
        self.assertEqual(pir["rdap"], "https://rdap.publicinterestregistry.org/rdap/domain/example.org")

    def test_txt_value(self):
        self.assertEqual(M._txt_value('"abc"'), "abc")
        self.assertEqual(M._txt_value('"ab" "cd"'), "abcd")
        self.assertEqual(M._txt_value("  abc  "), "abc")
        self.assertEqual(M._txt_value(None), "")

    def test_as_int_rejects_bool_and_junk(self):
        self.assertEqual(M._as_int(True), -1)
        self.assertEqual(M._as_int("x"), -1)
        self.assertEqual(M._as_int("16"), 16)


if __name__ == "__main__":
    unittest.main()
