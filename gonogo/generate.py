"""Synthetic digital-thread dataset for a fictional 3U CubeSat mission ("KESTREL-3U").

Everything here is invented. No real project, supplier or organisation data is used.
The dataset is deterministic for a given seed: a hand-written engineering skeleton
(requirements, parts, dependencies) plus seeded choices (verification methods/status,
risk scores) and a set of deliberately planted defects ("traps") that a traceability
tool should find. The planted defects are listed in ``ground_truth.json`` so that the
evaluation can check answers against something that was NOT computed by the graph.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

SEED = 2026

SUBSYSTEMS = [
    ("EPS", "Electrical Power Subsystem"),
    ("ADCS", "Attitude Determination and Control Subsystem"),
    ("COMMS", "Communications Subsystem"),
    ("OBC", "On-Board Computer and Data Handling"),
    ("STR", "Structure and Mechanisms"),
    ("THERM", "Thermal Control Subsystem"),
    ("PAY", "Multispectral Imager Payload"),
]

# (id, level, subsystem, parent id or None, text)
REQUIREMENTS = [
    ("REQ-MIS-01", "MISSION", "SYS", None, "The mission shall acquire multispectral images of agricultural regions at 30 m ground sampling distance from a 500 km sun-synchronous orbit."),
    ("REQ-MIS-02", "MISSION", "SYS", None, "The mission shall operate for at least 2 years in orbit."),
    ("REQ-MIS-03", "MISSION", "SYS", None, "The spacecraft shall comply with the 3U CubeSat design specification and deorbit within 25 years."),
    ("REQ-SYS-01", "SYSTEM", "SYS", "REQ-MIS-01", "The spacecraft shall point the imager to a ground target with an accuracy better than 0.5 deg (3 sigma)."),
    ("REQ-SYS-02", "SYSTEM", "SYS", "REQ-MIS-01", "The spacecraft shall downlink at least 800 MB of payload data per day."),
    ("REQ-SYS-03", "SYSTEM", "SYS", "REQ-MIS-02", "The spacecraft shall provide a positive power balance in all nominal operating modes over one orbit."),
    ("REQ-SYS-04", "SYSTEM", "SYS", "REQ-MIS-02", "All units shall remain within their operational temperature limits in hot and cold cases."),
    ("REQ-SYS-05", "SYSTEM", "SYS", "REQ-MIS-03", "The spacecraft mass shall not exceed 4.0 kg."),
    ("REQ-SYS-06", "SYSTEM", "SYS", "REQ-MIS-02", "The spacecraft shall survive a total ionising dose of 10 krad(Si) without loss of function."),
    ("REQ-SYS-07", "SYSTEM", "SYS", "REQ-MIS-03", "The spacecraft shall withstand the launch vehicle random vibration qualification levels."),
    ("REQ-SYS-08", "SYSTEM", "SYS", "REQ-MIS-01", "The spacecraft shall store at least 8 GB of payload data on board."),
    # EPS
    ("REQ-EPS-01", "SUBSYSTEM", "EPS", "REQ-SYS-03", "The solar arrays shall generate at least 7.5 W orbit-average power at end of life."),
    ("REQ-EPS-02", "SUBSYSTEM", "EPS", "REQ-SYS-03", "The battery shall provide 30 Wh usable energy with depth of discharge below 30 percent."),
    ("REQ-EPS-03", "SUBSYSTEM", "EPS", "REQ-SYS-03", "The power distribution unit shall provide latch-up protected 3.3 V and 5 V regulated buses."),
    ("REQ-EPS-04", "SUBSYSTEM", "EPS", "REQ-SYS-04", "The battery temperature shall be kept above 0 degC during charging."),
    ("REQ-EPS-05", "SUBSYSTEM", "EPS", "REQ-MIS-03", "The EPS shall keep the spacecraft unpowered until deployment switch release."),
    # ADCS
    ("REQ-ADCS-01", "SUBSYSTEM", "ADCS", "REQ-SYS-01", "The ADCS shall determine attitude with an accuracy better than 0.1 deg (3 sigma)."),
    ("REQ-ADCS-02", "SUBSYSTEM", "ADCS", "REQ-SYS-01", "The ADCS shall control attitude with an accuracy better than 0.4 deg (3 sigma)."),
    ("REQ-ADCS-03", "SUBSYSTEM", "ADCS", "REQ-MIS-02", "The ADCS shall detumble the spacecraft from 10 deg/s to below 0.5 deg/s within 3 orbits."),
    ("REQ-ADCS-04", "SUBSYSTEM", "ADCS", "REQ-SYS-03", "The ADCS shall support sun-pointing safe mode for battery charging."),
    ("REQ-ADCS-05", "SUBSYSTEM", "ADCS", "REQ-SYS-01", "The ADCS shall provide orbit position knowledge better than 50 m from GNSS."),
    # COMMS
    ("REQ-COMMS-01", "SUBSYSTEM", "COMMS", "REQ-SYS-02", "The X-band transmitter shall provide a downlink data rate of at least 50 Mbps."),
    ("REQ-COMMS-02", "SUBSYSTEM", "COMMS", "REQ-SYS-02", "The UHF transceiver shall provide telemetry and telecommand at 9.6 kbps."),
    ("REQ-COMMS-03", "SUBSYSTEM", "COMMS", "REQ-SYS-02", "The X-band link budget shall close with at least 3 dB margin at 10 deg elevation."),
    ("REQ-COMMS-04", "SUBSYSTEM", "COMMS", "REQ-MIS-03", "The UHF antenna shall deploy reliably after a 30 minute post-separation delay."),
    # OBC
    ("REQ-OBC-01", "SUBSYSTEM", "OBC", "REQ-SYS-08", "The on-board computer shall provide 16 GB of non-volatile mass memory."),
    ("REQ-OBC-02", "SUBSYSTEM", "OBC", "REQ-SYS-06", "The on-board computer shall recover from single event upsets via watchdog reset within 10 s."),
    ("REQ-OBC-03", "SUBSYSTEM", "OBC", "REQ-MIS-02", "The flight software shall support in-orbit software update with rollback."),
    ("REQ-OBC-04", "SUBSYSTEM", "OBC", "REQ-SYS-01", "The flight software shall time-tag images with an accuracy better than 1 ms (UTC)."),
    # planted: leaf requirement that no part satisfies
    ("REQ-OBC-05", "SUBSYSTEM", "OBC", "REQ-MIS-02", "The flight software shall autonomously enter safe mode after 72 h without ground contact."),
    # STR
    ("REQ-STR-01", "SUBSYSTEM", "STR", "REQ-SYS-07", "The structure first natural frequency shall be above 100 Hz."),
    ("REQ-STR-02", "SUBSYSTEM", "STR", "REQ-SYS-05", "The primary structure mass shall not exceed 0.45 kg."),
    ("REQ-STR-03", "SUBSYSTEM", "STR", "REQ-MIS-03", "The structure rails shall conform to the 3U deployer interface dimensions."),
    # THERM
    ("REQ-THERM-01", "SUBSYSTEM", "THERM", "REQ-SYS-04", "The thermal design shall keep the battery between 0 degC and 40 degC."),
    ("REQ-THERM-02", "SUBSYSTEM", "THERM", "REQ-SYS-04", "The thermal design shall keep the imager detector below 25 degC during acquisition."),
    ("REQ-THERM-03", "SUBSYSTEM", "THERM", "REQ-SYS-04", "Survival heaters shall be thermostatically controlled independent of flight software."),
    # PAY
    ("REQ-PAY-01", "SUBSYSTEM", "PAY", "REQ-MIS-01", "The imager shall provide 30 m ground sampling distance at 500 km altitude."),
    ("REQ-PAY-02", "SUBSYSTEM", "PAY", "REQ-MIS-01", "The imager shall acquire 4 spectral bands between 450 nm and 900 nm."),
    ("REQ-PAY-03", "SUBSYSTEM", "PAY", "REQ-SYS-06", "The imager electronics shall tolerate 10 krad(Si) total ionising dose."),
    ("REQ-PAY-04", "SUBSYSTEM", "PAY", "REQ-SYS-08", "The imager shall compress images losslessly with a ratio of at least 2:1."),
]

# (id, name, subsystem, supplier, depends_on list, satisfies list)
PARTS = [
    ("PART-EPS-SA-01", "Deployable solar array wing (2x3U)", "EPS", "Helios Panels", ["PART-STR-HNG-01"], ["REQ-EPS-01"]),
    ("PART-EPS-BAT-01", "Li-ion battery pack 8S1P", "EPS", "Volta Cells", ["PART-THERM-HTR-01"], ["REQ-EPS-02", "REQ-EPS-04"]),
    ("PART-EPS-PCDU-01", "Power conditioning and distribution unit", "EPS", "Volta Cells", ["PART-EPS-BAT-01", "PART-EPS-SA-01"], ["REQ-EPS-03"]),
    ("PART-EPS-SW-01", "Deployment kill switch assembly", "EPS", "Orbital Mechanix", [], ["REQ-EPS-05"]),
    ("PART-ADCS-ST-01", "Nano star tracker", "ADCS", "Polaris Optics", ["PART-EPS-PCDU-01", "PART-OBC-CPU-01"], ["REQ-ADCS-01"]),
    ("PART-ADCS-RW-01", "Reaction wheel triad", "ADCS", "Gyrodyne Labs", ["PART-EPS-PCDU-01"], ["REQ-ADCS-02"]),
    ("PART-ADCS-MTQ-01", "Magnetorquer set", "ADCS", "Gyrodyne Labs", ["PART-EPS-PCDU-01"], ["REQ-ADCS-03"]),
    ("PART-ADCS-SS-01", "Fine sun sensor set", "ADCS", "Polaris Optics", ["PART-EPS-PCDU-01"], ["REQ-ADCS-04"]),
    ("PART-ADCS-GNSS-01", "GNSS receiver and patch antenna", "ADCS", "Meridian Nav", ["PART-EPS-PCDU-01"], ["REQ-ADCS-05", "REQ-OBC-04"]),
    ("PART-ADCS-CTRL-01", "ADCS controller board", "ADCS", "Gyrodyne Labs", ["PART-EPS-PCDU-01", "PART-ADCS-ST-01", "PART-ADCS-RW-01", "PART-ADCS-MTQ-01", "PART-ADCS-SS-01"], ["REQ-ADCS-02", "REQ-ADCS-03", "REQ-ADCS-04"]),
    ("PART-COMMS-XTX-01", "X-band transmitter", "COMMS", "Skylink RF", ["PART-EPS-PCDU-01", "PART-OBC-CPU-01"], ["REQ-COMMS-01", "REQ-COMMS-03"]),
    ("PART-COMMS-XANT-01", "X-band patch array antenna", "COMMS", "Skylink RF", ["PART-COMMS-XTX-01"], ["REQ-COMMS-03"]),
    ("PART-COMMS-UHF-01", "UHF transceiver", "COMMS", "Skylink RF", ["PART-EPS-PCDU-01"], ["REQ-COMMS-02"]),
    ("PART-COMMS-UANT-01", "Deployable UHF dipole antenna", "COMMS", "Orbital Mechanix", ["PART-COMMS-UHF-01", "PART-STR-HNG-01"], ["REQ-COMMS-04"]),
    ("PART-OBC-CPU-01", "On-board computer (rad-tolerant MCU)", "OBC", "Kernel Avionics", ["PART-EPS-PCDU-01"], ["REQ-OBC-02", "REQ-OBC-03"]),
    ("PART-OBC-MEM-01", "Mass memory board 16 GB", "OBC", "Kernel Avionics", ["PART-OBC-CPU-01"], ["REQ-OBC-01"]),
    ("PART-OBC-FSW-01", "Flight software build", "OBC", "In-house", ["PART-OBC-CPU-01"], ["REQ-OBC-03", "REQ-OBC-04"]),
    ("PART-STR-FRM-01", "3U aluminium 7075 frame", "STR", "Orbital Mechanix", [], ["REQ-STR-01", "REQ-STR-02", "REQ-STR-03"]),
    ("PART-STR-HNG-01", "Spring hinge and burn-wire release", "STR", "Orbital Mechanix", ["PART-EPS-PCDU-01"], []),
    ("PART-THERM-HTR-01", "Battery survival heater with thermostat", "THERM", "Thermaflex", [], ["REQ-THERM-01", "REQ-THERM-03"]),
    ("PART-THERM-MLI-01", "Multi-layer insulation blanket", "THERM", "Thermaflex", [], ["REQ-THERM-01"]),
    ("PART-THERM-RAD-01", "Imager detector radiator", "THERM", "Thermaflex", ["PART-STR-FRM-01"], ["REQ-THERM-02"]),
    ("PART-PAY-OPT-01", "Imager telescope optics", "PAY", "Lumen Space Optics", ["PART-STR-FRM-01"], ["REQ-PAY-01"]),
    ("PART-PAY-DET-01", "Multispectral CMOS detector", "PAY", "Lumen Space Optics", ["PART-PAY-OPT-01", "PART-THERM-RAD-01"], ["REQ-PAY-02"]),
    ("PART-PAY-PEB-01", "Payload electronics board", "PAY", "Kernel Avionics", ["PART-EPS-PCDU-01", "PART-PAY-DET-01", "PART-OBC-MEM-01"], ["REQ-PAY-03", "REQ-PAY-04"]),
    # planted: parts that satisfy nothing (orphans)
    ("PART-PAY-FW-02", "Filter wheel (descoped option)", "PAY", "Lumen Space Optics", ["PART-PAY-OPT-01"], []),
    ("PART-COMMS-SBAND-01", "S-band patch antenna (legacy)", "COMMS", "Skylink RF", [], []),
]
# PART-STR-HNG-01 satisfies nothing by design too, but other parts depend on it -> it is an
# "enabling part". The orphan check treats a part as orphan only if it satisfies no
# requirement AND no other part depends on it.

# (id, title, severity 1-5, likelihood 1-5, threatens list, mitigated_by list (filled later))
RISKS = [
    ("RISK-01", "Solar array hinge fails to deploy", 5, 2, ["REQ-EPS-01", "PART-STR-HNG-01"]),
    ("RISK-02", "Battery cell thermal runaway", 5, 1, ["REQ-EPS-02", "PART-EPS-BAT-01"]),
    ("RISK-03", "Star tracker blinding by Sun or Earth limb", 3, 4, ["REQ-ADCS-01"]),
    ("RISK-04", "Reaction wheel bearing degradation", 4, 3, ["REQ-ADCS-02", "PART-ADCS-RW-01"]),
    ("RISK-05", "X-band frequency licence delay", 4, 3, ["REQ-COMMS-01", "REQ-SYS-02"]),
    ("RISK-06", "Single event latch-up in OBC memory", 4, 3, ["REQ-OBC-02", "PART-OBC-MEM-01"]),
    ("RISK-07", "Mass growth beyond 4.0 kg", 3, 3, ["REQ-SYS-05"]),
    ("RISK-08", "Imager defocus after launch vibration", 4, 3, ["REQ-PAY-01", "PART-PAY-OPT-01"]),
    ("RISK-09", "UHF antenna burn-wire does not release", 4, 2, ["REQ-COMMS-04"]),
    ("RISK-10", "Detector overheating during long acquisitions", 3, 4, ["REQ-THERM-02", "REQ-PAY-02"]),
    ("RISK-11", "Single-supplier dependency on Gyrodyne Labs for ADCS actuators", 4, 3, ["PART-ADCS-RW-01", "PART-ADCS-MTQ-01", "PART-ADCS-CTRL-01"]),
    ("RISK-12", "GNSS receiver loses lock at high latitude", 2, 3, ["REQ-ADCS-05"]),
    ("RISK-13", "Flight software update corrupts boot image", 5, 2, ["REQ-OBC-03"]),
    ("RISK-14", "Battery capacity fade larger than predicted", 3, 3, ["REQ-EPS-02", "REQ-MIS-02"]),
]
HIGH_RISK_SCORE = 12  # severity * likelihood >= 12 is "high" (5x5 matrix, red zone)

# (id, title, status, affects list, rationale)
DECISIONS = [
    ("DEC-01", "Use body-mounted plus 2 deployable solar wings", "ACCEPTED", ["PART-EPS-SA-01", "REQ-EPS-01"], "Body-mounted only cannot meet 7.5 W orbit average."),
    ("DEC-02", "Select 8S1P Li-ion battery pack", "ACCEPTED", ["PART-EPS-BAT-01"], "Heritage cells with screening data available."),
    ("DEC-03", "Add redundant watchdog and memory scrubbing", "ACCEPTED", ["PART-OBC-CPU-01", "PART-OBC-MEM-01", "REQ-OBC-02"], "Mitigates SEU and latch-up in commercial memory."),
    ("DEC-04", "Use X-band for payload downlink", "ACCEPTED", ["PART-COMMS-XTX-01", "REQ-COMMS-01"], "S-band cannot meet 800 MB per day."),
    ("DEC-05", "Retain S-band as payload downlink backup", "ACCEPTED", ["PART-COMMS-SBAND-01"], "Backup if X-band licence is delayed."),
    ("DEC-06", "Passive thermal control with survival heater only", "ACCEPTED", ["PART-THERM-HTR-01", "PART-THERM-MLI-01"], "Lower power and complexity."),
    ("DEC-07", "Active heater control by flight software for detector", "ACCEPTED", ["PART-THERM-RAD-01", "PART-OBC-FSW-01"], "Detector needs tighter temperature band."),
    ("DEC-08", "Descope filter wheel", "ACCEPTED", ["PART-PAY-FW-02"], "Mass and reliability; bands defined by detector filters."),
    ("DEC-09", "Use aluminium 7075 frame instead of CFRP", "ACCEPTED", ["PART-STR-FRM-01", "REQ-STR-02"], "Cost and lead time."),
    ("DEC-10", "Use CFRP frame to save mass", "REJECTED", ["PART-STR-FRM-01"], "Superseded by DEC-09."),
]
# (a, b): a conflicts with b
CONFLICTS = [
    ("DEC-05", "DEC-04"),  # planted: both ACCEPTED, contradictory downlink baselines
    ("DEC-07", "DEC-06"),  # planted: both ACCEPTED, passive-only vs. active control
    ("DEC-10", "DEC-09"),  # resolved: DEC-10 rejected -> not an open conflict
]

# Mitigations: risk -> list of verification ids or decision ids (verification ids are filled below)
MITIGATIONS = {
    "RISK-01": ["VER-EPS-01-T", "VER-STR-HNG-T"],
    "RISK-02": ["DEC-02", "VER-EPS-02-T"],
    "RISK-03": ["VER-ADCS-01-A"],
    "RISK-04": [],                   # planted: high risk (12) without mitigation
    "RISK-05": ["DEC-05"],
    "RISK-06": ["DEC-03"],
    "RISK-07": ["VER-SYS-05-I"],
    "RISK-08": ["VER-PAY-01-T"],
    "RISK-09": ["VER-COMMS-04-T"],
    "RISK-10": ["DEC-07"],
    "RISK-11": [],                   # planted: high risk (12) without mitigation
    "RISK-12": [],                   # low risk without mitigation (not a defect)
    "RISK-13": ["VER-OBC-03-T"],
    "RISK-14": ["VER-EPS-02-A"],
}

# planted defects on verification coverage
UNVERIFIED = {"REQ-ADCS-05", "REQ-THERM-03", "REQ-PAY-04"}           # no verification at all
FAILED = {"REQ-COMMS-03": "VER-COMMS-03-A", "REQ-STR-01": "VER-STR-01-T"}  # a FAILED verification

METHOD_CODES = {"T": "Test", "A": "Analysis", "I": "Inspection", "R": "Review"}

DOCUMENTS = [
    ("DOC-01", "Power budget analysis", ["REQ-SYS-03", "REQ-EPS-01", "REQ-EPS-02", "PART-EPS-SA-01", "PART-EPS-BAT-01"],
     "Orbit-average power generation of the deployable solar wings is 8.1 W at end of life against a consumption of 6.4 W in imaging mode. Battery depth of discharge stays below 25 percent in eclipse."),
    ("DOC-02", "Pointing budget and ADCS design note", ["REQ-SYS-01", "REQ-ADCS-01", "REQ-ADCS-02", "PART-ADCS-ST-01", "PART-ADCS-RW-01"],
     "Star tracker noise and reaction wheel jitter dominate the pointing error budget. Sun exclusion angle of the star tracker constrains imaging geometry."),
    ("DOC-03", "X-band link budget", ["REQ-COMMS-01", "REQ-COMMS-03", "PART-COMMS-XTX-01", "PART-COMMS-XANT-01"],
     "At 10 degree elevation the link margin is 1.8 dB, below the required 3 dB. Antenna gain or ground station G/T must increase."),
    ("DOC-04", "Thermal analysis hot and cold cases", ["REQ-SYS-04", "REQ-THERM-01", "REQ-THERM-02", "PART-THERM-HTR-01", "PART-THERM-RAD-01"],
     "In the cold case the battery reaches minus 3 degC without heater. The detector radiator keeps the detector at 21 degC for 8 minute acquisitions."),
    ("DOC-05", "Structural analysis and vibration test plan", ["REQ-SYS-07", "REQ-STR-01", "PART-STR-FRM-01"],
     "Finite element modal analysis predicts a first mode of 96 Hz with the deployable solar wings stowed, below the 100 Hz requirement."),
    ("DOC-06", "Radiation analysis", ["REQ-SYS-06", "REQ-OBC-02", "REQ-PAY-03", "PART-OBC-MEM-01", "PART-PAY-PEB-01"],
     "Total ionising dose behind 2 mm aluminium is 6 krad over 2 years. Commercial memory requires scrubbing and latch-up protection."),
    ("DOC-07", "Mass budget", ["REQ-SYS-05", "REQ-STR-02"],
     "Current best estimate is 3.62 kg with 10 percent margin, giving 3.98 kg against the 4.0 kg limit."),
    ("DOC-08", "Flight software architecture", ["REQ-OBC-03", "REQ-OBC-04", "PART-OBC-FSW-01", "PART-OBC-CPU-01"],
     "Dual boot images with golden image fallback. Time synchronisation uses the GNSS pulse per second."),
    ("DOC-09", "Imager optical design report", ["REQ-PAY-01", "REQ-PAY-02", "PART-PAY-OPT-01", "PART-PAY-DET-01"],
     "Focal length 580 mm with 5.5 micrometre pixels gives 4.7 m at 500 km per pixel before binning; 6x6 binning yields about 30 m."),
    ("DOC-10", "Frequency coordination status", ["REQ-COMMS-01", "PART-COMMS-SBAND-01"],
     "The X-band filing is under coordination. The S-band antenna from the previous mission is kept on the bill of materials pending a decision."),
]

# planted: an anomaly observed during a verification that is recorded as PASSED
ANOMALIES = [
    ("ANOM-01", "Reaction wheel current spikes during thermal vacuum test", "VER-ADCS-02-T", ["PART-ADCS-RW-01"]),
    ("ANOM-02", "UHF antenna partial deployment in cold test", "VER-COMMS-04-T", ["PART-COMMS-UANT-01", "PART-STR-HNG-01"]),
    ("ANOM-03", "Star tracker false star detections near South Atlantic Anomaly simulation", "VER-ADCS-01-A", ["PART-ADCS-ST-01"]),
]


def _verifications(rng: random.Random) -> list[dict]:
    """One or two verifications per requirement (except the planted unverified ones)."""
    out: list[dict] = []
    for rid, level, sub, _parent, _text in REQUIREMENTS:
        if rid in UNVERIFIED:
            continue
        if level == "MISSION":
            methods = ["R"]
        else:
            k = rng.choice([1, 1, 2])
            methods = rng.sample(["T", "A", "I", "T", "A"], k=k)
            methods = list(dict.fromkeys(methods))
        tag = rid.removeprefix("REQ-")
        for m in methods:
            status = rng.choices(["PASSED", "PLANNED"], weights=[0.7, 0.3])[0]
            out.append({"id": f"VER-{tag}-{m}", "method": METHOD_CODES[m], "status": status, "req": rid})
    by_id = {v["id"]: v for v in out}
    # ensure every verification referenced by mitigations / anomalies / planted failures exists
    needed = {vid for lst in MITIGATIONS.values() for vid in lst if vid.startswith("VER-")}
    needed |= {a[2] for a in ANOMALIES} | set(FAILED.values())
    for vid in sorted(needed):
        if vid in by_id or vid == "VER-STR-HNG-T":
            continue
        tag, m = vid.removeprefix("VER-").rsplit("-", 1)
        v = {"id": vid, "method": METHOD_CODES[m], "status": "PASSED", "req": f"REQ-{tag}"}
        out.append(v)
        by_id[vid] = v
    for rid, vid in FAILED.items():
        by_id[vid]["status"] = "FAILED"
    for _aid, _t, vid, _p in ANOMALIES:
        by_id[vid]["status"] = "PASSED"  # planted: anomaly on a verification recorded as PASSED
    # hinge deployment test verifies the system vibration requirement indirectly (part-level test)
    out.append({"id": "VER-STR-HNG-T", "method": "Test", "status": "PASSED", "req": "REQ-SYS-07"})
    return sorted(out, key=lambda v: v["id"])


def build_dataset(seed: int = SEED) -> dict:
    rng = random.Random(seed)
    verifs = _verifications(rng)
    nodes: dict[str, list[dict]] = {
        "Subsystem": [{"id": s, "name": n, "text": f"{s} {n}"} for s, n in SUBSYSTEMS],
        "Requirement": [
            {"id": r, "level": lvl, "subsystem": sub, "text": t} for r, lvl, sub, _p, t in REQUIREMENTS
        ],
        "Verification": [
            {"id": v["id"], "method": v["method"], "status": v["status"],
             "text": f"{v['method']} verification of {v['req']} ({v['status']})"} for v in verifs
        ],
        "Part": [
            {"id": p, "name": n, "subsystem": s, "supplier": sup, "text": f"{n}. Subsystem {s}. Supplier {sup}."}
            for p, n, s, sup, _d, _sat in PARTS
        ],
        "Risk": [
            {"id": r, "title": t, "severity": sev, "likelihood": lik, "score": sev * lik, "text": t}
            for r, t, sev, lik, _th in RISKS
        ],
        "Decision": [
            {"id": d, "title": t, "status": st, "rationale": why, "text": f"{t}. {why}"}
            for d, t, st, _a, why in DECISIONS
        ],
        "Anomaly": [{"id": a, "title": t, "text": t} for a, t, _v, _p in ANOMALIES],
        "Document": [{"id": d, "title": t, "text": f"{t}. {body}"} for d, t, _refs, body in DOCUMENTS],
    }
    edges: list[tuple[str, str, str]] = []
    for r, _lvl, sub, parent, _t in REQUIREMENTS:
        if parent:
            edges.append((r, "DERIVES_FROM", parent))
        if sub != "SYS":
            edges.append((r, "ALLOCATED_TO", sub))
    for v in verifs:
        edges.append((v["req"], "VERIFIED_BY", v["id"]))
    for p, _n, s, _sup, deps, sats in PARTS:
        edges.append((p, "PART_OF", s))
        edges += [(p, "DEPENDS_ON", d) for d in deps]
        edges += [(p, "SATISFIES", r) for r in sats]
    for r, _t, _s, _l, threatens in RISKS:
        edges += [(r, "THREATENS", x) for x in threatens]
    for r, mits in MITIGATIONS.items():
        edges += [(r, "MITIGATED_BY", m) for m in mits]
    for d, _t, _st, affects, _w in DECISIONS:
        edges += [(d, "AFFECTS", x) for x in affects]
    edges += [(a, "CONFLICTS_WITH", b) for a, b in CONFLICTS]
    for a, _t, vid, parts in ANOMALIES:
        edges.append((a, "OBSERVED_IN", vid))
        edges += [(a, "INVOLVES", p) for p in parts]
    for d, _t, refs, _b in DOCUMENTS:
        edges += [(x, "DOCUMENTED_IN", d) for x in refs]
    return {"seed": seed, "mission": "KESTREL-3U (fictional)", "nodes": nodes,
            "edges": [{"src": s, "type": t, "dst": d} for s, t, d in edges]}


def write(out_dir: Path, seed: int = SEED) -> dict:
    from gonogo.truth import ground_truth  # local import to avoid a cycle

    data = build_dataset(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dataset.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    (out_dir / "ground_truth.json").write_text(json.dumps(ground_truth(data), indent=1), encoding="utf-8")
    return data


if __name__ == "__main__":
    d = write(Path(__file__).resolve().parents[1] / "data")
    print({k: len(v) for k, v in d["nodes"].items()}, "edges:", len(d["edges"]))
