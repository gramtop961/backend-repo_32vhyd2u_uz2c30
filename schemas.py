"""
Rail Operations Schemas

Each class defines a MongoDB collection (lowercase class name).
"""
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Literal
from datetime import datetime

# Core domain
class Section(BaseModel):
    id: str = Field(..., description="Unique section identifier (e.g., S12)")
    name: str = Field(..., description="Human-friendly name")
    length_km: float = Field(..., ge=0)
    single_track: bool = Field(True)
    max_speed_kmh: float = Field(120, ge=10)
    crossing_loops: List[str] = Field(default_factory=list, description="Station/loop ids available for crossing")

class Train(BaseModel):
    id: str = Field(..., description="Train ID")
    service_type: Literal["passenger", "freight", "maintenance"] = "passenger"
    priority: int = Field(5, ge=1, le=10, description="Higher = more priority")
    length_m: Optional[int] = None
    max_speed_kmh: Optional[float] = None
    origin: str
    destination: str
    planned_departure: datetime
    planned_arrival: Optional[datetime] = None
    route: List[str] = Field(..., description="Ordered list of section ids")
    status: Literal["scheduled", "running", "delayed", "completed", "cancelled"] = "scheduled"

class Incident(BaseModel):
    id: str
    type: Literal["block", "speed_restriction", "weather", "signal_failure", "rolling_stock"]
    section_id: Optional[str] = None
    start_time: datetime
    end_time: Optional[datetime] = None
    details: Dict = Field(default_factory=dict)

class ScheduleLeg(BaseModel):
    train_id: str
    section_id: str
    enter_time: datetime
    exit_time: datetime
    meet_pass_at: Optional[str] = None  # loop/station id

class Schedule(BaseModel):
    scenario_id: Optional[str] = None
    legs: List[ScheduleLeg]
    objective: Dict = Field(default_factory=dict)  # KPIs like total_delay, throughput
    created_at: datetime = Field(default_factory=datetime.utcnow)

# What-if and scenarios
class Scenario(BaseModel):
    id: Optional[str] = None
    name: str
    description: Optional[str] = None
    trains: List[Train]
    incidents: List[Incident] = Field(default_factory=list)
    overrides: Dict = Field(default_factory=dict)
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

# Audit and KPIs
class AuditEvent(BaseModel):
    action: str
    actor: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    payload: Dict = Field(default_factory=dict)

class KPIReport(BaseModel):
    time_range: Dict
    punctuality: float
    avg_delay_min: float
    throughput_trains: int
    section_utilization: Dict[str, float] = Field(default_factory=dict)
    generated_at: datetime = Field(default_factory=datetime.utcnow)
