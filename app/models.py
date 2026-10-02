from datetime import datetime, timezone
from sqlalchemy import String, Integer, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


RESOURCE_TYPES = ["general", "emergency", "icu", "nicu", "ventilator",
                  "operation_theatre", "isolation", "dialysis", "trauma", "ambulance"]
BED_TYPES = ["general", "emergency", "icu", "nicu", "isolation", "trauma"]

# request status flow:
# searching -> request_sent -> hospital_reviewing -> accepted -> patient_transferred -> admitted -> discharged
# others: rejected, cancelled, expired, no_capacity
OPEN_STATUSES = ("request_sent", "hospital_reviewing", "accepted", "patient_transferred")
CLOSED_STATUSES = ("admitted", "discharged", "rejected", "cancelled", "expired", "no_capacity")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    full_name: Mapped[str] = mapped_column(String(100), default="")
    phone: Mapped[str] = mapped_column(String(30), default="")
    role: Mapped[str] = mapped_column(String(20))  # patient | coordinator | hospital_staff | admin
    hospital_id: Mapped[int | None] = mapped_column(ForeignKey("hospitals.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Hospital(Base):
    __tablename__ = "hospitals"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), index=True)
    location: Mapped[str] = mapped_column(String(200), default="")
    city: Mapped[str] = mapped_column(String(100), default="")  # used for regional analytics
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    contact: Mapped[str] = mapped_column(String(50), default="")
    services: Mapped[str] = mapped_column(Text, default="")  # comma separated specialist services
    verification_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|verified|rejected
    emergency_available: Mapped[bool] = mapped_column(Boolean, default=True)
    account_status: Mapped[str] = mapped_column(String(20), default="active")  # active|suspended
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    capacities: Mapped[list["Capacity"]] = relationship(back_populates="hospital", cascade="all, delete-orphan",
                                                        order_by="Capacity.id")

    @property
    def service_list(self) -> list[str]:
        return [s.strip().lower() for s in self.services.split(",") if s.strip()]


class Capacity(Base):
    __tablename__ = "capacities"
    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    resource_type: Mapped[str] = mapped_column(String(30))
    total_capacity: Mapped[int] = mapped_column(Integer, default=0)
    occupied_capacity: Mapped[int] = mapped_column(Integer, default=0)
    reserved_capacity: Mapped[int] = mapped_column(Integer, default=0)      # temporary reservations
    unavailable_capacity: Mapped[int] = mapped_column(Integer, default=0)   # temporarily out of service
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    hospital: Mapped[Hospital] = relationship(back_populates="capacities")

    @property
    def available_capacity(self) -> int:
        return max(0, self.total_capacity - self.occupied_capacity
                   - self.reserved_capacity - self.unavailable_capacity)


class CapacitySnapshot(Base):
    """Point-in-time copy of a capacity row. Feeds trend charts and forecasting."""
    __tablename__ = "capacity_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    resource_type: Mapped[str] = mapped_column(String(30), index=True)
    total_capacity: Mapped[int] = mapped_column(Integer)
    occupied_capacity: Mapped[int] = mapped_column(Integer)
    reserved_capacity: Mapped[int] = mapped_column(Integer, default=0)
    available_capacity: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class ReferralRequest(Base):
    __tablename__ = "requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_reference: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")  # structured case summary (JSON)
    required_resource: Mapped[str] = mapped_column(String(30))       # primary resource
    required_resources: Mapped[str] = mapped_column(Text, default="")  # all resources, comma separated
    required_service: Mapped[str | None] = mapped_column(String(100), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    urgency: Mapped[str] = mapped_column(String(10), default="medium")  # low|medium|high|critical
    selected_hospital_id: Mapped[int | None] = mapped_column(ForeignKey("hospitals.id"), nullable=True, index=True)
    request_status: Mapped[str] = mapped_column(String(30), default="searching", index=True)
    reserved: Mapped[bool] = mapped_column(Boolean, default=False)  # are capacity units currently held?
    confirmation_code: Mapped[str] = mapped_column(String(12), default="", index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    transferred_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    admitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    discharged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    response_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    hospital: Mapped[Hospital | None] = relationship()

    @property
    def resource_list(self) -> list[str]:
        items = [r for r in self.required_resources.split(",") if r]
        return items or [self.required_resource]

    @property
    def hospital_name(self) -> str | None:
        return self.hospital.name if self.hospital else None


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    message: Mapped[str] = mapped_column(Text)
    request_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hospital_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(50))
    details: Mapped[str] = mapped_column(Text, default="")
    suspicious: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
