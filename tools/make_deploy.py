"""Build contract_deploy.py from contract.py.

GenLayer Studio can reject a contract file that carries long comments or
docstring blocks, so the deploy file keeps exactly the two header lines and
then code. Docstrings are located with `ast` and comments with `tokenize`;
no regular expressions are involved. Removed text is replaced by blank
lines (or `pass` where a docstring was a whole body), so every line of code
keeps its line number and an error reported by Studio points at the same
line in contract.py.

Usage:
  python3 tools/make_deploy.py [source] [target]
  python3 tools/make_deploy.py --short
  python3 tools/make_deploy.py --evidence

`--short` writes contract_short_deploy.py, a test-only variant with
minute-scale windows (and "hours" between observations counted as minutes)
so the whole lifecycle, including the jury path, can be exercised live in a
few minutes. Never use it with real funds.

`--evidence` writes contract_evidence_deploy.py, a test-only variant with the
short windows AND a synthetic evidence source: instead of reading DNS and
RDAP, an observation reads a fixture that anyone may set with
`set_fixture`. Everything after the observation (facts, ruling, appeal, jury,
plausibility gate, settlement, payouts) is the same code as the production
contract. It exists only to show, live, outcomes that need real registry
changes (a partial handover, a jury reversal). It reports
EVIDENCE_SOURCE = "SYNTHETIC FIXTURE" and must never hold real funds.
"""
import ast
import io
import os
import sys
import tokenize

HEADER_LINES = 2

SHORT_WINDOWS = {
    "MIN_DEADLINE_LEAD_SECONDS": 120,
    "EVIDENCE_WINDOW_SECONDS": 900,
    "APPEAL_WINDOW_SECONDS": 240,
    "COMMIT_WINDOW_SECONDS": 240,
    "REVEAL_WINDOW_SECONDS": 240,
    "RESOLUTION_TIMEOUT_SECONDS": 1200,
    "GAP_UNIT_SECONDS": 60,
    "JUROR_MIN_AGE_SECONDS": 60,
}


FIXTURE_PATCHES = [
    (
        "def _take_observation(ctx: dict) -> dict:\n",
        "def _fixture_observation(ctx: dict) -> dict:\n"
        "    fx = _safe_json_obj(ctx[\"fixture\"])\n"
        "    seen = fx.get(\"seller_token\") is True\n"
        "    proof = fx.get(\"buyer_proof\") is True\n"
        "    servers = sorted(_norm_host(h) for h in str(fx.get(\"nameservers\", \"\")).split(\",\") if _norm_host(h))\n"
        "    return {\n"
        "        \"txt\": {\"err\": False, \"status\": 0 if seen else 3, \"token\": seen},\n"
        "        \"btxt\": {\"err\": False, \"status\": 0 if proof else 3, \"token\": proof},\n"
        "        \"rdap\": {\"err\": False, \"name_ok\": True, \"ns\": servers, \"registered\": \"\", \"changed\": \"\"},\n"
        "    }\n"
        "\n\n"
        "def _take_observation(ctx: dict) -> dict:\n"
        "    if ctx.get(\"fixture\"):\n"
        "        return _fixture_observation(ctx)\n"
        "    return _take_real_observation(ctx)\n"
        "\n\n"
        "def _take_real_observation(ctx: dict) -> dict:\n",
    ),
    (
        "            \"buyer_hash\": deed[\"buyer_proof_hash\"],\n",
        "            \"buyer_hash\": deed[\"buyer_proof_hash\"],\n"
        "            \"fixture\": self.fixtures.get(deed[\"domain\"], \"\"),\n",
    ),
    (
        "    in_flight: str\n",
        "    in_flight: str\n    fixtures: TreeMap[str, str]\n    EVIDENCE_SOURCE = \"SYNTHETIC FIXTURE - NOT REAL REGISTRY DATA\"\n",
    ),
    (
        "    @gl.public.write\n    def observe(self, deed_id: str) -> str:\n",
        "    @gl.public.write\n"
        "    def set_fixture(self, domain: str, nameservers: str, seller_token: bool, buyer_proof: bool) -> str:\n"
        "        value = json.dumps({\"nameservers\": nameservers, \"seller_token\": bool(seller_token),\n"
        "                            \"buyer_proof\": bool(buyer_proof)}, sort_keys=True)\n"
        "        self.fixtures[domain] = value\n"
        "        return value\n"
        "\n"
        "    @gl.public.view\n"
        "    def evidence_source(self) -> str:\n"
        "        return self.EVIDENCE_SOURCE\n"
        "\n"
        "    @gl.public.write\n    def observe(self, deed_id: str) -> str:\n",
    ),
]


def apply_fixture(source: str) -> str:
    for old, new in FIXTURE_PATCHES:
        if source.count(old) != 1:
            raise SystemExit(f"fixture patch target not found exactly once: {old[:50]!r}")
        source = source.replace(old, new)
    return source


def _is_docstring(node):
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def strip(source: str, overrides=None) -> str:
    lines = source.split("\n")
    tree = ast.parse(source)
    new = {}

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.body or not _is_docstring(node.body[0]):
            continue
        doc = node.body[0]
        if lines[doc.lineno - 1][: doc.col_offset].strip() or lines[doc.end_lineno - 1][doc.end_col_offset:].strip():
            raise SystemExit(f"docstring at line {doc.lineno} shares its line with code")
        for number in range(doc.lineno, doc.end_lineno + 1):
            new[number] = ""
        if len(node.body) == 1:
            new[doc.lineno] = " " * doc.col_offset + "pass"

    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT and tok.start[0] > HEADER_LINES:
            row, col = tok.start
            if row not in new:
                new[row] = lines[row - 1][:col].rstrip()

    if overrides:
        seen = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                        and isinstance(stmt.targets[0], ast.Name) and stmt.targets[0].id in overrides:
                    value = stmt.value
                    if value.lineno != value.end_lineno:
                        raise SystemExit(f"constant {stmt.targets[0].id} spans several lines")
                    line = new.get(value.lineno, lines[value.lineno - 1])
                    new[value.lineno] = (
                        line[: value.col_offset] + repr(overrides[stmt.targets[0].id]) + line[value.end_col_offset:]
                    )
                    seen.add(stmt.targets[0].id)
        missing = set(overrides) - seen
        if missing:
            raise SystemExit(f"constants not found: {sorted(missing)}")

    out = [new.get(number, text).rstrip() for number, text in enumerate(lines, start=1)]
    result = "\n".join(out).rstrip("\n") + "\n"
    ast.parse(result)
    return result


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    short = "--short" in sys.argv[1:]
    evidence = "--evidence" in sys.argv[1:]
    src = args[0] if len(args) > 0 else os.path.join(root, "contract.py")
    default_dst = "contract_evidence_deploy.py" if evidence else "contract_short_deploy.py" if short else "contract_deploy.py"
    dst = args[1] if len(args) > 1 else os.path.join(root, default_dst)
    with open(src, encoding="utf-8") as handle:
        text = handle.read()
        if evidence:
            text = apply_fixture(text)
        out = strip(text, SHORT_WINDOWS if (short or evidence) else None)
    with open(dst, "w", encoding="utf-8") as handle:
        handle.write(out)
    print(f"wrote {dst} ({len(out.splitlines())} lines)")


if __name__ == "__main__":
    main()
