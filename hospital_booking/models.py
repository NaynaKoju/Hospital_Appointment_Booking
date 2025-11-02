from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from flask_bcrypt import Bcrypt
from sqlalchemy import UniqueConstraint
from datetime import datetime

db = SQLAlchemy()
bcrypt = Bcrypt()

# ------------------ ADMIN ------------------ #
class Admin(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)


# ------------------ USER ------------------ #
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

    appointments = db.relationship("Appointment", back_populates="user", lazy=True)


# ------------------ DOCTOR ------------------ #
class Doctor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    specialization = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True)
    phone = db.Column(db.String(20), nullable=True)

    slots = db.relationship(
        "Slot",
        back_populates="doctor",
        lazy="joined",
        cascade="all, delete-orphan"
    )
    appointments = db.relationship(
        "Appointment",
        back_populates="doctor",
        lazy="joined",
        cascade="all, delete-orphan"
    )


# ------------------ SLOT ------------------ #
class Slot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctor.id"), nullable=False)

    date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)

    # Optional display fields (can remain None safely)
    day = db.Column(db.String(20))
    time = db.Column(db.String(20))

    doctor = db.relationship("Doctor", back_populates="slots")
    appointments = db.relationship(
        "Appointment",
        back_populates="slot",
        lazy=True,
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint('doctor_id', 'date', 'start_time', name='uq_doctor_slot'),
    )

    @property
    def is_booked(self):
        """Return True if the slot has any appointment, else False."""
        return bool(self.appointments)

    @property
    def formatted_time(self):
        """Return a safe, formatted time string."""
        if self.start_time and self.end_time:
            return f"{self.start_time.strftime('%I:%M %p')} - {self.end_time.strftime('%I:%M %p')}"
        return "Time Not Set"


# ------------------ APPOINTMENT ------------------ #
class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_name = db.Column(db.String(100), nullable=False)

    doctor_id = db.Column(db.Integer, db.ForeignKey("doctor.id"), nullable=False)
    slot_id = db.Column(db.Integer, db.ForeignKey("slot.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    turn_number = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default="Confirmed")
    updated_by_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)

    doctor = db.relationship("Doctor", back_populates="appointments")
    slot = db.relationship("Slot", back_populates="appointments")
    user = db.relationship("User", back_populates="appointments")

    __table_args__ = (
        UniqueConstraint('user_id', 'slot_id', name='uq_user_slot'),
    )
