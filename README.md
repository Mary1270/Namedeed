# NameDeed

Escrow and dispute resolution for the sale of a domain name, built as a single GenLayer Intelligent Contract. The buyer escrows the price and the seller posts a bond. The contract reads the domain registry (RDAP) and public DNS to decide, in code, whether the domain really changed hands. A staked jury can review a ruling, but only within what the registry data proves.

## How a deal works

1. The buyer opens a deed: domain, deadline, price, seller bond, and either the buyer's registrar ID (registrar transfer) or the nameservers the domain must move to.
2. The seller accepts by posting the bond. The contract derives a challenge token for this deed.
3. The seller publishes the token as a TXT record at `_namedeed.<domain>` and takes an observation. This proves control.
4. The seller transfers the domain or changes its nameservers, then takes another observation.
5. One gap after the latest observation, anyone issues the ruling. Either party may appeal; payouts are pulled with `withdraw`.

## What makes it hold up

- **The handover is read from the registry, not from DNS zone data.** Transfer mode checks the sponsoring registrar's IANA ID in RDAP; delegation mode checks the registry delegation in RDAP. Neither party can fake these by editing a zone.
- **The token proves control only.** After a switch the buyer runs the zone, so the handover never depends on a record the buyer could withhold.
- **Two-observation stability rule.** A handover or a control proof counts only when seen in observations at least N hours apart. A ruling can only run one gap after the latest observation, so a change flashed for one observation never wins and nobody can rule before a fresh handover matures.
- **Deterministic Tier 1.** The ruling is computed from the facts in code. No model, and no party-written text, influences it.
- **Backstop.** `HANDOVER_COMPLETE` is impossible unless every structured check passes; anything the facts do not allow becomes `UNDETERMINED`.
- **Bounded Tier 2.** A jury may only choose outcomes the frozen facts allow (for example, downgrade a completion over off-chain terms). Any departure from the code default must also pass a model plausibility check over the frozen facts. Tier 2 never re-fetches.
- **Fair money.** Price and bond are separate amounts. Payouts follow the outcome and whether the buyer already holds the domain.
- **Fair jury.** Jurors must be registered at least seven days before the deed was accepted. The pool is frozen at appeal and drawn from a future drand round; commits are bound to juror and deed.
- **Never stuck.** Every state has a permissionless exit that pays out or refunds.

Outcomes: `HANDOVER_COMPLETE`, `PARTIAL_HANDOVER`, `HANDOVER_FAILED`, `UNDETERMINED`. See `ARCHITECTURE.md` for the data-control table, payout table, facts, recovery paths and disclosed limitations.

## Files

| File | Purpose |
| --- | --- |
| `contract.py` | The documented source |
| `contract_deploy.py` | Same code without comments or docstrings, line numbers unchanged. Deploy this file to GenLayer Studio |
| `contract_short_deploy.py` | Test-only variant with minute-scale windows for trying the full lifecycle live. Never use it with real funds |
| `tools/make_deploy.py` | Builds both deploy files using `ast` and `tokenize` |
| `index.html` | Single-page dApp: explore, open a deed, deed page with contextual actions, jury, balance |
| `tests/` | Offline unit tests, the SDK stub and the registry and DNS model |
| `.github/workflows/tests.yml` | Runs the suite against both contract files and checks the deploy file is current |

## Tests

Offline unit tests against a self-written `genlayer` SDK stub, with a pinned clock and a model of the registry and the two zones (seller's and buyer's). No `pip install` is needed.

```
python3 -m unittest discover -s tests -v
ND_CONTRACT=contract_deploy.py python3 -m unittest discover -s tests -v
```

`test_adversarial.py` plays the buyer and the seller as attackers. Covered attacks include:

- withholding the token after the switch;
- forging apex NS records;
- flashing a delegation or a token;
- ruling early;
- moving the domain away after a transfer;
- replaying another deed's token;
- delegating to a third party.

The rest of the suite covers input validation, parsers built against real RDAP and dns.google responses, the stability rule and every fact, consensus agreement and drift, settlement, evidence digests, the jury and the plausibility gate, recovery paths, and the rules the deploy file must follow.

## The dApp

`index.html` uses `genlayer-js` from esm.sh and `createClient({ chain: studionet, account })`. Hash routes: `#/` (explore), `#/create`, `#/deed/<id>`, `#/jury`, `#/withdraw`. Set the contract address in `DEFAULT_CONTRACT` at the top of the script, or save it from the wallet sheet in the page (kept in that browser only).

## Live deployment

| Contract | Address |
| --- | --- |
| Production (`contract_deploy.py`) | [`0x3580ef2c572FdA4828531cE035318F7fEAD4131b`](https://explorer-studio.genlayer.com/address/0x3580ef2c572FdA4828531cE035318F7fEAD4131b) |
| Short-window test (`contract_short_deploy.py`) | [`0x4C6Ae6369b8D38FFf2e70fd7518f5084736bFB4b`](https://explorer-studio.genlayer.com/address/0x4C6Ae6369b8D38FFf2e70fd7518f5084736bFB4b) |

## Live test on GenLayer Studio

A full lifecycle was run on the short-window contract with real registry and DNS data (`google.com`, transfer mode, buyer registrar IANA ID `292`). Every call reached validator consensus.

| Step | Result |
| --- | --- |
| Five jurors register (1 GEN each) | Success |
| `create_deed` (price 5 GEN) and `accept_deed` (bond 1 GEN) | Success |
| `observe` by the seller | Real RDAP data frozen: registrar `292`, registry nameservers `ns1`–`ns4.google.com`. dns.google answered `NXDOMAIN` for the challenge name |
| `issue_ruling` 60 seconds later | Two observations agree, so `complete_proven` is true. Deterministic ruling: `HANDOVER_COMPLETE` |
| `contest` by the buyer (1 GEN bond) | Success |
| `seat_panel` | Jury drawn from drand round `6548464`; `verify_jury_selection` replays it (`match: true`) |
| `commit_vote` / `reveal_vote` | Four commits; two reveals in time. A late commit and late reveals were rejected by the contract windows |
| `close_panel` | No majority (2 of 5), so the ruling stands, the appeal bond is refunded, and the three jurors who did not reveal are slashed 15% |
| `withdraw` / `unstake_juror` | Seller 6 GEN, buyer 1 GEN, revealing jurors 0.225 GEN each; the contract balance returned to 0 |

Not exercised live (covered by the offline suite): a verified jury majority that departs from the ruling (the model plausibility check), the `HANDOVER_FAILED` and `PARTIAL_HANDOVER` outcomes, delegation mode, cancel and the expiry paths.

## License

MIT
