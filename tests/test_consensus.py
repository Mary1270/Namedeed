import ast
import inspect
import textwrap
import unittest
from unittest.mock import patch

from _bootstrap import (
    M, CLOCK, BUYER, SELLER, HOUR, MIN, gl,
    make_contract, set_caller, active_deed, deed_of, world_for, observe, rule,
)


def core(token=True, ns=("a.b.net", "c.b.net"), proof=True):
    return {"txt": {"err": False, "status": 0, "token": token},
            "btxt": {"err": False, "status": 0, "token": proof},
            "rdap": {"err": False, "name_ok": True, "ns": list(ns),
                     "registered": "2015-03-01T00:00:00+00:00", "changed": "2026-05-01T00:00:00+00:00"}}


class TestAgree(unittest.TestCase):
    def test_identical_cores_agree(self):
        self.assertTrue(M._observe_agree({"core": core()}, {"core": core()}))

    def test_any_decisive_difference_disagrees(self):
        for other in (core(token=False), core(ns=("x.y.net", "z.y.net")), core(proof=False)):
            self.assertFalse(M._observe_agree({"core": core()}, {"core": other}))

    def test_non_dicts_disagree(self):
        self.assertFalse(M._observe_agree(None, {"core": core()}))
        self.assertFalse(M._observe_agree({"core": core()}, "x"))


class TestValidatorDrift(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def drifting(self, world, calls_before, **changes):
        original = world.render
        count = {"n": 0}

        def render(url, mode="text"):
            count["n"] += 1
            if count["n"] == calls_before + 1:
                for key, value in changes.items():
                    if key == "registry_ns":
                        world.switch_delegation(value)
                    else:
                        setattr(world, key, value)
            return original(url, mode)
        return render

    def test_observation_fails_when_the_validator_sees_another_delegation(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        set_caller(SELLER)
        with patch.object(gl.nondet.web, "render", side_effect=self.drifting(world, 2, registry_ns=["x.y.net", "z.y.net"])):
            with self.assertRaises(Exception):
                self.c.observe(deed_id)
        self.assertEqual(deed_of(self.c, deed_id)["observations"], [])

    def test_ttl_drift_does_not_matter(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        world.seller_publishes()
        observe(self.c, deed_id, world)
        self.assertGreater(world.ttl, 301)
        self.assertEqual(len(deed_of(self.c, deed_id)["observations"]), 1)

    def test_validator_rejects_a_non_return_value(self):
        deed_id = active_deed(self.c)
        world = world_for(self.c, deed_id)
        captured = {}

        def fake(leader, validator):
            captured["validator"] = validator
            return leader()
        set_caller(SELLER)
        with patch.object(gl.vm, "run_nondet_unsafe", side_effect=fake), world.active():
            self.c.observe(deed_id)
        self.assertFalse(captured["validator"]("not a Return"))


class TestObservationGuards(unittest.TestCase):
    def ctx(self, **urls):
        base = {"txt": "https://dns.google/x", "btxt": "https://dns.google/y", "rdap": "https://rdap.verisign.com/x"}
        base.update(urls)
        return {"domain": "example.com", "token": "t", "buyer_hash": "h", "dns_host": "dns.google", "rdap_host": "rdap.verisign.com", "urls": base}

    def test_unlisted_or_plain_http_hosts_are_never_fetched(self):
        for bad in ({"txt": "https://evil.example/x"}, {"rdap": "http://rdap.verisign.com/x"},
                    {"txt": "https://dns.google@evil.example/"}):
            calls = []
            with patch.object(gl.nondet.web, "render", side_effect=lambda u, mode="text": calls.append(u) or "{}"):
                self.assertTrue(M._obs_err(M._take_observation(self.ctx(**bad))))
            self.assertEqual(calls, [])

    def test_obs_err_handles_malformed_core(self):
        self.assertTrue(M._obs_err({}))
        self.assertTrue(M._obs_err(None))
        self.assertFalse(M._obs_err(core()))

    def test_fetch_text_swallows_errors_and_non_strings(self):
        with patch.object(gl.nondet.web, "render", side_effect=RuntimeError("boom")):
            self.assertEqual(M._fetch_text("https://dns.google/x"), "")
        with patch.object(gl.nondet.web, "render", return_value=None):
            self.assertEqual(M._fetch_text("https://dns.google/x"), "")

    def test_ask_model_swallows_errors(self):
        with patch.object(gl.nondet, "exec_prompt", side_effect=RuntimeError("down")):
            self.assertEqual(M._ask_model("p"), {})
        with patch.object(gl.nondet, "exec_prompt", return_value="not json"):
            self.assertEqual(M._ask_model("p"), {})

    def test_plausibility_requires_literal_true(self):
        ctx = {"deed": {"domain": "example.com", "handover_terms": "t"}, "facts": {}, "default": "A", "candidate": "B"}
        for answer, expected in (({"plausible": True}, True), ({"plausible": "true"}, False),
                                 ({"plausible": 1}, False), ({}, False), ("garbage", False)):
            with patch.object(gl.nondet, "exec_prompt", return_value=answer):
                self.assertEqual(M._judge_plausible(ctx)["plausible"], expected)


class TestClosuresHoldNoContract(unittest.TestCase):
    def test_nested_functions_never_reference_self(self):
        for name in ("_observe_now", "_verify_majority_against_evidence"):
            tree = ast.parse(textwrap.dedent(inspect.getsource(getattr(M.NameDeed, name))))
            method = tree.body[0]
            nested = [n for n in ast.walk(method) if isinstance(n, ast.FunctionDef) and n is not method]
            self.assertTrue(nested, name)
            for fn in nested:
                names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
                self.assertNotIn("self", names, f"{name}.{fn.name}")


if __name__ == "__main__":
    unittest.main()
