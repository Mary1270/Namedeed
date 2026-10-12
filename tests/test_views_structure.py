import ast
import io
import json
import os
import sys
import tokenize
import unittest

from _bootstrap import M, make_contract, create, active_deed, honest_handover, deed_of, CLOCK

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import make_deploy  # noqa: E402

HEADER = [
    "# v0.2.16",
    '# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }',
]


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as handle:
        return handle.read()


def without_docstrings(source):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body \
                and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) \
                and isinstance(node.body[0].value.value, str):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.dump(tree)


class TestListView(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_empty(self):
        self.assertEqual(json.loads(self.c.list_deeds()), {"items": [], "offset": 0, "total": 0})

    def test_newest_first_with_summaries(self):
        for i in range(3):
            create(self.c, domain=f"site{i}.com")
        page = json.loads(self.c.list_deeds())
        self.assertEqual([d["deed_id"] for d in page["items"]], ["deed_2", "deed_1", "deed_0"])
        self.assertEqual(set(page["items"][0]), {"deed_id", "status", "domain", "mode", "buyer", "seller", "price",
                                                 "seller_bond", "deadline", "created_at", "ruling", "final_outcome"})

    def test_paging_and_clamping(self):
        for i in range(5):
            create(self.c, domain=f"site{i}.com")
        self.assertEqual([d["deed_id"] for d in json.loads(self.c.list_deeds(2, 2))["items"]], ["deed_2", "deed_1"])
        self.assertEqual(json.loads(self.c.list_deeds(10, 5))["items"], [])
        self.assertEqual(len(json.loads(self.c.list_deeds(0, 0))["items"]), 1)
        self.assertEqual(len(json.loads(self.c.list_deeds(-3, 10_000))["items"]), 5)

    def test_summary_tracks_ruling(self):
        honest_handover(self.c)
        item = json.loads(self.c.list_deeds())["items"][0]
        self.assertEqual((item["status"], item["ruling"], item["final_outcome"]), ("ruled", "HANDOVER_COMPLETE", None))

    def test_other_views(self):
        with self.assertRaises(Exception):
            self.c.get_deed("deed_5")
        self.assertEqual(self.c.get_pending_withdrawal("0x" + "99" * 20), "0")
        self.assertEqual(json.loads(self.c.get_juror("0x" + "99" * 20))["stake"], "0")
        self.assertEqual(self.c.get_contract_balance(), "0")
        with self.assertRaises(Exception):
            self.c.verify_jury_selection(active_deed(self.c))


class TestDeployFile(unittest.TestCase):
    def test_header_is_exactly_two_lines_then_code(self):
        lines = read("contract_deploy.py").split("\n")
        self.assertEqual(lines[:2], HEADER)
        self.assertEqual(lines[2], "from genlayer import *")
        self.assertEqual(read("contract.py").split("\n")[:2], HEADER)

    def test_only_comments_are_the_header(self):
        src = read("contract_deploy.py")
        comments = [t.string for t in tokenize.generate_tokens(io.StringIO(src).readline) if t.type == tokenize.COMMENT]
        self.assertEqual(comments, HEADER)

    def test_no_docstrings(self):
        tree = ast.parse(read("contract_deploy.py"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body:
                first = node.body[0]
                self.assertFalse(isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                                 and isinstance(first.value.value, str), getattr(node, "name", "module"))

    def test_is_up_to_date(self):
        self.assertEqual(read("contract_deploy.py"), make_deploy.strip(read("contract.py")))

    def test_same_logic_as_the_documented_source(self):
        self.assertEqual(without_docstrings(read("contract.py")), without_docstrings(read("contract_deploy.py")))

    def test_every_code_line_keeps_its_line_number(self):
        src = read("contract.py").split("\n")
        dep = read("contract_deploy.py").split("\n")
        self.assertEqual(len(src), len(dep))
        for number, (a, b) in enumerate(zip(src, dep), start=1):
            if b.strip() and b.strip() != "pass":
                self.assertTrue(a.startswith(b), f"line {number}: {b!r}")

    def test_parses_without_newer_syntax(self):
        ast.parse(read("contract_deploy.py"), feature_version=(3, 11))

    def test_short_variant_changes_only_window_constants(self):
        base = make_deploy.strip(read("contract.py")).split("\n")
        short = make_deploy.strip(read("contract.py"), make_deploy.SHORT_WINDOWS).split("\n")
        diff = [(a, b) for a, b in zip(base, short) if a != b]
        self.assertEqual(len(diff), len(make_deploy.SHORT_WINDOWS))
        for old, new in diff:
            self.assertEqual(old.split("=")[0], new.split("=")[0])
        self.assertEqual(read("contract_short_deploy.py").split("\n"), short)

    def test_builder_refuses_unknown_constants(self):
        with self.assertRaises(SystemExit):
            make_deploy.strip(read("contract.py"), {"NO_SUCH_CONSTANT": 1})


class TestGenLayerRules(unittest.TestCase):
    """Rules from earlier GenLayer submissions, checked on the AST."""

    def setUp(self):
        self.source = read("contract.py")
        self.tree = ast.parse(self.source)
        self.cls = [n for n in self.tree.body if isinstance(n, ast.ClassDef) and n.name == "NameDeed"][0]
        self.methods = {n.name: n for n in self.cls.body if isinstance(n, ast.FunctionDef)}

    def decorated(self, word):
        out = []
        for name, node in self.methods.items():
            for deco in node.decorator_list:
                if word in ast.unparse(deco):
                    out.append(name)
        return out

    def test_storage_is_declared_as_class_annotations(self):
        names = {n.target.id for n in self.cls.body if isinstance(n, ast.AnnAssign)}
        self.assertTrue({"deeds", "deed_count", "jurors", "pending_withdrawals"} <= names)

    def test_init_never_assigns_empty_containers(self):
        for node in ast.walk(self.methods["__init__"]):
            if isinstance(node, ast.Assign):
                self.assertNotIsInstance(node.value, (ast.Dict, ast.List))

    def test_no_custom_stored_classes(self):
        self.assertEqual(sorted(n.name for n in self.tree.body if isinstance(n, ast.ClassDef)), ["NameDeed", "_Payee"])

    def test_nondet_calls_take_two_positional_functions(self):
        calls = [n for n in ast.walk(self.tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "run_nondet_unsafe"]
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertEqual((len(call.args), call.keywords), (2, []))
            self.assertTrue(all(isinstance(a, ast.Name) for a in call.args))

    def test_validators_unwrap_return_calldata(self):
        validators = [n for n in ast.walk(self.cls) if isinstance(n, ast.FunctionDef) and n.name == "_validator"]
        self.assertEqual(len(validators), 2)
        for fn in validators:
            text = ast.unparse(fn)
            self.assertIn("isinstance(leader_result, gl.vm.Return)", text)
            self.assertIn("leader_result.calldata", text)

    def test_every_status_changing_write_checks_status_first(self):
        exempt = {"register_juror", "unstake_juror", "withdraw", "create_deed"}
        for name in self.decorated("write"):
            if name in exempt:
                continue
            compares = [n for n in ast.walk(self.methods[name]) if isinstance(n, ast.Compare)
                        and isinstance(n.left, ast.Subscript) and isinstance(n.left.slice, ast.Constant)
                        and n.left.slice.value == "status"]
            self.assertTrue(compares, name)

    def test_sensitive_writes_check_the_caller(self):
        for name in ("accept_deed", "cancel_deed", "observe", "contest", "commit_vote", "reveal_vote", "withdraw",
                     "unstake_juror"):
            text = ast.unparse(self.methods[name])
            self.assertTrue("sender_address" in text or "_require_party" in text, name)

    def test_time_and_sender_sources(self):
        self.assertIn("datetime.datetime.now(datetime.timezone.utc)", self.source)
        self.assertIn("gl.message.sender_address", self.source)
        self.assertIn("emit_transfer(value=amount)", self.source)

    def test_no_floats(self):
        for node in ast.walk(self.tree):
            self.assertFalse(isinstance(node, ast.Constant) and isinstance(node.value, float))
            self.assertFalse(isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div))

    def test_tier_one_has_no_model_call(self):
        for name in ("issue_ruling", "observe", "_observe_now"):
            self.assertNotIn("exec_prompt", ast.unparse(self.methods[name]))
            self.assertNotIn("_ask_model", ast.unparse(self.methods[name]))

    def test_outcome_vocabulary(self):
        self.assertEqual(M.VALID_OUTCOMES, ("HANDOVER_COMPLETE", "PARTIAL_HANDOVER", "HANDOVER_FAILED", "UNDETERMINED"))


if __name__ == "__main__":
    unittest.main()
