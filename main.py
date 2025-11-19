import os
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import db, create_document, get_documents
from schemas import Train, Section, Incident, Scenario, Schedule, ScheduleLeg, AuditEvent, KPIReport

app = FastAPI(title="Rail Decision Support API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"service": "Rail Decision Support API", "version": "0.2.0"}

# ------------------ Simple OR-based scheduler (greedy feasible) ------------------
# Demonstrator: respects single-track separation, priorities, and simple overrides

class OptimizeRequest(BaseModel):
    scenario: Scenario

class OptimizeResponse(BaseModel):
    schedule: Schedule
    explanation: str

SAFE_HEADWAY_MIN = 5  # minutes separation buffer between trains entering same single-track section


def compute_run_time_minutes(section: Section, train: Train) -> int:
    speed = min(section.max_speed_kmh, train.max_speed_kmh or section.max_speed_kmh)
    hours = section.length_km / max(speed, 1)
    return max(1, int(hours * 60))


def parse_fixed_overrides(overrides: Dict[str, Any]) -> Dict[str, datetime]:
    fixed = {}
    try:
        for entry in overrides.get("fixed_enters", []):
            key = f"{entry['train_id']}::{entry['section_id']}"
            fixed[key] = datetime.fromisoformat(entry["enter_time"])  # ISO string
    except Exception:
        pass
    return fixed


def generate_feasible_schedule(scenario: Scenario) -> Schedule:
    # Index sections
    sections_map: Dict[str, Section] = {s.id: s for s in get_domain_sections()}

    # Sort trains by priority (high first) then planned departure
    trains_sorted = sorted(scenario.trains, key=lambda t: (-t.priority, t.planned_departure))

    legs: List[ScheduleLeg] = []
    # Maintain last exit time per (section) to enforce single-track headway
    last_exit: Dict[str, datetime] = {}

    fixed_enters = parse_fixed_overrides(scenario.overrides or {})

    for tr in trains_sorted:
        current_time = tr.planned_departure
        for sec_id in tr.route:
            sec = sections_map.get(sec_id)
            if not sec:
                raise HTTPException(400, f"Unknown section {sec_id}")

            run_min = compute_run_time_minutes(sec, tr)

            # Consider fixed override for enter_time
            key = f"{tr.id}::{sec_id}"
            enter_time = fixed_enters.get(key, current_time)

            # Enforce headway on single track
            if sec.single_track:
                prev_exit = last_exit.get(sec_id)
                if prev_exit and enter_time < prev_exit + timedelta(minutes=SAFE_HEADWAY_MIN):
                    enter_time = prev_exit + timedelta(minutes=SAFE_HEADWAY_MIN)

            exit_time = enter_time + timedelta(minutes=run_min)

            legs.append(ScheduleLeg(train_id=tr.id, section_id=sec_id, enter_time=enter_time, exit_time=exit_time))
            last_exit[sec_id] = exit_time
            current_time = exit_time

    # Compute basic KPIs
    total_delay = 0.0
    # Throughput approximation: number of trains that reached their final section
    last_sections = {t.id: (t.route[-1] if t.route else None) for t in scenario.trains}
    finished_trains = set(
        l.train_id for l in legs if last_sections.get(l.train_id) == l.section_id
    )
    throughput = len(finished_trains)
    objective = {"total_delay_min": total_delay, "throughput": throughput}
    return Schedule(scenario_id=scenario.id, legs=legs, objective=objective)


def get_domain_sections() -> List[Section]:
    # Fetch from DB; if empty, seed defaults
    existing = get_documents("section", {}) if db else []
    if not existing:
        defaults = [
            Section(id="S1", name="Alpha-Loop", length_km=10.0, single_track=True, max_speed_kmh=110, crossing_loops=["A"]),
            Section(id="S2", name="Beta-Plain", length_km=18.0, single_track=True, max_speed_kmh=120, crossing_loops=["B"]),
            Section(id="S3", name="Gamma-Hill", length_km=12.0, single_track=True, max_speed_kmh=90, crossing_loops=["C"]),
        ]
        for s in defaults:
            try:
                create_document("section", s)
            except Exception:
                pass
        return defaults
    else:
        res: List[Section] = []
        for d in existing:
            try:
                # Normalize dict from Mongo into Section
                payload = {k: d.get(k) for k in Section.model_fields.keys()}
                res.append(Section(**payload))
            except Exception:
                continue
        return res


@app.get("/api/sections", response_model=List[Section])
def list_sections():
    return get_domain_sections()


@app.post("/api/optimize", response_model=OptimizeResponse)
def optimize(req: OptimizeRequest):
    sched = generate_feasible_schedule(req.scenario)
    explanation = (
        "Generated a conflict-minimized timetable using priority-first sequencing, safety headways, and controller overrides where provided."
    )
    # Audit
    try:
        create_document("auditevent", AuditEvent(action="optimize", payload={"scenario_id": req.scenario.id or "ad-hoc"}))
    except Exception:
        pass
    return OptimizeResponse(schedule=sched, explanation=explanation)


# What-if simulation: tweak a scenario (e.g., delay a train) and re-optimize
class WhatIfRequest(BaseModel):
    scenario: Scenario
    delay_train_id: Optional[str] = None
    delay_minutes: int = 0


@app.post("/api/whatif", response_model=OptimizeResponse)
def what_if(req: WhatIfRequest):
    sc = req.scenario
    if req.delay_train_id and req.delay_minutes:
        for t in sc.trains:
            if t.id == req.delay_train_id:
                t.planned_departure = t.planned_departure + timedelta(minutes=req.delay_minutes)
                break
    sched = generate_feasible_schedule(sc)
    try:
        create_document("auditevent", AuditEvent(action="what_if", payload={"train": req.delay_train_id, "delay": req.delay_minutes}))
    except Exception:
        pass
    return OptimizeResponse(schedule=sched, explanation="Scenario re-optimized with applied delay.")


# KPIs endpoint (mock aggregation over latest schedule records)
@app.get("/api/kpis", response_model=KPIReport)
def kpis():
    now = datetime.utcnow()
    report = KPIReport(
        time_range={"from": (now - timedelta(hours=1)).isoformat(), "to": now.isoformat()},
        punctuality=0.95,
        avg_delay_min=3.2,
        throughput_trains=24,
        section_utilization={"S1": 0.7, "S2": 0.8, "S3": 0.65},
    )
    try:
        create_document("auditevent", AuditEvent(action="kpi_view"))
    except Exception:
        pass
    return report


@app.get("/api/audit")
def audit_log(limit: int = 50):
    try:
        if db is None:
            return {"items": []}
        items = get_documents("auditevent", {})
        # Sort by timestamp desc if present
        def _ts(i):
            return i.get("timestamp") or i.get("created_at") or datetime.utcnow()
        items_sorted = sorted(items, key=_ts, reverse=True)[:limit]
        # Convert ObjectId to str if present
        for it in items_sorted:
            if "_id" in it:
                it["_id"] = str(it["_id"])
        return {"items": items_sorted}
    except Exception as e:
        return {"items": [], "error": str(e)}


# Health and DB test
@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = db.name if hasattr(db, 'name') else "❌ Unknown"
            response["connection_status"] = "Connected"
            collections = db.list_collection_names()
            response["collections"] = collections[:10]
            response["database"] = "✅ Connected & Working"
    except Exception as e:
        response["database"] = f"⚠️  Connected but Error: {str(e)[:80]}"
    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
