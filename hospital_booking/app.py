# app.py
from flask import Flask, render_template, request, redirect, url_for, flash, abort
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, login_user, logout_user, login_required, UserMixin, current_user
from config import Config
from forms import SignupForm, LoginForm
from functools import wraps
from flask_migrate import Migrate
from datetime import datetime, timedelta
from sqlalchemy.exc import IntegrityError
from sqlalchemy import and_, or_

# Models import (uses models.py)
from models import db, bcrypt, Admin, User, Doctor, Slot, Appointment


# ---------------- App Initialization ----------------
app = Flask(__name__)
app.config.from_object(Config)

# initialize models' db with app
db.init_app(app)
migrate = Migrate(app, db)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = "user_login"

# ---------------- App routes ----------------
with app.app_context():
    db.create_all()
    # Create default admin if not present
    if not Admin.query.filter_by(username="admin").first():
        hashed = bcrypt.generate_password_hash("admin123").decode("utf-8")
        db.session.add(Admin(username="admin", password=hashed))
        db.session.commit()

# ---------------- Login Manager ----------------
@login_manager.user_loader
def load_user(user_id):
    """
    We encode admin ids as "admin-<id>" using get_id override (see below)
    """
    if isinstance(user_id, str) and user_id.startswith("admin-"):
        aid = int(user_id.split("-", 1)[1])
        return Admin.query.get(aid)
    return User.query.get(int(user_id))

# Provide get_id helpers so Flask-Login can distinguish admin vs user
Admin.get_id = lambda self: f"admin-{self.id}"
User.get_id = lambda self: str(self.id)

# ---------------- Helpers ----------------
def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not isinstance(current_user._get_current_object(), Admin):
            abort(403)
        return func(*args, **kwargs)
    return wrapper

def send_notification(appointment, action):
    doctor = appointment.doctor
    patient = appointment.user  # None if patient not linked
    admins = Admin.query.all()

    # Determine initiator (best-effort)
    if current_user.is_authenticated:
        initiator = getattr(current_user, "username", "Unknown")
        actor_type = "Admin" if isinstance(current_user._get_current_object(), Admin) else "Patient"
    else:
        initiator = patient.username if patient else "Unknown"
        actor_type = "Patient"

    msg = f"Appointment with Dr. {doctor.name} on {appointment.slot.day} at {appointment.slot.time} has been {action} by {actor_type} ({initiator})."
    print("Notification:", msg)
    if patient:
        print(f"Patient notified: {patient.username} -> {msg}")
    for admin in admins:
        print(f"Admin notified: {admin.username} -> {msg}")
    print(f"Doctor notified: {doctor.name} -> {msg}")

def mark_admin_update(appointment):
    appointment.updated_by_admin = True
    # if appointment.user_id is None but patient_name matches an existing user, link it
    if appointment.user_id is None:
        user = User.query.filter_by(username=appointment.patient_name).first()
        if user:
            appointment.user_id = user.id

# Public landing
@app.route("/")
def patient_dashboard():
    return render_template("patient_dashboard.html", hide_navbar=True)

@app.route("/access")
def login_or_signup():
    return render_template("login_signup.html")

# Signup / Login
@app.route("/signup", methods=["GET", "POST"])
def signup():
    form = SignupForm()
    if form.validate_on_submit():
        hashed = bcrypt.generate_password_hash(form.password.data).decode('utf-8')
        user = User(username=form.username.data, email=form.email.data, password=hashed)
        db.session.add(user)
        db.session.commit()
        flash("Account created successfully! Please log in.", "success")
        return redirect(url_for("user_login"))
    return render_template("signup.html", form=form)

@app.route("/login", methods=["GET", "POST"])
def user_login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user and bcrypt.check_password_hash(user.password, form.password.data):
            login_user(user)
            flash("Login successful!", "success")
            return redirect(url_for("user_dashboard"))
        flash("Invalid credentials", "danger")
    return render_template("userlogin.html", form=form)

# User dashboard (shows user's appointments)
@app.route("/dashboard")
@login_required
def user_dashboard():
    # fetch appointments for this user
    appointments = Appointment.query.filter_by(user_id=current_user.id).all()
    doctors = Doctor.query.all()

    # flash admin-driven updates once per appointment (these will appear once)
    # Note: flashing here is OK; make sure templates do not ALSO show the same flash block
    for appt in appointments:
        if appt.status == "Canceled" and appt.updated_by_admin:
            flash(f"Your appointment with Dr. {appt.doctor.name} has been canceled by Admin.", "info")
        elif appt.status == "Rescheduled" and appt.updated_by_admin:
            flash(f"Your appointment with Dr. {appt.doctor.name} has been rescheduled by Admin.", "info")

    return render_template("user_dashboard.html", appointments=appointments, doctors=doctors)

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You’ve been logged out.", "info")
    return redirect(url_for("patient_dashboard"))

# User: view doctors and book
#also checks double appointment booking at the same time slot/ partial time overlap
@app.route("/doctors")
@login_required
def view_doctors():
    doctors = Doctor.query.all()
    return render_template("doctors.html", doctors=doctors)

@app.route("/book/<int:doctor_id>", methods=["GET", "POST"])
@login_required
def book(doctor_id):
    doctor = Doctor.query.get_or_404(doctor_id)
    slots = Slot.query.filter_by(doctor_id=doctor.id).all()

    if request.method == "POST":
        slot_id = int(request.form["slot"])
        slot = Slot.query.get_or_404(slot_id)

        # 1️⃣ Same user booking same slot (same doctor)
        existing_by_user = Appointment.query.filter(
            and_(
                Appointment.user_id == current_user.id,
                Appointment.slot_id == slot_id,
                Appointment.status != "Canceled"
            )
        ).first()

        if existing_by_user:
            flash("You already have a booking for that slot.", "warning")
            return redirect(url_for("user_dashboard"))

        # 2️⃣ Check for overlapping time with other doctor slots
        overlapping = (
            db.session.query(Appointment)
            .join(Slot)
            .filter(
                and_(
                    Appointment.user_id == current_user.id,
                    Appointment.status != "Canceled",
                    Slot.date == slot.date,
                    or_(
                        and_(Slot.start_time < slot.end_time, Slot.end_time > slot.start_time),
                        and_(slot.start_time < Slot.end_time, slot.end_time > Slot.start_time)
                    )
                )
            )
            .first()
        )

        if overlapping:
            flash("You already have another appointment that overlaps with this time.", "danger")
            return redirect(url_for("user_dashboard"))

        # 3️⃣ Continue booking process
        existing_count = Appointment.query.filter_by(slot_id=slot_id).count()
        turn = existing_count + 1

        appt = Appointment(
            patient_name=current_user.username,
            doctor_id=doctor.id,
            slot_id=slot_id,
            turn_number=turn,
            user_id=current_user.id
        )

        db.session.add(appt)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Could not book — the slot was just taken. Please try another slot.", "danger")
            return redirect(url_for("view_doctors"))

        send_notification(appt, "booked")
        flash("Appointment booked successfully!", "success")
        return redirect(url_for("user_dashboard"))

    return render_template("book.html", doctor=doctor, slots=slots)
#lsit of all the doctors slots
@app.route("/admin/doctor_slots")
@login_required
@admin_required
def admin_doctor_slots():
    doctors = Doctor.query.all()

    doctor_slots = []
    for doctor in doctors:
        slots_info = []
        for slot in doctor.slots:
            slots_info.append({
                "id": slot.id,
                "date": slot.date,
                "start_time": slot.start_time,
                "end_time": slot.end_time,
                "day": slot.day or slot.date.strftime("%Y-%m-%d"),
                "time": slot.time or f"{slot.start_time.strftime('%I:%M %p')} - {slot.end_time.strftime('%I:%M %p')}",
                "is_booked": slot.is_booked
            })
        doctor_slots.append({
            "doctor": doctor,
            "slots": slots_info
        })

    return render_template("admin_doctor_slots.html", doctor_slots=doctor_slots)

#success route                
@app.route("/success/<int:appointment_id>")
@login_required
def booking_success(appointment_id):
    appt = Appointment.query.get_or_404(appointment_id)
    return render_template("success.html", turn=appt.turn_number, doctor=appt.doctor)

# Helpers for cancel/reschedule that handle admin vs user
def cancel_appointment_generic(appointment, actor="user"):
    if actor == "user" and appointment.user_id != current_user.id:
        abort(403)
    appointment.status = "Canceled"
    if actor == "admin":
        mark_admin_update(appointment)
    db.session.commit()
    send_notification(appointment, "canceled")

def reschedule_appointment_generic(appointment, new_slot_id, actor="user"):
    if actor == "user" and appointment.user_id != current_user.id:
        abort(403)

    # compute turn for new slot
    existing = Appointment.query.filter_by(slot_id=new_slot_id).count()
    appointment.slot_id = new_slot_id
    appointment.turn_number = existing + 1
    appointment.status = "Rescheduled"
    if actor == "admin":
        mark_admin_update(appointment)
    db.session.commit()
    send_notification(appointment, "rescheduled")

# User cancel/reschedule endpoints
@app.route("/appointment/cancel/<int:appt_id>", methods=["POST"])
@login_required
def cancel_appointment(appt_id):
    appt = Appointment.query.get_or_404(appt_id)
    # parse slot datetime safely; slot.day is YYYY-MM-DD, slot.time is "hh:mm AM/PM"
    try:
        slot_time = datetime.strptime(f"{appt.slot.day} {appt.slot.time}", "%Y-%m-%d %I:%M %p")
    except ValueError:
        # fallback: allow cancel if parsing fails (or change as you wish)
        slot_time = datetime.now() + timedelta(days=2)
    if slot_time - datetime.now() < timedelta(hours=24):
        flash("Cannot cancel within 24 hours of the appointment", "warning")
        return redirect(url_for("user_dashboard"))
    cancel_appointment_generic(appt, actor="user")
    flash("Appointment canceled successfully!", "success")
    return redirect(url_for("user_dashboard"))

@app.route("/appointment/reschedule/<int:appt_id>", methods=["GET", "POST"])
@login_required
def reschedule_appointment(appt_id):
    appt = Appointment.query.get_or_404(appt_id)
    slots = Slot.query.filter_by(doctor_id=appt.doctor_id).all()
    if request.method == "POST":
        new_slot_id = int(request.form["slot"])
        reschedule_appointment_generic(appt, new_slot_id, actor="user")
        flash("Appointment rescheduled successfully!", "success")
        return redirect(url_for("user_dashboard"))
    return render_template("reschedule.html", appointment=appt, slots=slots)

# ---------------- Admin Routes ----------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        user = Admin.query.filter_by(username=request.form["username"]).first()
        if user and bcrypt.check_password_hash(user.password, request.form["password"]):
            login_user(user)
            return redirect(url_for("admin_dashboard"))
        flash("Invalid login", "danger")
    return render_template("admin/login.html")

# @app.route("/admin/dashboard")
# @login_required
# @admin_required
# def admin_dashboard():
#     return render_template("admin/dashboard.html")

@app.route("/admin/dashboard")
@login_required
@admin_required
def admin_dashboard():
    doctors = Doctor.query.all()  # Fetch all doctors from DB
    return render_template("admin/dashboard.html", doctors=doctors)


@app.route("/admin/add_doctor", methods=["GET", "POST"])
@login_required
@admin_required
def add_doctor():
    if request.method == "POST":
        name = request.form.get("name")
        specialization = request.form.get("specialization")
        email = request.form.get("email")
        phone = request.form.get("phone")
        new_doctor = Doctor(name=name, specialization=specialization, email=email, phone=phone)
        db.session.add(new_doctor)
        db.session.commit()
        flash("Doctor added successfully!", "success")
        return redirect(url_for("view_doctors_admin"))
    return render_template("admin/add_doctor.html")

#route to add/store slots 
@app.route("/admin/add_slot", methods=["GET", "POST"])
@login_required
@admin_required
def add_slot():
    doctors = Doctor.query.all()

    if request.method == "POST":
        doctor_id = int(request.form["doctor"])
        date_str = request.form["date"]   # e.g. "2025-10-31"
        start_time_str = request.form["start_time"]  # e.g. "10:00"
        end_time_str = request.form["end_time"]      # e.g. "10:30"

        # Convert to Python date and time objects
        date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
        start_time_obj = datetime.strptime(start_time_str, "%H:%M").time()
        end_time_obj = datetime.strptime(end_time_str, "%H:%M").time()

        slot = Slot(
            doctor_id=doctor_id,
            date=date_obj,
            start_time=start_time_obj,
            end_time=end_time_obj,
            day=date_obj.strftime("%Y-%m-%d"),
            time=f"{start_time_obj.strftime('%I:%M %p')} - {end_time_obj.strftime('%I:%M %p')}"
        )

        db.session.add(slot)
        db.session.commit()

        flash("Slot added successfully!", "success")
        return redirect(url_for("admin_dashboard"))

    return render_template("admin/add_slot.html", doctors=doctors)

@app.route("/admin/view_bookings")
@login_required
@admin_required
def view_bookings():
    bookings = Appointment.query.all()
    data = []
    for b in bookings:
        data.append({
            "id": b.id,
            "patient": b.user.username if b.user else b.patient_name,
            "doctor": b.doctor.name if b.doctor else "Unknown",
            "specialization": b.doctor.specialization if b.doctor else "",
            "slot": b.slot,  # keep Slot object
            "turn": b.turn_number,
            "status": b.status
        })
    return render_template("admin/view_bookings.html", bookings=data)


@app.route("/admin/appointment/cancel/<int:appt_id>", methods=["POST"])
@login_required
@admin_required
def admin_cancel_appointment(appt_id):
    appt = Appointment.query.get_or_404(appt_id)
    cancel_appointment_generic(appt, actor="admin")
    flash("Appointment canceled by admin", "success")
    return redirect(url_for("view_bookings"))

@app.route("/admin/appointment/reschedule/<int:appt_id>", methods=["GET", "POST"])
@login_required
@admin_required
def admin_reschedule_appointment(appt_id):
    appt = Appointment.query.get_or_404(appt_id)
    slots = Slot.query.filter_by(doctor_id=appt.doctor_id).all()
    if request.method == "POST":
        new_slot_id = int(request.form["slot"])
        reschedule_appointment_generic(appt, new_slot_id, actor="admin")
        flash("Appointment rescheduled by admin", "success")
        return redirect(url_for("view_bookings"))
    return render_template("admin/reschedule_appointment.html", appointment=appt, slots=slots)

# Admin view to show all slots
@app.route("/admin/slots")
@login_required  # optional if admin login is required
def admin_slots():
    doctors = Doctor.query.all()  # get all doctors
    return render_template("admin_slots.html", doctors=doctors)

# Admin doctor management
@app.route("/admin/view_doctors")
@login_required
@admin_required
def view_doctors_admin():
    doctors = Doctor.query.options(db.joinedload(Doctor.slots)).all()
    return render_template("admin/view_doctors.html", doctors=doctors)

@app.route("/admin/edit_doctor/<int:doctor_id>", methods=["GET", "POST"])
@login_required
@admin_required
def edit_doctor(doctor_id):
    doctor = Doctor.query.get_or_404(doctor_id)
    if request.method == "POST":
        doctor.name = request.form.get("name")
        doctor.specialization = request.form.get("specialization")
        doctor.email = request.form.get("email")
        doctor.phone = request.form.get("phone")
        db.session.commit()
        flash("Doctor details updated successfully!", "success")
        return redirect(url_for("view_doctors_admin"))
    return render_template("admin/edit_doctor.html", doctor=doctor)

@app.route("/admin/delete_doctor/<int:doctor_id>", methods=["POST"])
@login_required
@admin_required
def delete_doctor(doctor_id):
    doctor = Doctor.query.get_or_404(doctor_id)
    db.session.delete(doctor)
    db.session.commit()
    flash("Doctor removed successfully!", "success")
    return redirect(url_for("view_doctors_admin"))

@app.route("/admin/delete_booking/<int:booking_id>", methods=["POST"])
@login_required
@admin_required
def delete_booking(booking_id):
    booking = Appointment.query.get_or_404(booking_id)
    db.session.delete(booking)
    db.session.commit()
    flash("Booking deleted successfully!", "success")
    return redirect(url_for("view_bookings"))

@app.route("/admin/logout")
@login_required
@admin_required
def admin_logout():
    logout_user()
    return redirect(url_for("admin_login"))

# Error handler
@app.errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403

if __name__ == "__main__":
    app.run(debug=True)
