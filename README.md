# NameDeed

Escrow and dispute resolution for the sale of a domain name, built as a single GenLayer Intelligent Contract. The buyer escrows the price and the seller posts a bond. The contract reads the domain registry (RDAP) and public DNS to decide, in code, whether the domain really changed hands. A staked jury can review a ruling, but only within what the registry data proves.

## How a deal works

1. The buyer opens a deed: domain, deadline, price, seller bond, the nameservers the domain must move to, and the hash of a secret only the buyer knows.
2. The seller accepts by posting the bond. The contract derives a challenge token for this deed.
3. The seller publishes the token as a TXT record at `_namedeed.<domain>` and takes an observation **before touching the domain**. This baseline proves control and that nothing had changed yet.
4. The seller changes the domain's nameservers at the registrar. The buyer publishes its secret as a TXT record at `_namedeed-buyer.<domain>` in the zone at those nameservers. Either party then takes another observation.
5. One gap after the latest observation, anyone issues the ruling. Either party may appeal; payouts are pulled with `withdraw`.

## What makes it hold up

- **A registry match alone never pays the seller.** The handover needs the registry delegation (RDAP `nameservers`, which no zone operator can forge) **and** the buyer's own secret served from the zone that delegation points to. Only the buyer knows the secret, so only a zone the buyer runs can show it. A delegation without the secret is `UNDETERMINED` and both sides are refunded. An earlier registrar-transfer mode was removed because a registrar ID cannot show whose account received the domain.
- **A handover needs a baseline.** It only counts if an earlier observation shows the token while the handover is not yet in place. A domain that already matched the target at the first sighting is `UNDETERMINED` and both sides are refunded.
- **The seller's token proves control only.** It is used for the baseline and for `PARTIAL_HANDOVER`, never for the handover itself.
- **Two-observation stability rule.** A handover or a control proof counts only when seen in observations at least N hours apart. A ruling can only run one gap after the latest observation, so a change flashed for one observation never wins and nobody can rule before a fresh handover matures.
- **Deterministic Tier 1.** The ruling is computed from the facts in code. No model, and no party-written text, influences it.
- **Backstop.** `HANDOVER_COMPLETE` is impossible unless every structured check passes; anything the facts do not allow becomes `UNDETERMINED`.
- **Bounded Tier 2.** A jury may only choose outcomes the frozen facts allow (for example, downgrade a completion over off-chain terms). Any departure from the code default must also pass a model plausibility check over the frozen facts. Tier 2 never re-fetches.
- **Fair money.** Price and bond are separate amounts. Payouts follow the outcome and whether the buyer already holds the domain.
- **Fair jury.** Jurors must be registered at least seven days before the deed was accepted. The pool is frozen at appeal and drawn from a future drand round; commits are bound to juror and deed.
- **Never stuck.** Every state has a permissionless exit that pays out or refunds.
- **Deposits are never stranded.** A payable call that is invalid does not revert. It returns `{"rejected": true, "reason": ..., "refund_wei": ...}` and credits the GEN back to the sender's balance.
- **Payouts are reconciled.** `withdraw` records the payout as the single in-flight transfer. `reconcile_withdrawal` confirms it once the contract's balance shows it left, or, after seven days, returns it to the owner if it never did. The dApp reports a payment only after the wallet balance has risen.

Outcomes:

| Outcome | Meaning | Money |
| --- | --- | --- |
| `HANDOVER_COMPLETE` | Delegation and buyer proof held, started in time, after a baseline | Seller receives price + bond |
| `PARTIAL_HANDOVER` | Either the handover was proven but late, or the seller proved control twice in time (token) and then did not complete by the deadline | Seller forfeits half the bond to the buyer; the buyer gets the price back, unless the buyer already holds the domain, in which case the seller is paid the price |
| `HANDOVER_FAILED` | Deadline passed, registry and DNS answered, no handover and no control shown | Buyer receives price + bond |
| `UNDETERMINED` | The evidence cannot show what happened (no baseline, delegation without buyer proof, sources unreadable) | Each side gets its own money back |

A seller who only shows control and never changes the delegation therefore gets `PARTIAL_HANDOVER`, a penalty but not a failure. This is a deliberate rule, not a handover: nothing is paid to the seller. See `ARCHITECTURE.md` for the data-control table, payout table, facts, recovery paths and disclosed limitations.

## Files

| File | Purpose |
| --- | --- |
| `contract.py` | The documented source |
| `contract_deploy.py` | Same code without comments or docstrings, line numbers unchanged. Deploy this file to GenLayer Studio |
| `contract_short_deploy.py` | Test-only variant with minute-scale windows for trying the full lifecycle live against real registry and DNS data. Never use it with real funds |
| `contract_evidence_deploy.py` | Test-only variant with minute-scale windows and a **synthetic evidence source**: an observation reads a fixture set with `set_fixture` instead of DNS and RDAP. Every function after the observation is identical to the production contract (a test checks this). It reports `SYNTHETIC FIXTURE - NOT REAL REGISTRY DATA` and exists only to show, live, outcomes that need real registry changes. Never use it with real funds |
| `tools/make_deploy.py` | Builds the three deploy files using `ast` and `tokenize` |
| `index.html` | Single-page dApp: explore, open a deed, deed page with contextual actions, jury, balance |
| `tests/` | Offline unit tests, the SDK stub and the registry and DNS model |
| `.github/workflows/tests.yml` | Runs the suite against both contract files and checks the deploy file is current |

## Tests

Offline unit tests against a self-written `genlayer` SDK stub, with a pinned clock and a model of the registry and the two zones (seller's and buyer's). No `pip install` is needed.

```
python3 -m unittest discover -s tests -v
ND_CONTRACT=contract_deploy.py python3 -m unittest discover -s tests -v
ND_CONTRACT=contract_evidence_deploy.py python3 -m unittest discover -s tests -p test_evidence_variant.py -v
```

`test_adversarial.py` plays the buyer and the seller as attackers. Covered attacks include:

- withholding the token after the switch;
- forging apex NS records;
- flashing a delegation or a token;
- ruling early;
- a registry match without the buyer's proof (the seller changes the delegation, the buyer's zone does not serve the secret);
- a forged or guessed buyer proof in the seller's own zone;
- replaying another deed's token;
- delegating to a third party.

The rest of the suite covers input validation, parsers built against real RDAP and dns.google responses, the stability rule and every fact, consensus agreement and drift, settlement, evidence digests, the jury and the plausibility gate, recovery paths, and the rules the deploy file must follow.

## The dApp

`index.html` uses `genlayer-js` from esm.sh and `createClient({ chain: studionet, account })` (with `provider: window.ethereum` for a browser wallet). Every action waits for finalization, checks the execution result and reads the contract back to confirm the expected change before reporting success. Hash routes: `#/` (explore), `#/create`, `#/deed/<id>`, `#/jury`, `#/withdraw`. Set the contract address in `DEFAULT_CONTRACT` at the top of the script, or save it from the wallet sheet in the page (kept in that browser only).

## Live deployment

Everything listed as superseded ran an earlier design and is kept for history only.

| Contract | Address | Status |
| --- | --- | --- |
| Production (`contract_deploy.py`) | [`0xB44eBf81164AE592f54E5818Ab82f8625485e266`](https://explorer-studio.genlayer.com/address/0xB44eBf81164AE592f54E5818Ab82f8625485e266) | current |
| Short-window test (`contract_short_deploy.py`) | [`0x01e28EE920197C7B7413991d4Ef5c0b3B57907a6`](https://explorer-studio.genlayer.com/address/0x01e28EE920197C7B7413991d4Ef5c0b3B57907a6) | current |
| Synthetic-evidence test (`contract_evidence_deploy.py`) | [`0xa6eA661aDCBF7048F5C6574728C3Aa3EC0D62056`](https://explorer-studio.genlayer.com/address/0xa6eA661aDCBF7048F5C6574728C3Aa3EC0D62056) | current, test only |
| Previous production (registry-only proof, with transfer mode) | [`0x970d3c629B79D6C05c5aC59db790b3aC75a9DB53`](https://explorer-studio.genlayer.com/address/0x970d3c629B79D6C05c5aC59db790b3aC75a9DB53) | superseded (a registrar match could pay the seller) |
| Previous short-window test | [`0x516b52F6aB1Eb9D397B71CB9CB3702Bd2456b092`](https://explorer-studio.genlayer.com/address/0x516b52F6aB1Eb9D397B71CB9CB3702Bd2456b092) | superseded |
| First production deployment | [`0x3580ef2c572FdA4828531cE035318F7fEAD4131b`](https://explorer-studio.genlayer.com/address/0x3580ef2c572FdA4828531cE035318F7fEAD4131b) | superseded (no baseline rule) |
| First short-window deployment | [`0x4C6Ae6369b8D38FFf2e70fd7518f5084736bFB4b`](https://explorer-studio.genlayer.com/address/0x4C6Ae6369b8D38FFf2e70fd7518f5084736bFB4b) | superseded (no baseline rule) |

## Live tests of the current version

All runs below used the synthetic-evidence contract `0xa6eA661aDCBF7048F5C6574728C3Aa3EC0D62056` (see the next section for what that means), with separate Studio wallets for the buyer, the seller and five jurors. Every transaction reached validator consensus.

| Run | What it showed |
| --- | --- |
| `deed_0`, partial handover | The seller proved control twice in time (token seen at two observations a gap apart) and never changed the delegation. After the deadline `issue_ruling` returned `PARTIAL_HANDOVER` (`control_in_time: true`, `handover_ever: false`). Settlement: the price went back to the buyer and half the seller bond went to the buyer as the penalty |
| `deed_1`, complete handover with the buyer proof | Baseline observation (token seen, delegation still the seller's), then the delegation matched the anchored nameservers and the zone served the buyer's secret. Held for the gap and ruled `HANDOVER_COMPLETE` (`complete_proven: true`, `delegation_unproven: false`). The seller received price plus bond |
| `deed_2`, successful jury reversal | Ruled `HANDOVER_COMPLETE`. The buyer appealed with a 1 GEN bond. Five separate juror wallets were drawn from a pool of exactly five, committed and revealed `PARTIAL_HANDOVER`. The plausibility gate accepted the departure (`verification_passed: true`; the handover terms say that delivery of the website files and the mailbox archive is part of the handover) and the final outcome became `PARTIAL_HANDOVER` (`status: closed`, `payout_settled: true`) |
| Payout reconciliation | After the three deeds the contract held 29 GEN: 8 GEN withdrawable by the buyer, 16 GEN by the seller and 5 GEN of juror stakes, which is exactly the contract balance |
| A mistaken call | One `create_deed` was sent from a juror wallet by mistake, which made that wallet the buyer of `deed_1` and left only four eligible jurors for that deed. The deed was completed without an appeal and a fresh deed (`deed_2`) was used for the jury run |

### Real RDAP and DNS data on the current code

Run on the short-window contract `0x01e28EE920197C7B7413991d4Ef5c0b3B57907a6` (the production logic with minute-scale windows), domain `google.com`, `deed_0`. The agreed nameservers were `ns1.example.net` and `ns2.example.net`. RDAP returned `ns1`..`ns4.google.com` and no token was served, so nothing was shown and no transfer is claimed. The ruling was `HANDOVER_FAILED` and the buyer received price plus bond (2 GEN); the contract balance returned to 0.

| Step | Method | Transaction |
| --- | --- | --- |
| Open | `create_deed` | `0x6f47782e...02ef9ace` |
| Accept | `accept_deed` | `0xde78331e...5c670a65` |
| Observe (real RDAP/DNS) | `observe` | `0x577661d1...81478d5a` |
| Ruling | `issue_ruling` | `0x01ea223f...9a543cdc` |
| Close | `close_uncontested` | `0x88aa8cf8...6dad9e6e` |
| Withdraw | `withdraw` | `0x7edf6395...5dbf453e` |
| Confirm payout | `reconcile_withdrawal` | `0x4569342d...f7907325` |

Transaction hashes are shortened as shown in the Studio explorer; open the contract page and match by prefix.

Earlier runs of the previous version (jury round with a rejected majority and a non-revealing juror, invalid payments refunded, no-baseline `UNDETERMINED` with real `google.com` data) are described in the history of this repository; they ran on superseded contracts.

## What is real and what is not

- **Real registry and DNS data** is used by the production and short-window contracts. No genuine seller-controlled domain transfer has been demonstrated live: no test domain was available to the author. The handover path is covered by the offline suite, which models a seller, a buyer and both zones.
- **The synthetic-evidence contract** exists to show on chain the outcomes that need a real registry change (`PARTIAL_HANDOVER`, a jury reversal accepted by the plausibility gate). Only the evidence source is replaced; the facts, ruling, appeal, jury, gate, settlement and payouts are the production code. Results from it demonstrate the settlement logic, not that a registry or a zone behaved in a certain way.

## Earlier live findings (superseded versions)

A full lifecycle on the first short-window deployment, with real data for `google.com`, exposed a flaw: the domain was already at the target registrar when the deed was opened, no control proof had been published, and the contract still paid the seller. The baseline rule fixes this (`google.com` is now `UNDETERMINED`). A later review showed that a registrar match, even with a baseline, still cannot prove whose account received the domain, so transfer mode was removed and the buyer proof was added.

## License

MIT
