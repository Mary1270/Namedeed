"""
Shared test bootstrap: wires the offline genlayer SDK stub, loads the
contract (contract.py, or the file named by ND_CONTRACT), pins the clock,
and models the outside world the contract observes.

The World model is deliberately faithful to how DNS really works, because
the earlier design was wrong in exactly that respect:
- the registry holds the delegation (nameservers) and the sponsoring
  registrar, and RDAP reports those;
- whoever the registry delegates to runs the zone, so the TXT record at
  _namedeed.<domain> is answered by the seller's zone before a switch and
  by the buyer's zone after it;
- each zone also publishes its own apex NS records, which the operator can
  set to anything and which the contract must ignore.
"""
import contextlib
import datetime as _dt
import hashlib
import importlib.util
import json
import os
import sys
from unittest.mock import patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_STUB_DIR = os.path.join(_THIS_DIR, "genlayer_stub")
if _STUB_DIR not in sys.path:
    sys.path.insert(0, _STUB_DIR)

CONTRACT_PATH = os.environ.get("ND_CONTRACT") or os.path.join(os.path.dirname(_THIS_DIR), "contract.py")
_spec = importlib.util.spec_from_file_location("namedeed_contract", CONTRACT_PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

NameDeed = M.NameDeed
gl = M.gl
Address = M.Address
u256 = M.u256

NameDeed.MIN_STAKE = u256(1)
NameDeed.MIN_JUROR_STAKE = u256(1)
NameDeed.MIN_APPEAL_BOND = u256(1)

UTC = _dt.timezone.utc
BASE = _dt.datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
HOUR = 3600
DAY = 24 * HOUR
MIN = 60


class _Clock:
    def __init__(self):
        self.t = BASE

    def reset(self):
        self.t = BASE

    def advance(self, seconds):
        self.t = self.t + _dt.timedelta(seconds=seconds)

    def set(self, when):
        self.t = when


CLOCK = _Clock()
NameDeed._now_utc = staticmethod(lambda: CLOCK.t)

BUYER = "0x" + "11" * 20
SELLER = "0x" + "22" * 20
STRANGER = "0x" + "33" * 20
JURORS = ["0x" + hex(0xA0 + i)[2:].rjust(2, "0") * 20 for i in range(12)]

DOMAIN = "example.com"
NS = ["ns1.buyer-dns.net", "ns2.buyer-dns.net"]
SELLER_NS = ["ns1.seller-host.net", "ns2.seller-host.net"]
OTHER_NS = ["ns1.third-party.net", "ns2.third-party.net"]
SELLER_REGISTRAR = "1001"
BUYER_REGISTRAR = "2002"
OTHER_REGISTRAR = "3003"
HOSTS = "dns.google,rdap.verisign.com"
PRICE = 1000
BOND = 400


def iso(when):
    return when.astimezone(UTC).isoformat()


def parse(text):
    return _dt.datetime.fromisoformat(text)


def set_caller(address):
    gl.message.sender_address = Address(address)


def reset_transfers():
    gl.evm.transfers.clear()


class Rejected(Exception):
    """A payable call the contract refused without reverting: it returned
    {"rejected": true, ...} and credited the GEN back to the sender."""

    def __init__(self, result):
        super().__init__(result.get("reason", ""))
        self.result = result


def call_payable(contract, method, value, *args, **kwargs):
    """Call a payable method with `value` wei attached. The GEN stays in the
    contract balance. A rejection (a normal return) is raised as `Rejected`
    so a test can still assert that a call did not succeed; the refund is
    then visible in the sender's pending balance."""
    gl.message.value = u256(value)
    contract.balance = contract.balance + u256(value)
    try:
        out = getattr(contract, method)(*args, **kwargs)
    except Exception:
        contract.balance = contract.balance - u256(value)
        raise
    finally:
        gl.message.value = u256(0)
    if isinstance(out, str) and out.startswith("{"):
        try:
            parsed = json.loads(out)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and parsed.get("rejected") is True:
            raise Rejected(parsed)
    return out


def deliver_transfers(contract, deliver=True):
    """Model the runtime executing the transfers the contract emitted: the
    contract balance drops by each amount (or, with deliver=False, the
    transfers fail and the balance stays). The emitted list is cleared."""
    for item in list(gl.evm.transfers):
        if deliver:
            contract.balance = contract.balance - item["value"]
    gl.evm.transfers.clear()


def make_contract():
    CLOCK.reset()
    reset_transfers()
    gl.message.value = u256(0)
    return NameDeed()


def dns_json(name, rtype, status=0, answers=None, ttl=300):
    body = {"Status": status, "TC": False, "RD": True, "RA": True, "AD": False, "CD": False,
            "Question": [{"name": name + ".", "type": rtype}]}
    if answers:
        body["Answer"] = [{"name": a[0], "type": a[1], "TTL": ttl, "data": a[2]} for a in answers]
    return json.dumps(body)


def rdap_json(domain, nameservers, registrar, registered="2015-03-01T00:00:00Z", changed="2026-05-01T00:00:00Z"):
    events = [{"eventAction": "registration", "eventDate": registered},
              {"eventAction": "expiration", "eventDate": "2030-03-01T00:00:00Z"},
              {"eventAction": "last changed", "eventDate": changed},
              {"eventAction": "last update of RDAP database", "eventDate": "2030-01-01T00:00:00Z"}]
    return json.dumps({
        "objectClassName": "domain", "ldhName": domain.upper(), "events": events,
        "nameservers": [{"objectClassName": "nameserver", "ldhName": h.upper()} for h in nameservers],
        "entities": [{"objectClassName": "entity", "roles": ["registrar"],
                      "publicIds": [{"type": "IANA Registrar ID", "identifier": registrar}],
                      "entities": [{"roles": ["abuse"]}]}],
    })


class World:
    """Registry, two zones, and the model used by the Tier-2 gate."""

    def __init__(self, domain=DOMAIN, token=""):
        self.domain = domain
        self.token = token
        self.registry_ns = list(SELLER_NS)
        self.registrar = SELLER_REGISTRAR
        self.rdap_name = domain
        self.zones = {
            "seller": {"txt": ["v=spf1 -all"], "apex_ns": list(SELLER_NS)},
            "buyer": {"txt": [], "apex_ns": list(NS)},
            "other": {"txt": [], "apex_ns": list(OTHER_NS)},
        }
        self.txt_status_override = None
        self.fail = set()
        self.garbage = set()
        self.calls = []
        self.ttl = 300
        self.plausible = True
        self.llm_calls = 0
        self.prompts = []

    def operator(self):
        delegated = sorted(self.registry_ns)
        if delegated == sorted(NS):
            return "buyer"
        if delegated == sorted(SELLER_NS):
            return "seller"
        return "other"

    def seller_publishes(self, value=None):
        self.zones["seller"]["txt"].append(self.token if value is None else value)

    def seller_unpublishes(self):
        self.zones["seller"]["txt"] = [v for v in self.zones["seller"]["txt"] if v != self.token]

    def buyer_publishes(self, value=None):
        self.zones["buyer"]["txt"].append(self.token if value is None else value)

    def switch_delegation(self, servers=None):
        self.registry_ns = list(NS if servers is None else servers)

    def transfer_registrar(self, registrar=BUYER_REGISTRAR):
        self.registrar = registrar

    def render(self, url, mode="text"):
        self.calls.append(url)
        kind = "txt" if "type=TXT" in url else "rdap"
        if kind in self.fail:
            raise RuntimeError("fetch failed")
        if kind in self.garbage:
            return "<html>not json</html>"
        self.ttl += 1
        if kind == "txt":
            qname = f"{M.CHALLENGE_LABEL}.{self.domain}"
            values = self.zones[self.operator()]["txt"]
            status = 0 if values else 3
            if self.txt_status_override is not None:
                status = self.txt_status_override
            answers = [(qname + ".", 16, v) for v in values] if status == 0 else None
            return dns_json(qname, 16, status, answers, self.ttl)
        return rdap_json(self.rdap_name, self.registry_ns, self.registrar)

    def exec_prompt(self, prompt, response_format="json"):
        self.llm_calls += 1
        self.prompts.append(prompt)
        if isinstance(self.plausible, Exception):
            raise self.plausible
        if isinstance(self.plausible, str):
            return self.plausible
        return {"plausible": self.plausible}

    @contextlib.contextmanager
    def active(self):
        with patch.object(gl.nondet.web, "render", side_effect=self.render), \
             patch.object(gl.nondet, "exec_prompt", side_effect=self.exec_prompt):
            yield self


def create(c, price=PRICE, bond=BOND, deadline_in=6 * HOUR, gap_hours=1, domain=DOMAIN, nameservers=None,
           hosts=HOSTS, terms="Move the domain to the buyer as agreed", registrar="", seller=SELLER, caller=BUYER):
    set_caller(caller)
    ns_csv = ",".join(NS) if nameservers is None else nameservers
    return call_payable(c, "create_deed", price, seller, domain,
                        iso(CLOCK.t + _dt.timedelta(seconds=deadline_in)), ns_csv, hosts, terms,
                        str(bond), registrar, gap_hours)


def accept(c, deed_id, bond=BOND, caller=SELLER):
    set_caller(caller)
    return call_payable(c, "accept_deed", bond, deed_id)


def active_deed(c, **kw):
    deed_id = create(c, **kw)
    accept(c, deed_id, kw.get("bond", BOND))
    return deed_id


def deed_of(c, deed_id):
    return json.loads(c.get_deed(deed_id))


def world_for(c, deed_id):
    deed = deed_of(c, deed_id)
    return World(domain=deed["domain"], token=deed["challenge_token"])


def observe(c, deed_id, world, caller=SELLER):
    set_caller(caller)
    with world.active():
        return c.observe(deed_id)


def rule(c, deed_id, world, caller=STRANGER):
    set_caller(caller)
    with world.active():
        return c.issue_ruling(deed_id)


def honest_handover(c, transfer=False, **kw):
    """An honest seller: publish the token, observe, hand over, observe,
    wait the gap, rule. Returns (deed_id, world)."""
    if transfer:
        kw.setdefault("registrar", BUYER_REGISTRAR)
    deed_id = active_deed(c, **kw)
    world = world_for(c, deed_id)
    world.seller_publishes()
    CLOCK.advance(10 * MIN)
    observe(c, deed_id, world)
    if transfer:
        world.transfer_registrar()
        world.switch_delegation()
    else:
        world.switch_delegation()
    CLOCK.advance(10 * MIN)
    observe(c, deed_id, world)
    CLOCK.advance(HOUR)
    rule(c, deed_id, world)
    return deed_id, world


def seller_never_acts(c, **kw):
    """The buyer observes nothing published, then rules after the deadline."""
    deed_id = active_deed(c, **kw)
    world = world_for(c, deed_id)
    CLOCK.advance(10 * MIN)
    observe(c, deed_id, world, caller=BUYER)
    CLOCK.advance(7 * HOUR)
    rule(c, deed_id, world, caller=BUYER)
    return deed_id, world


def control_without_switch(c, **kw):
    """The seller proves control twice in time but never hands over."""
    deed_id = active_deed(c, **kw)
    world = world_for(c, deed_id)
    world.seller_publishes()
    CLOCK.advance(10 * MIN)
    observe(c, deed_id, world)
    CLOCK.advance(HOUR + MIN)
    observe(c, deed_id, world)
    CLOCK.advance(6 * HOUR)
    rule(c, deed_id, world)
    return deed_id, world


def close_window(c, deed_id):
    CLOCK.set(parse(deed_of(c, deed_id)["appeal_deadline"]) + _dt.timedelta(seconds=1))


def age_jurors(c):
    for key in list(c.jurors.keys()):
        rec = json.loads(c.jurors[key])
        rec["registered_at"] = "2000-01-01T00:00:00+00:00"
        rec["stake_updated_at"] = "2000-01-01T00:00:00+00:00"
        c.jurors[key] = json.dumps(rec, sort_keys=True)


def seat_panel(c, deed_id, beacon="fixed-test-beacon"):
    deed = deed_of(c, deed_id)
    CLOCK.set(parse(deed["appeal_deadline"]) + _dt.timedelta(seconds=120))
    round_no = deed["appeal"]["draw_round"]
    with patch.object(gl.nondet.web, "render",
                      return_value=json.dumps({"round": round_no, "randomness": beacon})):
        c.seat_panel(deed_id)


def commit_hash(vote, salt, deed_id, juror):
    return hashlib.sha256(f"{vote}:{salt}:{str(juror).lower()}:{deed_id}".encode()).hexdigest()


def tamper(c, deed_id, mutate):
    deed = json.loads(c.deeds[deed_id])
    mutate(deed)
    c.deeds[deed_id] = json.dumps(deed, sort_keys=True)


def pending(c, who):
    return int(c.get_pending_withdrawal(who))
