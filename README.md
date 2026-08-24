# Vehicle History — GenLayer

AI-verified vehicle condition screening with reusable on-chain records. The
chain cannot know a vehicle's accident, flood, salvage, or recall history, so
each check queries authoritative registries and validators must agree on the
verdict before it is recorded. Records are reusable by any marketplace or buyer.

## Lifecycle

```
submit_vehicle(vin, make, model, year)   # PENDING
process_vehicle(id)      # queries authoritative sources + AI consensus -> COMPLETED, caches record
get_vehicle(id)          # full check record
get_record(vin)          # reusable record (VIN key, case-insensitive)
```

## Authoritative sources & retrieval evidence

Every check queries four authoritative vehicle-history / recall registries built
from the VIN — **CARFAX** vehicle history, **NHTSA** recall lookup, **Copart**
and **IAA** salvage-auction listings — via `gl.nondet.web.get`. A source only
counts as `retrieved=True` when its response body is non-empty **and actually
references this VIN**, so a generic landing/error page never counts as
VIN-specific evidence. Each source's URL, retrieval success, and a content
excerpt are preserved in the verdict and reusable record, so the evidence behind
a condition claim is auditable on-chain (see `sources[]`). VINs are validated
on-chain (17 characters, valid charset, normalized uppercase).

## Explicit inconclusive results

A verdict is never silently "clean": the AI must return an explicit `status` of
`CLEAN`, `DAMAGED`, `FLOODED`, `SALVAGE`, or `INCONCLUSIVE`. A missing or
unsupported `status` is never derived to `CLEAN` (which would let a bad vehicle
silently pass) — it defaults to `INCONCLUSIVE`. When no authoritative source
yields VIN-specific content, the check returns `INCONCLUSIVE` — stored, surfaced,
and counted separately in stats — rather than guessing a false "clean".

## Hard source requirement

If no authoritative source was successfully retrieved for this VIN, the verdict
is forced to `INCONCLUSIVE` in contract logic regardless of what the LLM
returned. A `CLEAN` verdict must rest on VIN-specific evidence, not on generic
page responses or empty results.

## Consensus binding

`gl.nondet.web.get` gathers the live registry content and
`gl.eq_principle.prompt_comparative` binds the decision outputs — `status`
(CLEAN/DAMAGED/FLOODED/SALVAGE/INCONCLUSIVE), `recalled` (bool), `severity`
(NONE/LOW/MEDIUM/HIGH/CRITICAL), `matched_records` (order-insensitive), and
`sources` (url + retrieved pairs, order-insensitive) — so validators agree on
which authoritative sources were actually retrieved for this VIN as well as on
the verdict. `reasoning` and excerpt wording may differ. Every verdict is
normalized before storage.

## Record ownership & collision guard

The reusable record is keyed by the normalized VIN and stores full identity
(make, model, year), the requester, and the originating check. A settled record
(`CLEAN`/`DAMAGED`/`FLOODED`/`SALVAGE`) can only be replaced by its original
requester; an unrelated caller or a same-VIN collision with a different identity
is rejected rather than silently overwriting the record. An `INCONCLUSIVE`
record may be improved by anyone (retry with better sources).

## Trust problem

Off-chain claims ("no accidents, never flooded, clean title") are unverifiable
on-chain. This contract replaces trust with an AI-consensus condition verdict
anchored to authoritative live sources, recorded once and reused via
`get_record`, so any marketplace or buyer can gate on the outcome.

## Tests

```bash
pip install -r requirements.txt
python -m pytest tests/direct/ -v
```

Direct-mode tests (in-memory VM, no network/consensus) cover submit -> process ->
verdict, VIN validation, authoritative-source querying, VIN-specific retrieval
(generic page without VIN does not count), preserved retrieval details, the hard
source requirement (no source -> forced INCONCLUSIVE even when the LLM answers
CLEAN), explicit INCONCLUSIVE handling, missing-status -> INCONCLUSIVE
normalization, reusable caching, ownership/collision guards, state guards, and
stats.

## Live

- Contract: `0xEE596f3230b739D66763662128CCaC9450B36E96`
- Explorer: https://explorer-bradbury.genlayer.com/address/0xEE596f3230b739D66763662128CCaC9450B36E96
- Deploy tx: `0x794eb5e25d1e6b3bddbf3e3c333e964ff0d572d7b3f05ef6f9f274e11a33af47`
- Frontend: https://qlxjcj.github.io/genlayer-vehicle-history/
