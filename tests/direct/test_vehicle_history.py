"""Direct-mode tests for Vehicle History.

Covers the screening lifecycle: submit -> process (AI consensus) -> COMPLETED
with a normalized, reusable verdict. Verifies authoritative-source querying
with preserved retrieval details, explicit INCONCLUSIVE results, normalization,
reusable on-chain records, ownership/collision guards, state guards, and stats.

No network, no consensus: deterministic and instant.
Run: python -m pytest tests/direct/ -v   (from the project root)
"""

import json
import pytest

from conftest import (
    VIN,
    VERDICT_CLEAN,
    VERDICT_DAMAGED,
    VERDICT_FLOODED,
    VERDICT_INCONCLUSIVE,
    VERDICT_MALFORMED,
    VERDICT_SALVAGE,
    LLM_PATTERN,
)


def _check(c, cid):
    return json.loads(c.get_vehicle(cid))


def _verdict(c, cid):
    return json.loads(_check(c, cid)["verdict"])


def _record(c, vin):
    return json.loads(c.get_record(vin)) if c.get_record(vin) != "{}" else None


# ---------- submit ----------

def test_submit_creates_pending_check(direct_vm, vh):
    vm, c = vh
    c.submit_vehicle(VIN, "BMW", "328i", "2013")

    assert c.get_vehicle_count() == 1
    r = _check(c, 1)
    assert r["status"] == "PENDING"
    assert r["vin"] == VIN.upper()
    assert r["make"] == "BMW"
    assert r["verdict"] == ""
    assert r["requester"]


def test_submit_normalizes_vin_case(direct_vm, vh):
    vm, c = vh
    c.submit_vehicle("wbadx1c54djk00001", "BMW", "328i", "2013")
    assert _check(c, 1)["vin"] == VIN.upper()


def test_submit_rejects_short_vin(direct_vm, vh):
    vm, c = vh
    with pytest.raises(Exception) as ei:
        c.submit_vehicle("WBADX1C54", "BMW", "328i", "2013")
    assert "17" in str(ei.value)


def test_submit_rejects_bad_vin_chars(direct_vm, vh):
    vm, c = vh
    with pytest.raises(Exception) as ei:
        c.submit_vehicle("WBADX1C54DJK0000O", "BMW", "328i", "2013")
    assert "invalid" in str(ei.value).lower()


def test_submit_rejects_missing_make(direct_vm, vh):
    vm, c = vh
    with pytest.raises(Exception) as ei:
        c.submit_vehicle(VIN, "   ", "328i", "2013")
    assert "Make" in str(ei.value)


# ---------- process: statuses ----------

def test_process_clean(direct_vm, vh):
    vm, c = vh
    c.submit_vehicle(VIN, "BMW", "328i", "2013")
    c.process_vehicle(1)

    r = _check(c, 1)
    assert r["status"] == "COMPLETED"
    v = _verdict(c, 1)
    assert v["status"] == "CLEAN"
    assert v["recalled"] is False
    assert v["severity"] == "NONE"
    assert v["matched_records"] == []


def test_process_damaged(direct_vm, vh):
    vm, c = vh
    vm.clear_mocks()
    vm.mock_llm(LLM_PATTERN, VERDICT_DAMAGED)
    c.submit_vehicle(VIN, "BMW", "328i", "2013")
    c.process_vehicle(1)

    v = _verdict(c, 1)
    assert v["status"] == "DAMAGED"
    assert v["recalled"] is True
    assert v["severity"] == "HIGH"
    assert "NHTSA-R2024-001" in v["matched_records"]


def test_process_flooded(direct_vm, vh):
    vm, c = vh
    vm.clear_mocks()
    vm.mock_llm(LLM_PATTERN, VERDICT_FLOODED)
    c.submit_vehicle(VIN, "BMW", "328i", "2013")
    c.process_vehicle(1)

    v = _verdict(c, 1)
    assert v["status"] == "FLOODED"
    assert "IAA-FL-3301" in v["matched_records"]


def test_process_salvage(direct_vm, vh):
    vm, c = vh
    vm.clear_mocks()
    vm.mock_llm(LLM_PATTERN, VERDICT_SALVAGE)
    c.submit_vehicle(VIN, "BMW", "328i", "2013")
    c.process_vehicle(1)

    v = _verdict(c, 1)
    assert v["status"] == "SALVAGE"
    assert "COPART-1C301234" in v["matched_records"]


# ---------- authoritative sources + retrieval details ----------

def test_record_queries_authoritative_sources(direct_vm, vh):
    vm, c = vh
    c.submit_vehicle(VIN, "BMW", "328i", "2013")
    c.process_vehicle(1)

    rec = _record(c, VIN)
    urls = [s["url"] for s in rec["sources"]]
    assert any("carfax.com" in u for u in urls)        # CARFAX vehicle history
    assert any("nhtsa.gov" in u for u in urls)         # NHTSA recall lookup
    assert any("copart.com" in u for u in urls)        # Copart salvage auction
    assert any("iaai.com" in u for u in urls)          # IAA salvage auction
    assert len(urls) == 4
    assert VIN.upper() in urls[0]
    # no source mocked -> all retrieval attempts recorded as failed
    assert all(s["retrieved"] is False for s in rec["sources"])
    assert all(s["excerpt"] == "" for s in rec["sources"])


def test_record_preserves_retrieval_details(direct_vm, vh):
    vm, c = vh
    vm.mock_web(r".*nhtsa\.gov.*", {
        "method": "GET", "status": 200,
        "body": "Recall 24V-001 applies to this VIN. Battery fire risk.",
    })
    c.submit_vehicle(VIN, "BMW", "328i", "2013")
    c.process_vehicle(1)

    rec = _record(c, VIN)
    nhtsa = next(s for s in rec["sources"] if "nhtsa.gov" in s["url"])
    assert nhtsa["retrieved"] is True
    assert "Recall 24V-001" in nhtsa["excerpt"]
    others = [s for s in rec["sources"] if "nhtsa.gov" not in s["url"]]
    assert all(s["retrieved"] is False for s in others)


# ---------- explicit inconclusive ----------

def test_inconclusive_explicit(direct_vm, vh):
    vm, c = vh
    vm.clear_mocks()
    vm.mock_llm(LLM_PATTERN, VERDICT_INCONCLUSIVE)
    c.submit_vehicle(VIN, "BMW", "328i", "2013")
    c.process_vehicle(1)

    v = _verdict(c, 1)
    assert v["status"] == "INCONCLUSIVE"
    assert v["recalled"] is False
    assert v["severity"] == "NONE"
    assert v["matched_records"] == []
    assert _record(c, VIN)["status"] == "INCONCLUSIVE"


# ---------- verdict normalization ----------

def test_verdict_normalized(direct_vm, vh):
    vm, c = vh
    vm.clear_mocks()
    vm.mock_llm(LLM_PATTERN, VERDICT_MALFORMED)
    c.submit_vehicle(VIN, "BMW", "328i", "2013")
    c.process_vehicle(1)

    v = _verdict(c, 1)
    assert v["status"] == "DAMAGED"                 # derived from recalled=True
    assert v["severity"] == "NONE"                  # invalid level coerced
    assert v["matched_records"] == ["NHTSA-R2024-001"]  # string -> list


# ---------- reusable on-chain record ----------

def test_record_cached_and_case_insensitive(direct_vm, vh):
    vm, c = vh
    c.submit_vehicle(VIN, "BMW", "328i", "2013")
    c.process_vehicle(1)

    rec = _record(c, VIN)
    assert rec["status"] == "CLEAN"
    assert rec["vin"] == VIN.upper()
    assert rec["requester"]
    assert rec["from_check"] == "1"
    # lookup key is case-insensitive
    rec2 = _record(c, VIN.lower())
    assert rec2["status"] == "CLEAN"


def test_get_record_unknown(direct_vm, vh):
    vm, c = vh
    assert c.get_record("1G1JC5444R7252296") == "{}"


# ---------- record ownership / collision guards ----------

def test_unrelated_caller_cannot_replace_record(direct_vm, vh, direct_bob):
    vm, c = vh
    c.submit_vehicle(VIN, "BMW", "328i", "2013")
    c.process_vehicle(1)
    owner = _record(c, VIN)["requester"]

    vm.sender = direct_bob
    c.submit_vehicle(VIN, "BMW", "328i", "2013")
    with pytest.raises(Exception) as ei:
        c.process_vehicle(2)
    assert "settled by another requester" in str(ei.value).lower()

    rec = _record(c, VIN)
    assert rec["requester"] == owner
    assert rec["from_check"] == "1"


def test_vin_collision_with_different_identity_rejected(direct_vm, vh, direct_bob):
    vm, c = vh
    c.submit_vehicle(VIN, "BMW", "328i", "2013")
    c.process_vehicle(1)

    # same VIN, different make/model, unrelated caller -> rejected, not overwritten
    vm.sender = direct_bob
    c.submit_vehicle(VIN, "Toyota", "Corolla", "2020")
    with pytest.raises(Exception) as ei:
        c.process_vehicle(2)
    assert "settled" in str(ei.value).lower()
    assert _record(c, VIN)["make"] == "BMW"


def test_same_requester_can_refresh_record(direct_vm, vh):
    vm, c = vh
    c.submit_vehicle(VIN, "BMW", "328i", "2013")
    c.process_vehicle(1)

    c.submit_vehicle(VIN, "BMW", "328i", "2013")
    c.process_vehicle(2)
    assert _record(c, VIN)["from_check"] == "2"


def test_inconclusive_record_can_be_improved_by_anyone(direct_vm, vh, direct_bob):
    vm, c = vh
    vm.clear_mocks()
    vm.mock_llm(LLM_PATTERN, VERDICT_INCONCLUSIVE)
    c.submit_vehicle(VIN, "BMW", "328i", "2013")
    c.process_vehicle(1)
    assert _record(c, VIN)["status"] == "INCONCLUSIVE"

    vm.sender = direct_bob
    vm.clear_mocks()
    vm.mock_llm(LLM_PATTERN, VERDICT_DAMAGED)
    c.submit_vehicle(VIN, "BMW", "328i", "2013")
    c.process_vehicle(2)
    rec = _record(c, VIN)
    assert rec["status"] == "DAMAGED"
    assert rec["requester"] == direct_bob.as_hex


# ---------- state guards ----------

def test_process_twice_blocked(direct_vm, vh):
    vm, c = vh
    c.submit_vehicle(VIN, "BMW", "328i", "2013")
    c.process_vehicle(1)

    with pytest.raises(Exception) as ei:
        c.process_vehicle(1)
    assert "processed" in str(ei.value).lower()


def test_process_not_found(direct_vm, vh):
    vm, c = vh
    with pytest.raises(Exception) as ei:
        c.process_vehicle(99)
    assert "not found" in str(ei.value).lower()


# ---------- stats ----------

def test_stats_counts_statuses(direct_vm, vh):
    vm, c = vh
    c.submit_vehicle(VIN, "BMW", "328i", "2013")
    c.process_vehicle(1)

    vm.clear_mocks()
    vm.mock_llm(LLM_PATTERN, VERDICT_FLOODED)
    c.submit_vehicle("1G1JC5444R7252296", "Chevrolet", "Camaro", "1994")
    c.process_vehicle(2)

    vm.clear_mocks()
    vm.mock_llm(LLM_PATTERN, VERDICT_INCONCLUSIVE)
    c.submit_vehicle("JH4NA1260MT001352", "Acura", "Integra", "1991")
    c.process_vehicle(3)

    s = c.get_stats()
    assert s["total"] == 3
    assert s["completed"] == 3
    assert s["clean"] == 1
    assert s["flooded"] == 1
    assert s["inconclusive"] == 1
    assert s["records"] == 3
