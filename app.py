"""DLC Online Tutoring Platform - prototype (CSC 476, Group 4).

A small Flask + SQLite application demonstrating the core features proposed in the
project report: tutor availability and session booking, per-course Q&A, a learner
progress dashboard, an administrator engagement report, and a low-bandwidth (lite) mode.
"""
import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

DB_PATH = os.environ.get("DLC_DB", os.path.join(os.path.dirname(__file__), "dlc.db"))
AT_RISK_DAYS = 14

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
  pw_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('learner','tutor','admin')));
CREATE TABLE IF NOT EXISTS courses(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, title TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS enrollments(
  learner_id INTEGER NOT NULL, course_id INTEGER NOT NULL, PRIMARY KEY(learner_id, course_id));
CREATE TABLE IF NOT EXISTS tutor_courses(
  tutor_id INTEGER NOT NULL, course_id INTEGER NOT NULL, PRIMARY KEY(tutor_id, course_id));
CREATE TABLE IF NOT EXISTS slots(
  id INTEGER PRIMARY KEY, tutor_id INTEGER NOT NULL, course_id INTEGER NOT NULL,
  start_at TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'video',
  booked_by INTEGER, booked_at TEXT, status TEXT NOT NULL DEFAULT 'open');
CREATE TABLE IF NOT EXISTS questions(
  id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL, learner_id INTEGER NOT NULL,
  body TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS answers(
  id INTEGER PRIMARY KEY, question_id INTEGER NOT NULL, tutor_id INTEGER NOT NULL,
  body TEXT NOT NULL, created_at TEXT NOT NULL);
"""

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")


# ---------------------------------------------------------------- database
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- security
@app.before_request
def csrf_protect():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    if request.method == "POST":
        if request.form.get("csrf") != session.get("csrf"):
            abort(400, "Missing or invalid CSRF token")


@app.context_processor
def inject_globals():
    user = None
    if session.get("uid"):
        user = db().execute("SELECT * FROM users WHERE id=?", (session["uid"],)).fetchone()
    return {"csrf": session.get("csrf", ""), "user": user, "lite": session.get("lite", False)}


def login_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not session.get("uid"):
                return redirect(url_for("login"))
            if roles and session.get("role") not in roles:
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


# ---------------------------------------------------------------- auth
@app.route("/")
def index():
    role = session.get("role")
    if not role:
        return redirect(url_for("login"))
    return redirect(url_for({"learner": "learner_home", "tutor": "tutor_home",
                             "admin": "admin_home"}[role]))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        row = db().execute("SELECT * FROM users WHERE email=?",
                           (request.form.get("email", "").strip().lower(),)).fetchone()
        if row and check_password_hash(row["pw_hash"], request.form.get("password", "")):
            session.update(uid=row["id"], role=row["role"])
            return redirect(url_for("index"))
        flash("Incorrect email or password.")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    csrf = session.get("csrf")
    session.clear()
    session["csrf"] = csrf
    return redirect(url_for("login"))


@app.route("/lite/<state>", methods=["POST"])
def lite_mode(state):
    session["lite"] = state == "on"
    return redirect(request.referrer or url_for("index"))


# ---------------------------------------------------------------- learner
def learner_stats(learner_id):
    d = db()
    sessions_done = d.execute("SELECT COUNT(*) FROM slots WHERE booked_by=? AND status='done'",
                              (learner_id,)).fetchone()[0]
    sessions_up = d.execute("SELECT COUNT(*) FROM slots WHERE booked_by=? AND status='booked'",
                            (learner_id,)).fetchone()[0]
    asked = d.execute("SELECT COUNT(*) FROM questions WHERE learner_id=?", (learner_id,)).fetchone()[0]
    answered = d.execute("""SELECT COUNT(DISTINCT q.id) FROM questions q
                            JOIN answers a ON a.question_id=q.id WHERE q.learner_id=?""",
                         (learner_id,)).fetchone()[0]
    return dict(done=sessions_done, upcoming=sessions_up, asked=asked, answered=answered)


@app.route("/learner")
@login_required("learner")
def learner_home():
    d, uid = db(), session["uid"]
    course = request.args.get("course", type=int)
    sql = """SELECT s.*, c.code, c.title, u.name AS tutor FROM slots s
             JOIN courses c ON c.id=s.course_id JOIN users u ON u.id=s.tutor_id
             WHERE s.status='open' AND s.course_id IN
                   (SELECT course_id FROM enrollments WHERE learner_id=?)"""
    args = [uid]
    if course:
        sql += " AND s.course_id=?"
        args.append(course)
    open_slots = d.execute(sql + " ORDER BY s.start_at", args).fetchall()
    mine = d.execute("""SELECT s.*, c.code, u.name AS tutor FROM slots s
                        JOIN courses c ON c.id=s.course_id JOIN users u ON u.id=s.tutor_id
                        WHERE s.booked_by=? ORDER BY s.start_at""", (uid,)).fetchall()
    courses = d.execute("""SELECT c.* FROM courses c JOIN enrollments e ON e.course_id=c.id
                           WHERE e.learner_id=?""", (uid,)).fetchall()
    return render_template("learner.html", open_slots=open_slots, mine=mine,
                           courses=courses, stats=learner_stats(uid), selected=course)


@app.route("/book/<int:slot_id>", methods=["POST"])
@login_required("learner")
def book(slot_id):
    d = db()
    # single atomic UPDATE: prevents two learners booking the same slot
    cur = d.execute("""UPDATE slots SET booked_by=?, booked_at=?, status='booked'
                       WHERE id=? AND status='open' AND course_id IN
                       (SELECT course_id FROM enrollments WHERE learner_id=?)""",
                    (session["uid"], now(), slot_id, session["uid"]))
    d.commit()
    flash("Session booked." if cur.rowcount else "That slot is no longer available.")
    return redirect(url_for("learner_home"))


@app.route("/cancel/<int:slot_id>", methods=["POST"])
@login_required("learner")
def cancel(slot_id):
    d = db()
    cur = d.execute("""UPDATE slots SET booked_by=NULL, booked_at=NULL, status='open'
                       WHERE id=? AND booked_by=? AND status='booked'""",
                    (slot_id, session["uid"]))
    d.commit()
    flash("Booking cancelled." if cur.rowcount else "Nothing to cancel.")
    return redirect(url_for("learner_home"))


# ---------------------------------------------------------------- tutor
@app.route("/tutor")
@login_required("tutor")
def tutor_home():
    d, uid = db(), session["uid"]
    slots = d.execute("""SELECT s.*, c.code, l.name AS learner FROM slots s
                         JOIN courses c ON c.id=s.course_id LEFT JOIN users l ON l.id=s.booked_by
                         WHERE s.tutor_id=? ORDER BY s.start_at""", (uid,)).fetchall()
    courses = d.execute("""SELECT c.* FROM courses c JOIN tutor_courses t ON t.course_id=c.id
                           WHERE t.tutor_id=?""", (uid,)).fetchall()
    waiting = d.execute("""SELECT q.*, c.code, u.name AS learner FROM questions q
                           JOIN courses c ON c.id=q.course_id JOIN users u ON u.id=q.learner_id
                           WHERE q.course_id IN (SELECT course_id FROM tutor_courses WHERE tutor_id=?)
                           AND NOT EXISTS (SELECT 1 FROM answers a WHERE a.question_id=q.id)
                           ORDER BY q.created_at""", (uid,)).fetchall()
    return render_template("tutor.html", slots=slots, courses=courses, waiting=waiting)


@app.route("/tutor/slots", methods=["POST"])
@login_required("tutor")
def add_slot():
    d = db()
    course_id = request.form.get("course_id", type=int)
    start = request.form.get("start_at", "").strip().replace("T", " ")
    mode = request.form.get("mode", "video")
    try:
        datetime.strptime(start, "%Y-%m-%d %H:%M")
    except ValueError:
        flash("Enter the date and time in a valid format.")
        return redirect(url_for("tutor_home"))
    if mode not in ("video", "audio", "text"):
        abort(400)
    owns = d.execute("SELECT 1 FROM tutor_courses WHERE tutor_id=? AND course_id=?",
                     (session["uid"], course_id)).fetchone()
    if not owns:
        abort(403)
    d.execute("INSERT INTO slots(tutor_id,course_id,start_at,mode) VALUES(?,?,?,?)",
              (session["uid"], course_id, start, mode))
    d.commit()
    flash("Availability added.")
    return redirect(url_for("tutor_home"))


@app.route("/tutor/complete/<int:slot_id>", methods=["POST"])
@login_required("tutor")
def complete(slot_id):
    d = db()
    d.execute("UPDATE slots SET status='done' WHERE id=? AND tutor_id=? AND status='booked'",
              (slot_id, session["uid"]))
    d.commit()
    return redirect(url_for("tutor_home"))


# ---------------------------------------------------------------- forum
@app.route("/forum/<int:course_id>", methods=["GET", "POST"])
@login_required("learner", "tutor", "admin")
def forum(course_id):
    d, uid, role = db(), session["uid"], session["role"]
    course = d.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone() or abort(404)
    member = (role == "admin"
              or (role == "learner" and d.execute("SELECT 1 FROM enrollments WHERE learner_id=? AND course_id=?", (uid, course_id)).fetchone())
              or (role == "tutor" and d.execute("SELECT 1 FROM tutor_courses WHERE tutor_id=? AND course_id=?", (uid, course_id)).fetchone()))
    if not member:
        abort(403)
    if request.method == "POST":
        body = request.form.get("body", "").strip()
        if role != "learner":
            abort(403)
        if body:
            d.execute("INSERT INTO questions(course_id,learner_id,body,created_at) VALUES(?,?,?,?)",
                      (course_id, uid, body[:1000], now()))
            d.commit()
        return redirect(url_for("forum", course_id=course_id))
    qs = d.execute("""SELECT q.*, u.name AS learner FROM questions q JOIN users u ON u.id=q.learner_id
                      WHERE q.course_id=? ORDER BY q.created_at DESC""", (course_id,)).fetchall()
    answers = {q["id"]: d.execute("""SELECT a.*, u.name AS tutor FROM answers a JOIN users u ON u.id=a.tutor_id
                                     WHERE a.question_id=? ORDER BY a.created_at""", (q["id"],)).fetchall()
               for q in qs}
    return render_template("forum.html", course=course, questions=qs, answers=answers)


@app.route("/answer/<int:qid>", methods=["POST"])
@login_required("tutor")
def answer(qid):
    d = db()
    q = d.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone() or abort(404)
    if not d.execute("SELECT 1 FROM tutor_courses WHERE tutor_id=? AND course_id=?",
                     (session["uid"], q["course_id"])).fetchone():
        abort(403)
    body = request.form.get("body", "").strip()
    if body:
        d.execute("INSERT INTO answers(question_id,tutor_id,body,created_at) VALUES(?,?,?,?)",
                  (qid, session["uid"], body[:2000], now()))
        d.commit()
    return redirect(url_for("forum", course_id=q["course_id"]))


# ---------------------------------------------------------------- admin
def avg_response_minutes(d):
    row = d.execute("""SELECT AVG((julianday(first_a) - julianday(created_at)) * 24 * 60) FROM
                       (SELECT q.created_at, MIN(a.created_at) AS first_a FROM questions q
                        JOIN answers a ON a.question_id=q.id GROUP BY q.id)""").fetchone()[0]
    return None if row is None else round(row, 1)


def at_risk_learners(d, today=None):
    cutoff = ((today or datetime.now()) - timedelta(days=AT_RISK_DAYS)).strftime("%Y-%m-%d %H:%M")
    return d.execute("""SELECT u.id, u.name, MAX(act) AS last_active FROM users u
        LEFT JOIN (SELECT learner_id AS uid, created_at AS act FROM questions
                   UNION ALL SELECT booked_by, booked_at FROM slots WHERE booked_by IS NOT NULL) x
             ON x.uid=u.id
        WHERE u.role='learner' GROUP BY u.id
        HAVING last_active IS NULL OR last_active < ? ORDER BY last_active""", (cutoff,)).fetchall()


@app.route("/admin")
@login_required("admin")
def admin_home():
    d = db()
    totals = {
        "learners": d.execute("SELECT COUNT(*) FROM users WHERE role='learner'").fetchone()[0],
        "tutors": d.execute("SELECT COUNT(*) FROM users WHERE role='tutor'").fetchone()[0],
        "booked": d.execute("SELECT COUNT(*) FROM slots WHERE status IN ('booked','done')").fetchone()[0],
        "open": d.execute("SELECT COUNT(*) FROM slots WHERE status='open'").fetchone()[0],
        "questions": d.execute("SELECT COUNT(*) FROM questions").fetchone()[0],
        "unanswered": d.execute("""SELECT COUNT(*) FROM questions q WHERE NOT EXISTS
                                   (SELECT 1 FROM answers a WHERE a.question_id=q.id)""").fetchone()[0],
        "avg_response": avg_response_minutes(d),
    }
    per_course = d.execute("""SELECT c.code, c.title,
        (SELECT COUNT(*) FROM slots s WHERE s.course_id=c.id AND s.status IN ('booked','done')) AS sessions,
        (SELECT COUNT(*) FROM questions q WHERE q.course_id=c.id) AS questions
        FROM courses c ORDER BY c.code""").fetchall()
    return render_template("admin.html", totals=totals, per_course=per_course,
                           at_risk=at_risk_learners(d), days=AT_RISK_DAYS)


# ---------------------------------------------------------------- seed data
def seed():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
        conn.close()
        return
    pw = generate_password_hash("password123")
    users = [("Admin DLC", "admin@dlc.test", "admin"),
             ("Dr. Bello", "bello@dlc.test", "tutor"), ("Mrs. Adeyemi", "adeyemi@dlc.test", "tutor"),
             ("Amaka Eze", "amaka@dlc.test", "learner"), ("Tunde Salami", "tunde@dlc.test", "learner"),
             ("Ngozi Okoro", "ngozi@dlc.test", "learner"), ("Ibrahim Musa", "ibrahim@dlc.test", "learner")]
    for n, e, r in users:
        conn.execute("INSERT INTO users(name,email,pw_hash,role) VALUES(?,?,?,?)", (n, e, pw, r))
    for code, title in [("CSC 302", "Systems Analysis"), ("CSC 476", "Research Methods in Computer Science"),
                        ("STA 231", "Statistical Computing")]:
        conn.execute("INSERT INTO courses(code,title) VALUES(?,?)", (code, title))
    conn.executemany("INSERT INTO tutor_courses VALUES(?,?)", [(2, 1), (2, 2), (3, 3), (3, 1)])
    conn.executemany("INSERT INTO enrollments VALUES(?,?)",
                     [(4, 1), (4, 2), (5, 1), (5, 3), (6, 2), (6, 3), (7, 1)])
    base = datetime.now() + timedelta(days=2)
    for i, (t, c) in enumerate([(2, 1), (2, 2), (3, 3), (3, 1), (2, 1)]):
        conn.execute("INSERT INTO slots(tutor_id,course_id,start_at,mode) VALUES(?,?,?,?)",
                     (t, c, (base + timedelta(days=i, hours=i)).strftime("%Y-%m-%d %H:00"),
                      ["video", "audio", "text"][i % 3]))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    seed()
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=int(os.environ.get("PORT", 5000)))
