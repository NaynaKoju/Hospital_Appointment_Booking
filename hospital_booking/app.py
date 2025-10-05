from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, login_user, logout_user, login_required, UserMixin
from config import Config

# ---------------- App Initialization ----------------
app = Flask(__name__)
app.config.from_object(Config)

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

login_manager = LoginManager(app)
login_manager.login_view = "admin_login"

# ---------------- Models ----------------
class Admin(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

class Doctor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    specialization = db.Column(db.String(100), nullable=False)

class Slot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctor.id"), nullable=False)
    day = db.Column(db.String(20), nullable=False)
    time = db.Column(db.String(20), nullable=False)

    doctor = db.relationship("Doctor", backref="slots")

class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_name = db.Column(db.String(100), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctor.id"), nullable=False)
    slot_id = db.Column(db.Integer, db.ForeignKey("slot.id"), nullable=False)
    turn_number = db.Column(db.Integer, nullable=False)

    doctor = db.relationship("Doctor", backref="appointments")
    slot = db.relationship("Slot", backref="appointments")

# ---------------- Login Manager ----------------
@login_manager.user_loader
def load_user(admin_id):
    return Admin.query.get(int(admin_id))

# ---------------- Initialize Database ----------------
with app.app_context():
    db.create_all()
    if not Admin.query.filter_by(username="admin").first():
        hashed = bcrypt.generate_password_hash("admin123").decode("utf-8")
        db.session.add(Admin(username="admin", password=hashed))
        db.session.commit()

# ---------------- Routes ----------------

# Home Page
@app.route("/")
def index():
    doctors = Doctor.query.all()
    return render_template("index.html", doctors=doctors)

# Patient Booking
@app.route("/book/<int:doctor_id>", methods=["GET", "POST"])
def book(doctor_id):
    doctor = Doctor.query.get_or_404(doctor_id)
    slots = Slot.query.filter_by(doctor_id=doctor.id).all()

    if request.method == "POST":
        name = request.form["name"]
        slot_id = int(request.form["slot"])

        existing = Appointment.query.filter_by(slot_id=slot_id).count()
        turn = existing + 1

        appt = Appointment(patient_name=name, doctor_id=doctor.id, slot_id=slot_id, turn_number=turn)
        db.session.add(appt)
        db.session.commit()
        return render_template("success.html", turn=turn, doctor=doctor)

    return render_template("book.html", doctor=doctor, slots=slots)

# ---------------- Admin Routes ----------------

# Admin Login
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        user = Admin.query.filter_by(username=request.form["username"]).first()
        if user and bcrypt.check_password_hash(user.password, request.form["password"]):
            login_user(user)
            return redirect(url_for("admin_dashboard"))
        flash("Invalid login")
    return render_template("admin/login.html")

# Admin Dashboard
@app.route("/admin/dashboard")
@login_required
def admin_dashboard():
    return render_template("admin/dashboard.html")

# Add Doctor
@app.route("/admin/add_doctor", methods=["GET", "POST"])
@login_required
def add_doctor():
    if request.method == "POST":
        doc = Doctor(name=request.form["name"], specialization=request.form["specialization"])
        db.session.add(doc)
        db.session.commit()
        flash("Doctor added successfully!", "success")
        return redirect(url_for("admin_dashboard"))
    return render_template("admin/add_doctor.html")

# Add Slot
@app.route("/admin/add_slot", methods=["GET", "POST"])
@login_required
def add_slot():
    doctors = Doctor.query.all()
    if request.method == "POST":
        slot = Slot(
            doctor_id=int(request.form["doctor"]),
            day=request.form["day"],
            time=request.form["time"]
        )
        db.session.add(slot)
        db.session.commit()
        flash("Slot added successfully!", "success")
        return redirect(url_for("admin_dashboard"))
    return render_template("admin/add_slot.html", doctors=doctors)

# View Bookings
@app.route("/admin/view_bookings")
@login_required
def view_bookings():
    bookings = Appointment.query.all()
    data = []
    for b in bookings:
        doctor = Doctor.query.get(b.doctor_id)
        slot = Slot.query.get(b.slot_id)
        data.append({
            "id": b.id,
            "patient": b.patient_name,
            "doctor": doctor.name if doctor else "Unknown",
            "specialization": doctor.specialization if doctor else "",
            "slot": f"{slot.day} - {slot.time}" if slot else "Unknown",
            "turn": b.turn_number
        })
    return render_template("admin/view_bookings.html", bookings=data)

# Delete Booking
@app.route("/admin/delete_booking/<int:booking_id>", methods=["POST"])
@login_required
def delete_booking(booking_id):
    booking = Appointment.query.get_or_404(booking_id)
    db.session.delete(booking)
    db.session.commit()
    flash("Booking deleted successfully!", "success")
    return redirect(url_for("view_bookings"))

# Admin Logout
@app.route("/admin/logout")
@login_required
def admin_logout():
    logout_user()
    return redirect(url_for("admin_login"))

# ---------------- Run App ----------------
if __name__ == "__main__":
    app.run(debug=True)
