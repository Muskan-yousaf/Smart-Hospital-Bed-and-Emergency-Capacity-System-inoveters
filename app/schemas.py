from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

Urgency = Literal["low", "medium", "high", "critical"]
Role = Literal["patient", "coordinator", "hospital_staff", "admin"]


# ---------------- Users ----------------
class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=6, max_length=128)
    full_name: str = Field("", max_length=100)
    phone: str = Field("", max_length=30)
    role: Literal["patient", "coordinator"] = "patient"


class AdminCreateUserIn(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=6, max_length=128)
    full_name: str = Field("", max_length=100)
    phone: str = Field("", max_length=30)
    role: Role
    hospital_id: int | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    full_name: str
    phone: str
    role: str
    hospital_id: int | None
    is_active: bool
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    hospital_id: int | None
    full_name: str


# ---------------- Hospitals & capacity ----------------
class CapacityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    resource_type: str
    total_capacity: int
    occupied_capacity: int
    reserved_capacity: int
    unavailable_capacity: int
    available_capacity: int
    last_updated: datetime
    outdated: bool = False


class CapacityUpdateIn(BaseModel):
    total_capacity: int | None = Field(None, ge=0, le=10000)
    occupied_capacity: int | None = Field(None, ge=0, le=10000)
    unavailable_capacity: int | None = Field(None, ge=0, le=10000)


class HospitalIn(BaseModel):
    name: str = Field(min_length=3, max_length=150)
    location: str = Field("", max_length=200)
    city: str = Field("", max_length=100)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    contact: str = Field("", max_length=50)
    services: str = ""
    emergency_available: bool = True


class HospitalUpdateIn(BaseModel):
    """Fields hospital staff may edit about their own hospital."""
    contact: str | None = Field(None, max_length=50)
    services: str | None = None
    emergency_available: bool | None = None
    location: str | None = Field(None, max_length=200)


class HospitalSignupIn(HospitalIn):
    """Public hospital registration. Creates a pending hospital + its first staff account."""
    staff_username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    staff_password: str = Field(min_length=6, max_length=128)
    staff_full_name: str = Field("", max_length=100)


class HospitalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    location: str
    city: str
    latitude: float
    longitude: float
    contact: str
    services: str
    verification_status: str
    emergency_available: bool
    account_status: str
    created_at: datetime
    capacities: list[CapacityOut] = []


# ---------------- Matching & AI ----------------
class MatchIn(BaseModel):
    required_resources: list[str] = Field(min_length=1, description="e.g. ['icu','ventilator']")
    required_service: str | None = None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    urgency: Urgency = "medium"
    max_distance_km: float = Field(50, gt=0, le=1000)
    emergency_only: bool = False


class ClassifyIn(BaseModel):
    description: str = Field(max_length=5000)


# ---------------- Referral requests ----------------
class RequestIn(BaseModel):
    patient_reference: str = Field(min_length=1, max_length=100)
    description: str = Field("", max_length=5000)
    required_resources: list[str] = Field(default_factory=list)
    required_resource: str | None = None  # kept for older clients; merged into required_resources
    required_service: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    urgency: Urgency = "medium"
    hospital_id: int


class RequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patient_reference: str
    description: str
    summary: str
    required_resource: str
    resource_list: list[str]
    required_service: str | None
    urgency: str
    latitude: float | None
    longitude: float | None
    distance_km: float | None
    match_percent: int | None
    selected_hospital_id: int | None
    hospital_name: str | None
    request_status: str
    reserved: bool
    confirmation_code: str
    note: str
    created_by: int
    created_at: datetime
    accepted_at: datetime | None
    expires_at: datetime | None
    transferred_at: datetime | None
    admitted_at: datetime | None
    discharged_at: datetime | None
    response_seconds: float | None


class RespondIn(BaseModel):
    accept: bool
    note: str = Field("", max_length=500)


class AdmitIn(BaseModel):
    confirmation_code: str | None = None


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    message: str
    request_id: int | None
    is_read: bool
    created_at: datetime
