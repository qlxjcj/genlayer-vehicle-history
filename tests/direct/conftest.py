"""Shared fixtures for Vehicle History direct-mode tests.

Direct mode runs the real contract source in-process. The AI vehicle-history
screening is mocked so tests are deterministic and instant, with no network or
consensus dependency.
"""

import json
import os
import pytest

CONTRACT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "vehicle_history.py",
)

VIN = "WBADX1C54DJK00001"

VERDICT_CLEAN = json.dumps({
    "status": "CLEAN",
    "recalled": False,
    "severity": "NONE",
    "matched_records": [],
    "reasoning": "No damage, salvage, or recall records found in authoritative registries.",
})

VERDICT_DAMAGED = json.dumps({
    "status": "DAMAGED",
    "recalled": True,
    "severity": "HIGH",
    "matched_records": ["NHTSA-R2024-001"],
    "reasoning": "Open recall and documented accident damage.",
})

VERDICT_FLOODED = json.dumps({
    "status": "FLOODED",
    "recalled": False,
    "severity": "CRITICAL",
    "matched_records": ["IAA-FL-3301"],
    "reasoning": "Flood-damage title found in an auction record.",
})

VERDICT_SALVAGE = json.dumps({
    "status": "SALVAGE",
    "recalled": False,
    "severity": "CRITICAL",
    "matched_records": ["COPART-1C301234"],
    "reasoning": "Vehicle was sold at a salvage auction.",
})

VERDICT_INCONCLUSIVE = json.dumps({
    "status": "INCONCLUSIVE",
    "recalled": False,
    "severity": "NONE",
    "matched_records": [],
    "reasoning": "Authoritative sources could not be retrieved for this VIN.",
})

# Missing / invalid fields to prove verdict normalization.
VERDICT_MALFORMED = json.dumps({
    "recalled": True,
    "severity": "not-a-level",
    "matched_records": "NHTSA-R2024-001",
})

LLM_PATTERN = r".*vehicle-history screening engine.*"

# A successful authoritative source whose body references the VIN, so verdicts
# have at least one VIN-specific retrieved source.
SOURCE_OK = r".*nhtsa\.gov.*"
SOURCE_BODY = "No open recalls for VIN " + VIN + ". History clean."


def with_source(vm, body=SOURCE_BODY):
    vm.mock_web(SOURCE_OK, {"method": "GET", "status": 200, "body": body})


@pytest.fixture
def vh(direct_vm, direct_deploy):
    vm = direct_vm
    vm.mock_llm(LLM_PATTERN, VERDICT_CLEAN)
    with_source(vm)
    c = direct_deploy(CONTRACT)
    return vm, c
