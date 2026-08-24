# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
import json
from dataclasses import dataclass
from genlayer import *


@allow_storage
@dataclass
class Vehicle:
    vehicle_id: str
    requester: str
    vin: str
    make: str
    model: str
    year: str
    status: str
    verdict: str


class VehicleHistory(gl.Contract):
    vehicles: TreeMap[str, str]
    records: TreeMap[str, str]
    vehicle_count: u256

    STATUSES = ("CLEAN", "DAMAGED", "FLOODED", "SALVAGE", "INCONCLUSIVE")
    SEVERITIES = ("NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL")
    # Authoritative vehicle-history / recall registries queried for every check.
    AUTHORITATIVE_SOURCES = (
        "https://www.carfax.com/vin/",
        "https://www.nhtsa.gov/recalls?nhtsaId=",
        "https://www.copart.com/vehicleFinder/search?query=",
        "https://www.iaai.com/Search?keyword=",
    )

    def __init__(self):
        pass

    def _decode_body(self, content) -> str:
        body = getattr(content, "body", None)
        if body is None:
            return str(content)
        if isinstance(body, bytes):
            return body.decode("utf-8", errors="replace")
        return str(body)

    def _valid_vin(self, vin: str) -> str:
        vin = vin.strip().upper()
        if len(vin) != 17:
            raise gl.vm.UserError("VIN must be exactly 17 characters")
        for ch in vin:
            if ch not in "ABCDEFGHJKLMNPRSTUVWXYZ0123456789":
                raise gl.vm.UserError("VIN contains invalid characters")
        return vin

    def _authoritative_urls(self, vin: str) -> list:
        return [base + vin for base in self.AUTHORITATIVE_SOURCES]

    def _check(self, vin: str, make: str, model: str, year: str) -> dict:
        def gather_and_check() -> dict:
            sources = []
            texts = []
            for url in self._authoritative_urls(vin):
                try:
                    content = gl.nondet.web.get(url)
                    body = self._decode_body(content)[:1200]
                    texts.append(f"[{url}]\n{body}")
                    sources.append({"url": url, "retrieved": True, "excerpt": body[:400]})
                except Exception:
                    texts.append(f"[{url}] [FETCH_FAILED]")
                    sources.append({"url": url, "retrieved": False, "excerpt": ""})

            task = f"""
You are a vehicle-history screening engine. Base the verdict ONLY on the
authoritative sources below (CARFAX vehicle history, NHTSA recall lookup,
Copart / IAA salvage-auction listings), which were queried for this VIN.
Cross-reference them for accident damage, flood damage, salvage / rebuilt
titles, and open recalls.

If NO source was retrieved, or the retrieved sources do not cover this VIN, you
MUST return status "INCONCLUSIVE" — never report a vehicle as clean without
evidence.

VIN: {vin}
MAKE: {make or "[none provided]"}
MODEL: {model or "[none provided]"}
YEAR: {year or "[none provided]"}

SOURCES:
{chr(10).join(texts) if texts else "[none]"}

Evaluate: is the vehicle damaged, flood-damaged, salvage-titled, or under an
open recall? How severe is the finding, and which specific records matched?
Be explicit and never invent record identifiers.

Respond ONLY in this JSON format with exact fields:
{{
    "status": "CLEAN" | "DAMAGED" | "FLOODED" | "SALVAGE" | "INCONCLUSIVE",
    "recalled": bool,
    "severity": "NONE" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
    "matched_records": [str],
    "reasoning": str
}}

When status is "INCONCLUSIVE", set recalled=false, severity="NONE",
matched_records=[].
"""
            result = gl.nondet.exec_prompt(task, response_format="json")
            if isinstance(result, str):
                result = json.loads(result.replace("```json", "").replace("```", ""))
            if not isinstance(result, dict):
                raise gl.vm.UserError("[LLM_ERROR] LLM returned non-dict result")
            result["sources"] = sources
            return result

        principle = (
            "Two results are equivalent if status "
            "(CLEAN/DAMAGED/FLOODED/SALVAGE/INCONCLUSIVE), recalled (bool), and "
            "severity (NONE/LOW/MEDIUM/HIGH/CRITICAL) match exactly, and "
            "matched_records contains the same record identifiers (order-insensitive). "
            "reasoning and sources may differ in wording."
        )
        return gl.eq_principle.prompt_comparative(gather_and_check, principle)

    def _normalize_verdict(self, v: dict) -> dict:
        recalled = bool(v.get("recalled", False))
        severity = str(v.get("severity", "NONE")).upper()
        if severity not in self.SEVERITIES:
            severity = "NONE"
        matched = v.get("matched_records", [])
        if not isinstance(matched, list):
            matched = [str(matched)]
        matched = [str(x) for x in matched]

        status = str(v.get("status", "")).upper()
        if status not in self.STATUSES:
            if recalled:
                status = "DAMAGED"
            elif severity == "NONE" and not matched:
                status = "CLEAN"
            else:
                status = "INCONCLUSIVE"
        # Keep the enum self-consistent.
        if status == "CLEAN":
            recalled = False
            severity = "NONE"
            matched = []
        if status == "INCONCLUSIVE":
            recalled = False
            severity = "NONE"
            matched = []

        sources = v.get("sources", [])
        if not isinstance(sources, list):
            sources = []
        norm_sources = []
        for s in sources:
            if not isinstance(s, dict):
                continue
            norm_sources.append({
                "url": str(s.get("url", "")),
                "retrieved": bool(s.get("retrieved", False)),
                "excerpt": str(s.get("excerpt", ""))[:400],
            })

        return {
            "status": status,
            "recalled": recalled,
            "severity": severity,
            "matched_records": matched,
            "sources": norm_sources,
            "reasoning": str(v.get("reasoning", "")),
        }

    @gl.public.write
    def submit_vehicle(self, vin: str, make: str, model: str, year: str):
        vin = self._valid_vin(vin)
        if not make or not make.strip():
            raise gl.vm.UserError("Make is required")
        sender = gl.message.sender_address
        self.vehicle_count += 1
        vehicle_id = str(self.vehicle_count)

        vehicle = Vehicle(
            vehicle_id=vehicle_id,
            requester=sender.as_hex,
            vin=vin,
            make=make.strip(),
            model=str(model or "").strip(),
            year=str(year or "").strip(),
            status="PENDING",
            verdict="",
        )
        self.vehicles[vehicle_id] = json.dumps(vehicle.__dict__)

    @gl.public.write
    def process_vehicle(self, vehicle_id: str):
        vehicle_id = str(vehicle_id)
        vehicle = json.loads(self.vehicles.get(vehicle_id, "{}"))
        if not vehicle:
            raise gl.vm.UserError("Check not found")
        if vehicle["status"] != "PENDING":
            raise gl.vm.UserError("Already processed")

        verdict = self._normalize_verdict(
            self._check(vehicle["vin"], vehicle["make"], vehicle["model"], vehicle["year"])
        )

        # Reusable record is keyed by normalized VIN. Guard against an unrelated
        # caller (or a VIN collision with different identity) silently replacing
        # a settled record: only its original requester may update it, and anyone
        # may improve an INCONCLUSIVE one.
        key = vehicle["vin"]
        existing = json.loads(self.records.get(key, "{}"))
        if existing:
            settled = existing.get("status") != "INCONCLUSIVE"
            same_owner = existing.get("requester") == vehicle["requester"]
            if settled and not same_owner:
                raise gl.vm.UserError(
                    "Reusable record for this VIN is already settled by another requester"
                )

        vehicle["status"] = "COMPLETED"
        vehicle["verdict"] = json.dumps(verdict, sort_keys=True)
        self.vehicles[vehicle_id] = json.dumps(vehicle)

        record = dict(verdict)
        record["vin"] = vehicle["vin"]
        record["make"] = vehicle["make"]
        record["model"] = vehicle["model"]
        record["year"] = vehicle["year"]
        record["requester"] = vehicle["requester"]
        record["from_check"] = vehicle["vehicle_id"]
        self.records[key] = json.dumps(record, sort_keys=True)

    @gl.public.view
    def get_vehicle(self, vehicle_id: str) -> str:
        return self.vehicles.get(str(vehicle_id), "{}")

    @gl.public.view
    def get_record(self, vin: str) -> str:
        return self.records.get(self._valid_vin(vin), "{}")

    @gl.public.view
    def get_vehicle_count(self) -> int:
        return self.vehicle_count

    @gl.public.view
    def get_stats(self) -> dict:
        clean = 0
        damaged = 0
        flooded = 0
        salvage = 0
        inconclusive = 0
        for v in self.vehicles.values():
            r = json.loads(v)
            if r["status"] == "COMPLETED" and r["verdict"]:
                verdict = json.loads(r["verdict"])
                st = verdict.get("status", "INCONCLUSIVE")
                if st == "CLEAN":
                    clean += 1
                elif st == "DAMAGED":
                    damaged += 1
                elif st == "FLOODED":
                    flooded += 1
                elif st == "SALVAGE":
                    salvage += 1
                else:
                    inconclusive += 1
        return {
            "total": len(self.vehicles),
            "completed": clean + damaged + flooded + salvage + inconclusive,
            "clean": clean,
            "damaged": damaged,
            "flooded": flooded,
            "salvage": salvage,
            "inconclusive": inconclusive,
            "records": len(self.records),
        }
