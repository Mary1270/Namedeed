# NameDeed: architecture

NameDeed is a single GenLayer Intelligent Contract that escrows the sale of a domain name and settles disputes about the handover. This document explains what it proves, who controls each piece of data it reads, and where it can still be wrong.

## 1. Who controls what

The design starts from one question: for every piece of data the contract reads, who can change it?

| Data | Read from | Controlled by |
| --- | --- | --- |
| Delegation (the domain's nameservers at the registry) | RDAP `nameservers` | The sponsoring registrar, on the account holder's instruction |
| Sponsoring registrar | RDAP entity with role `registrar`, `IANA Registrar ID` | Changes only through an inter-registrar transfer |
| TXT record at `_namedeed.<domain>` | dns.google | Whoever the registry delegates to: the seller before a switch, the buyer after it |
| NS records inside the zone (apex NS) | not read | Whoever runs the zone; never used, because either side can forge them |

Earlier versions read the zone's own NS records and required the challenge token after the switch. Both were wrong: the seller could forge apex NS records without moving anything, and once the domain moved, the buyer ran the zone and could withhold the token to turn a real handover into a failure. The current design reads the handover **only from registry data**, and uses the token **only to prove the seller's control** before the switch.

## 2. Money and roles

- The **buyer** opens a deed and escrows the **price**. The buyer also fixes the **seller bond**, which can be any amount (it does not have to equal the price).
- The **seller** accepts by sending exactly the bond.
- Payouts only credit `pending_withdrawals`; `withdraw()` is the one method that sends GEN out, and it zeroes the ledger before the transfer (see 8 for how a payout is reconciled).
- Every payable method (`create_deed`, `accept_deed`, `register_juror`, `contest`) counts the GEN it received, validates everything before its first write, and on any invalid input returns `{"rejected": true, "reason", "refund_wei"}` instead of reverting. The GEN sent is credited to the caller's `pending_withdrawals`, so an invalid deposit is never stranded.

| Outcome | When | Payout |
| --- | --- | --- |
| `HANDOVER_COMPLETE` | Handover proven, started by the deadline, held for the gap | Seller gets price + bond |
| `PARTIAL_HANDOVER`, buyer holds the domain | Handover proven but started after the deadline (or a jury downgrade, see 7) | Seller gets price + bond − penalty; buyer gets the penalty |
| `PARTIAL_HANDOVER`, buyer does not hold it | Control proven twice in time, no handover | Buyer gets price + penalty; seller gets bond − penalty |
| `HANDOVER_FAILED` | After the deadline: no handover, no control in time | Buyer gets price + bond |
| `UNDETERMINED` | Data cannot support any other outcome, including a handover with no baseline (see 6) | Price back to the buyer, bond back to the seller |

The penalty is `PARTIAL_PENALTY_BPS` (50%) of the bond.

## 3. Anchors

`create_deed` stores, before anyone knows how things will go: the domain; the record type (`TXT`); the deadline; the buyer's nameserver set; the buyer's IANA registrar ID (optional); the two evidence hosts (`dns.google` and `rdap.verisign.com` for `.com` and `.net`, or `rdap.publicinterestregistry.org` for `.org`); and the minimum gap. The seller sees all of it before posting the bond. The contract builds every URL it fetches from these anchors; no party supplies a URL. The hosts are still validated with `urlsplit` (https only, no userinfo, exact host or subdomain), and the two hosts must belong to different registrable domains.

**Two modes.**

- **Transfer mode** (registrar ID given): the handover is the domain being sponsored by the buyer's registrar. After a transfer the buyer controls the delegation, so the nameserver set is not decisive in this mode.
- **Delegation mode** (no registrar ID): the handover is the registry delegation equal to the anchored nameserver set. The seller keeps the registrar account, so this mode is weaker (see section 10). The dApp recommends transfer mode.

## 4. Handshake nonce

At `accept_deed` the contract derives

```
challenge_token = sha256("<deed_id>:<domain>:<buyer>:<seller>")   # lowercase hex, addresses lowercased
```

The seller publishes it as a TXT record at `_namedeed.<domain>` while still running the zone. It is bound to one deed, one domain and one pair of parties, so a record from another deal cannot be replayed. It proves **control**, which only matters for `PARTIAL_HANDOVER`; it is never part of the handover proof.

## 5. Observations

`observe` (buyer or seller, three each, until the evidence deadline) takes one observation:

- `https://dns.google/resolve?name=_namedeed.<domain>&type=TXT`: `Status` and whether a type-16 answer at exactly that name equals the token. Values are compared after removing quotes; dns.google returns them unquoted.
- The RDAP domain object: `ldhName` must equal the domain; it records the delegation from `nameservers`, the IANA ID of the registrar entity, and the registration and last-changed dates for display.

Each observation is reduced to these decisive fields. TTLs, raw text and unrelated records are dropped, so validators fetching a moment apart still agree. Validators re-run both fetches and compare the reduced result exactly in code through `gl.vm.run_nondet_unsafe`. A failed or unparsable fetch reverts the call so it can be retried; it never becomes evidence. Each observation is frozen on-chain with its digest, and `evidence_root` commits to the ordered list.

`issue_ruling` takes the final observation. It can only run once `min_gap_seconds` have passed since the **latest** observation, so whoever observed last always gets the full gap and a counterparty cannot rule early.

## 6. Stability rule and facts

All facts are computed in code from the ordered, frozen observations (`_facts`):

| Fact | Meaning |
| --- | --- |
| `baseline_seen` | An observation shows the token (the seller controls the zone) while the handover is not in place. Only a handover run that starts after it can count. |
| `handover_stable` | A run after the baseline, seen in consecutive observations spanning at least the gap. In delegation mode this run must reach the final observation (a revert is the seller's). In transfer mode any such run counts (after the transfer only the buyer can move the domain). |
| `handover_unbaselined` | A run spanning the gap exists, but no baseline precedes it (for example the registry already showed the buyer's registrar at the first sighting). It never counts as a handover and the default is `UNDETERMINED`. |
| `complete_proven` | `handover_stable`, and the run started at or before the deadline |
| `late_handover` | `handover_stable`, but the run started after the deadline |
| `handover_pending` | Handover in place at the final observation but no run has spanned the gap yet. The ruling **reverts** instead of settling. |
| `buyer_holds` | Handover in place at the final observation, or (transfer mode) a run spanning the gap happened |
| `control_in_time` | Token seen in two observations at least the gap apart, the first at or before the deadline |
| `failure_shown` | After the deadline: the TXT query was answered (`NOERROR` or `NXDOMAIN`), the registry answered for this domain, no handover now, no control in time, and (transfer mode) no run that spanned the gap. A match seen once, or twice closer than the gap, and then reverted does not block a failure ruling. |

A record or delegation flashed for one observation never counts, because every proof needs two sightings at least the gap apart. A handover with no baseline never counts either.

**Why a baseline.** The registry can only show the state now. If the domain already sits at the buyer's registrar when the first observation is taken, nothing in the data shows that the seller moved it after the deed was accepted. The seller must therefore observe, with the token published, **before** moving the domain. The first live test used `google.com`, whose registrar already matched the target, and the first version paid the seller in full. With the baseline the same data is `UNDETERMINED` and both sides are refunded.

## 7. Tier 1 is deterministic; Tier 2 is bounded

**Tier 1.** `issue_ruling` maps the facts to an outcome in code (`_default_outcome`): completion, then late handover, then a handover with no baseline (`UNDETERMINED`), then control in time, then failure, otherwise `UNDETERMINED`. No model is called, so no party-written text can influence it and validators cannot disagree about wording. Before the deadline only `HANDOVER_COMPLETE` can be recorded; anything else reverts and leaves the deed open.

**Backstop.** `_clamp_outcome` turns any outcome not in `_allowed_outcomes` into `UNDETERMINED`. `HANDOVER_COMPLETE` is allowed only when `complete_proven` is true.

**Tier 2.** Either party may appeal within two days with a bond of 20% of the price. The juror pool is frozen at appeal time, and only jurors registered at least seven days before the deed was accepted (and not topped up since) are eligible. Parties are excluded. The jury is drawn from the drand round that follows the appeal window, using integer-only weighted selection that `verify_jury_selection` replays. Votes are commit-reveal, bound to the juror and the deed.

After the reveal window, `close_panel` verifies a strict majority against the frozen data only (no fetch):

1. Every observation is re-hashed against the root; a mismatch fails verification.
2. The facts are recomputed and the majority must be in `_allowed_outcomes`:
   - the code default is always allowed;
   - a proven completion may be downgraded to `PARTIAL_HANDOVER`, for off-chain terms the code cannot see (website files, email, and so on);
   - in transfer mode, when the default pays the seller for a handover (`HANDOVER_COMPLETE`, or `PARTIAL_HANDOVER` with the buyer holding), `UNDETERMINED` (refund both) and `HANDOVER_FAILED` are also allowed. A registrar match proves which registrar sponsors the domain, not whose account holds it, so a jury can find that the buyer never received the domain;
   - otherwise `UNDETERMINED` is allowed only while the buyer does **not** already hold the domain;
   - nothing above what the facts prove is ever allowed.
3. A majority equal to the code default is verified by code.
4. A majority that departs from the default must also pass a bounded model check. Given the frozen facts and the terms, which it is told are untrusted and written by one party, is there a concrete reason for the departure? Validators compare a single boolean. A model failure or any answer other than literal `true` counts as no.

An unverified majority leaves the ruling in place, refunds the appeal bond and slashes only non-revealers. Wrong-vote slash is 10%, non-reveal 15%. Slashed stake and a forfeited bond go to majority jurors pro rata. Reputation stays in [-50, 50] and moves selection weight between 0.5x and 1.5x.

## 8. Recovery paths

| State | Exit |
| --- | --- |
| `proposed` | `cancel_deed` (buyer) refunds the price |
| `active` | `expire_unobserved` after the evidence window; both refunded |
| `observing` | `issue_ruling`, or `expire_unruled` 7 days after the later of (latest observation + gap) and the evidence deadline; both refunded |
| `ruled` | `close_uncontested` after the appeal window |
| `contested`, `paneled` | `close_panel`, or `expire_stuck_appeal` after 7 days: the ruling stands, the bond is refunded, jurors are unlocked |

### Payout reconciliation

`withdraw()` zeroes the ledger, records the payout as the single `in_flight` withdrawal (recipient, amount, time) and emits the transfer. The contract counts every wei that arrives through a payable call (`total_received`) and every payout it has confirmed (`confirmed_sent`), so it knows what it must hold (`held = total_received - confirmed_sent`).

| Contract balance | Meaning | `reconcile_withdrawal` |
| --- | --- | --- |
| `<= held - amount` | the transfer left | `confirmed`: `confirmed_sent += amount` |
| `>= held`, less than 7 days old | not yet confirmed; the funds are still held and tracked | `waiting` |
| `>= held`, 7 days or more old | the transfer never left | `restored`: the amount returns to the recipient's `pending_withdrawals` |
| anything else | accounting mismatch | reverts; nothing is guessed |

Only one payout can be in flight. A new `withdraw()` first tries to settle the previous one, and is refused while it is still unconfirmed. The seven-day delay is deliberately long, because a restored amount must never be paid twice if the runtime delivers a transfer late. The dApp reports a payment only after the recipient's wallet balance has risen.

## 9. Authorization

| Method | Who |
| --- | --- |
| `create_deed` | anyone (becomes the buyer) |
| `accept_deed` | the named seller |
| `cancel_deed` | the buyer, before acceptance |
| `observe`, `contest` | buyer or seller |
| `commit_vote`, `reveal_vote` | selected jurors |
| `issue_ruling`, `close_uncontested`, `seat_panel`, `close_panel`, `expire_*`, `reconcile_withdrawal` | anyone; these take no caller input and are gated by status, time or the contract's own accounting |

## 10. Limitations (disclosed)

- **The suite uses a modelled runtime.** It runs against a self-written SDK stub, a modelled registry and DNS, and a pinned clock. The parsers were written against real responses (`rdap.verisign.com` for google.com, `rdap.publicinterestregistry.org` for wikipedia.org, `dns.google` TXT). The live runs are listed in the README; what they do and do not cover is stated there.
- **A baseline needs the seller to act in order.** The seller must publish the token and observe before moving the domain. A seller who moves first, or a domain that was already at the target, is refunded, never paid.
- **Delegation mode leaves the registrar account with the seller.** The seller can switch the nameservers back after settlement. Transfer mode avoids this, because a transfer back needs the buyer's authorisation code, and registrars commonly block a new transfer for 60 days after the last one. Delegation mode must not be combined with a same-registrar account push: after a push the buyer could move the delegation away and the contract cannot tell who did it.
- **Transfer mode trusts the registry's registrar entity.** It proves which registrar sponsors the domain, not which account at that registrar. A seller with an account at the buyer's registrar could transfer into their own account there. The handover terms should name the receiving account, and the jury can downgrade a completion when the buyer shows it did not arrive.
- **Registry data lags.** RDAP and dns.google reflect changes within minutes to hours. Sellers should hand over early, and the gap should comfortably exceed the expected lag.
- **Coverage.** Only `TXT` is used for the handshake, and only the two built-in RDAP hosts are supported (`.com`, `.net`, `.org`). Other TLDs need a code change.
- **The plausibility check is a model.** It only gates a jury departure from a code default that the jurors themselves voted for, and it can only move the outcome inside the allowed set. Party-written terms can still mislead it.
- **Jury.** The drand beacon is fetched through the validators' web access, not an on-chain VRF. The seven-day registration age and stake freeze make planting jurors harder but do not stop a patient attacker. Building the pool reads every juror record, so `contest` costs more as the registry grows (the snapshot is capped at the 100 heaviest jurors).
- **The clock is transaction time.** `datetime.now()` is pinned by GenVM to the transaction timestamp, identical for every validator.
- **Rejection returns the GEN through the ledger.** An invalid payable call succeeds with a rejection result and credits the sender, so the refund needs a `withdraw()`. A malformed call that fails before the contract runs (wrong ABI, no value field) never reaches it.
- **Payout reconciliation relies on the contract balance.** GEN sent to the contract outside a payable method raises the balance above the accounting; the worst case is that a payout is restored while it also left, which a donor can only cause by paying the same amount in.
- **The dApp loads `genlayer-js@latest`.** This follows the project rule, but a breaking release of the library could break the page until it is updated.
