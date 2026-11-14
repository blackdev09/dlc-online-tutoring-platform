# DLC Online Tutoring Platform (prototype)

Software implementation for the CSC 476 group project, **Group 4**, Department of Computer Science,
University of Ibadan:
*Online Tutoring Platform for Distance Learning Students: A Proposed Digital Learning Solution for the
Distance Learning Centre (DLC), University of Ibadan.*

This is a design-stage prototype that demonstrates the core features proposed in the project report. It is not
connected to DLC's real portal, LMS or Single Sign-On.

## Features
| Feature | Where |
|---|---|
| Role-based sign-in (learner, tutor, administrator) | `/login` |
| Tutor availability slots (video, audio or text mode) | `/tutor` |
| One-click session booking and cancellation, with double-booking protection | `/learner` |
| Per-course question-and-answer forum | `/forum/<course_id>` |
| Learner progress dashboard (sessions, questions, answers) | `/learner` |
| Administrator engagement report: sessions, unanswered questions, average response time, inactive learners | `/admin` |
| Low-bandwidth view (text-first, minimal CSS) | button in the header |

## Run it
```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py                                         # http://127.0.0.1:5000
```
The first run creates `dlc.db` (SQLite) and seeds demo data. All demo accounts use the password `password123`:

| Role | Email |
|---|---|
| Learner | amaka@dlc.test, tunde@dlc.test, ngozi@dlc.test, ibrahim@dlc.test |
| Tutor | bello@dlc.test, adeyemi@dlc.test |
| Administrator | admin@dlc.test |

## Test it
```bash
python -m unittest tests.test_app -v
```
14 tests cover sign-in, role restrictions, CSRF rejection, booking, double-booking, enrolment checks, cancellation,
tutor permissions, forum replies, response-time calculation, inactive-learner detection and low-bandwidth mode.

## Design notes
- Flask + SQLite, no other dependencies. Passwords are hashed (Werkzeug); every POST requires a CSRF token.
- Booking is a single atomic `UPDATE ... WHERE status='open'`, so two learners cannot take the same slot.
- Demo passwords and the default `SECRET_KEY` are for local use only. Set `SECRET_KEY` before any real deployment.

## Known limits (prototype scope)
No real video/audio (slots record the mode only), no email/SMS or USSD gateway, no SSO or LMS integration,
no time-zone handling, and no pagination. These are covered as future work in the report.

## Group 4
Sunday Obayan (E048864), Lucky Olaniyan (E050846), James Akinnifesi (E050851),
Paul Oghenetega Francis (E050853, group leader), Temitayo Awosanmi (E050855).
Supervisor: Prof. Oladejo.
