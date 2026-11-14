import os
import re
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta

_tmp = tempfile.mkdtemp()
os.environ["DLC_DB"] = os.path.join(_tmp, "test.db")
import app as dlc  # noqa: E402


def token(client):
    client.get("/login")
    with client.session_transaction() as s:
        return s["csrf"]


def login(client, email, pw="password123"):
    return client.post("/login", data={"email": email, "password": pw, "csrf": token(client)})


class PlatformTests(unittest.TestCase):
    def setUp(self):
        if os.path.exists(dlc.DB_PATH):
            os.remove(dlc.DB_PATH)
        dlc.seed()
        dlc.app.config["TESTING"] = True
        self.c = dlc.app.test_client()

    def post(self, c, url, **data):
        data["csrf"] = token(c)
        return c.post(url, data=data, follow_redirects=True)

    def test_01_login_success_and_role_redirect(self):
        r = login(self.c, "amaka@dlc.test")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/", r.headers["Location"])
        self.assertIn(b"My learning", self.c.get("/learner").data)

    def test_02_login_rejects_wrong_password(self):
        r = login(self.c, "amaka@dlc.test", "wrong")
        self.assertIn(b"Incorrect email or password", r.data)

    def test_03_learner_cannot_open_tutor_or_admin_pages(self):
        login(self.c, "amaka@dlc.test")
        self.assertEqual(self.c.get("/tutor").status_code, 403)
        self.assertEqual(self.c.get("/admin").status_code, 403)

    def test_04_post_without_csrf_is_rejected(self):
        login(self.c, "amaka@dlc.test")
        self.assertEqual(self.c.post("/book/1").status_code, 400)

    def test_05_learner_books_slot_and_it_leaves_open_list(self):
        login(self.c, "amaka@dlc.test")
        r = self.post(self.c, "/book/1")
        self.assertIn(b"Session booked", r.data)
        row = sqlite3.connect(dlc.DB_PATH).execute("SELECT status,booked_by FROM slots WHERE id=1").fetchone()
        self.assertEqual(row, ("booked", 4))

    def test_06_double_booking_is_blocked(self):
        login(self.c, "amaka@dlc.test")
        self.post(self.c, "/book/1")
        other = dlc.app.test_client()
        login(other, "tunde@dlc.test")
        r = self.post(other, "/book/1")
        self.assertIn(b"no longer available", r.data)

    def test_07_learner_cannot_book_unenrolled_course(self):
        login(self.c, "ibrahim@dlc.test")   # enrolled only in CSC 302; slot 2 is CSC 476
        r = self.post(self.c, "/book/2")
        self.assertIn(b"no longer available", r.data)

    def test_08_cancel_reopens_slot(self):
        login(self.c, "amaka@dlc.test")
        self.post(self.c, "/book/1")
        self.post(self.c, "/cancel/1")
        row = sqlite3.connect(dlc.DB_PATH).execute("SELECT status,booked_by FROM slots WHERE id=1").fetchone()
        self.assertEqual(row, ("open", None))

    def test_09_tutor_adds_slot_for_own_course_only(self):
        login(self.c, "bello@dlc.test")           # teaches courses 1 and 2
        ok = self.post(self.c, "/tutor/slots", course_id=2, start_at="2026-12-01T10:00", mode="video")
        self.assertIn(b"Availability added", ok.data)
        c = token(self.c)
        bad = self.c.post("/tutor/slots", data={"course_id": 3, "start_at": "2026-12-01T10:00",
                                                "mode": "video", "csrf": c})
        self.assertEqual(bad.status_code, 403)

    def test_10_tutor_slot_rejects_bad_date(self):
        login(self.c, "bello@dlc.test")
        r = self.post(self.c, "/tutor/slots", course_id=1, start_at="not-a-date", mode="video")
        self.assertIn(b"valid format", r.data)

    def test_11_question_answer_and_response_time(self):
        login(self.c, "amaka@dlc.test")
        self.post(self.c, "/forum/1", body="What is the difference between SDLC and a methodology?")
        conn = sqlite3.connect(dlc.DB_PATH)
        earlier = (datetime.now() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M")
        conn.execute("UPDATE questions SET created_at=?", (earlier,))
        conn.commit(); conn.close()
        tutor = dlc.app.test_client()
        login(tutor, "bello@dlc.test")
        self.post(tutor, "/answer/1", body="A methodology is an approach; the SDLC is the life cycle.")
        with dlc.app.app_context():
            self.assertTrue(28 <= dlc.avg_response_minutes(dlc.db()) <= 32)

    def test_12_tutor_cannot_answer_other_course(self):
        login(self.c, "tunde@dlc.test")
        self.post(self.c, "/forum/3", body="Question for STA 231")
        tutor = dlc.app.test_client()
        login(tutor, "bello@dlc.test")           # Dr. Bello does not teach course 3
        c = token(tutor)
        self.assertEqual(tutor.post("/answer/1", data={"body": "x", "csrf": c}).status_code, 403)

    def test_13_at_risk_flags_inactive_learners(self):
        with dlc.app.app_context():
            names = [r["name"] for r in dlc.at_risk_learners(dlc.db())]
        self.assertEqual(len(names), 4)          # nobody has acted yet
        login(self.c, "amaka@dlc.test"); self.post(self.c, "/book/1")
        with dlc.app.app_context():
            names = [r["name"] for r in dlc.at_risk_learners(dlc.db())]
        self.assertNotIn("Amaka Eze", names)

    def test_14_admin_report_loads_and_lite_mode_switches_css(self):
        login(self.c, "admin@dlc.test")
        self.assertIn(b"Engagement report", self.c.get("/admin").data)
        self.post(self.c, "/lite/on")
        self.assertIn(b"lite.css", self.c.get("/admin").data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
