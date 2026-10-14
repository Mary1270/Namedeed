# NameDeed

Escrow and dispute resolution for the sale of a domain name, built as a single GenLayer Intelligent Contract. The buyer escrows the price and the seller posts a bond. The contract reads the domain registry (RDAP) and public DNS to decide, in code, whether the domain really changed hands. A staked jury can review a ruling, but only within what the registry data proves.

## How a deal works

1. The buyer opens a deed: domain, deadline, price, seller bond, and either the buyer's registrar ID (registrar transfer) or the nameservers the domain must move to.
2. The seller accepts by posting the bond. The contract derives a challenge token for this deed.
3. The seller publishes the token as a TXT record at `_namedeed.<domain>` and takes an observation **before touching the domain**. This baseline proves control and that nothing had changed yet.
4. The seller transfers the domain or changes its nameservers, then takes another observation.
5. One gap after the latest observation, anyone issues the ruling. Either party may appeal; payouts are pulled with `withdraw`.

## What makes it hold up

- **The handover is read from the registry, not from DNS zone data.** Transfer mode checks the sponsoring registrar's IANA ID in RDAP; delegation mode checks the registry delegation in RDAP. Neither party can fake these by editing a zone.
- **A handover needs a baseline.** It only counts if an earlier observation shows the token while the handover is not yet in place. A domain that already matched the target at the first sighting is `UNDETERMINED` and both sides are refunded.
- **The token proves control only.** After a switch the buyer runs the zone, so the handover never depends on a record the buyer could withhold.
- **Two-observation stability rule.** A handover or a control proof counts only when seen in observations at least N hours apart. A ruling can only run one gap after the latest observation, so a change flashed for one observation never wins and nobody can rule before a fresh handover matures.
- **Deterministic Tier 1.** The ruling is computed from the facts in code. No model, and no party-written text, influences it.
- **Backstop.** `HANDOVER_COMPLETE` is impossible unless every structured check passes; anything the facts do not allow becomes `UNDETERMINED`.
- **Bounded Tier 2.** A jury may only choose outcomes the frozen facts allow (for example, downgrade a completion over off-chain terms; in transfer mode, find that the domain reached the buyer's registrar but not the buyer's account and choose a refund or a failure). Any departure from the code default must also pass a model plausibility check over the frozen facts. Tier 2 never re-fetches.
- **Fair money.** Price and bond are separate amounts. Payouts follow the outcome and whether the buyer already holds the domain.
- **Fair jury.** Jurors must be registered at least seven days before the deed was accepted. The pool is frozen at appeal and drawn from a future drand round; commits are bound to juror and deed.
- **Never stuck.** Every state has a permissionless exit that pays out or refunds.
- **Deposits are never stranded.** A payable call that is invalid does not revert. It returns `{"rejected": true, "reason": ..., "refund_wei": ...}` and credits the GEN back to the sender's balance.
- **Payouts are reconciled.** `withdraw` records the payout as the single in-flight transfer. `reconcile_withdrawal` confirms it once the contract's balance shows it left, or, after seven days, returns it to the owner if it never did. The dApp reports a payment only after the wallet balance has risen.

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

`index.html` uses `genlayer-js` from esm.sh and `createClient({ chain: studionet, account })` (with `provider: window.ethereum` for a browser wallet). Every action waits for finalization, checks the execution result and reads the contract back to confirm the expected change before reporting success. Hash routes: `#/` (explore), `#/create`, `#/deed/<id>`, `#/jury`, `#/withdraw`. Set the contract address in `DEFAULT_CONTRACT` at the top of the script, or save it from the wallet sheet in the page (kept in that browser only).

## Live deployment

| Contract | Address | Status |
| --- | --- | --- |
| Production (`contract_deploy.py`) | [`0x970d3c629B79D6C05c5aC59db790b3aC75a9DB53`](https://explorer-studio.genlayer.com/address/0x970d3c629B79D6C05c5aC59db790b3aC75a9DB53) | current |
| Short-window test (`contract_short_deploy.py`) | [`0x516b52F6aB1Eb9D397B71CB9CB3702Bd2456b092`](https://explorer-studio.genlayer.com/address/0x516b52F6aB1Eb9D397B71CB9CB3702Bd2456b092) | current |
| First production deployment | [`0x3580ef2c572FdA4828531cE035318F7fEAD4131b`](https://explorer-studio.genlayer.com/address/0x3580ef2c572FdA4828531cE035318F7fEAD4131b) | superseded (no baseline rule) |
| First short-window deployment | [`0x4C6Ae6369b8D38FFf2e70fd7518f5084736bFB4b`](https://explorer-studio.genlayer.com/address/0x4C6Ae6369b8D38FFf2e70fd7518f5084736bFB4b) | superseded (no baseline rule) |

## Live tests of the current version

All runs used separate Studio wallets for buyer, seller and five jurors on the short-window deployment `0x516b52F6aB1Eb9D397B71CB9CB3702Bd2456b092`, with real registry and DNS data for `google.com`.

| Run | What it showed |
| --- | --- |
| Invalid payments | A wrong seller bond and an invalid deed were accepted as normal calls, returned a reason, credited the full value to the sender and changed no deed state. The refunds were withdrawn and confirmed with `reconcile_withdrawal` |
| No baseline (transfer mode) | `google.com` was already sponsored by registrar `292` and no control token was published. The old version paid the seller. This version ruled `UNDETERMINED` (`handover_unbaselined`) and both sides were refunded |
| Delegation mode, failure | Nameservers unchanged and no handover: `HANDOVER_FAILED` with `failure_shown` |
| Jury round | Five separate juror wallets were drawn. Three revealed `UNDETERMINED`, one `HANDOVER_FAILED`, one never revealed. The majority did not pass the plausibility gate (`verification_passed: false`), the code ruling stood, the appeal bond was returned and only the non-revealing juror was slashed (stake 1 to 0.85 GEN, reputation -3) |
| Payouts | `withdraw` records the in-flight payout, `reconcile_withdrawal` returned `confirmed` only after the transfer left the contract. Final contract balance equalled the held ledger |

The production deployment `0x970d3c629B79D6C05c5aC59db790b3aC75a9DB53` was smoke-tested with real timings: an invalid deed and a wrong seller bond were refunded in full, a valid deed was created and cancelled, and every payout was withdrawn and confirmed with `reconcile_withdrawal` (final balance 0 GEN). A genuine seller-controlled domain transfer could not be demonstrated live because no test domain was available; that path is covered by the offline suite.

## First live test (superseded version)

A full lifecycle was run on the first short-window deployment with real registry and DNS data (`google.com`, transfer mode, buyer registrar IANA ID `292`). Every call reached validator consensus. The run exercised funding, observation, the ruling, an appeal, the jury draw (replayed on chain), commit and reveal, slashing and withdrawals.

It also exposed a flaw in that version: `google.com` was already sponsored by registrar `292` when the deed was opened, no control proof had been published, and the contract still ruled `HANDOVER_COMPLETE` and paid the seller. The current version requires a baseline observation before a handover can count, so the same data is now `UNDETERMINED` and refunds both sides (`test_domain_already_at_the_buyers_registrar_cannot_pay_the_seller`). Live runs of the current version are listed below once completed.

## License

MIT
