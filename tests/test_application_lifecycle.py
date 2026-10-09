"""Focused regression coverage for the review and applicant notification workflow."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from unittest.mock import patch

from fastapi import HTTPException

from app import database
from app import main
from app.schemas import ApplicationInput, DecisionInput
from app.security import hash_password


APPLICATION = {
    "loan_amount": 12000,
    "term_months": 36,
    "annual_income": 72000,
    "home_ownership": "RENT",
    "employment_length": "2 years",
    "purpose": "debt_consolidation",
    "dti": 18.5,
    "prior_delinquencies": 0,
    "fico_score": 710,
    "recent_credit_inquiries": 1,
    "open_accounts": 9,
    "public_records": 0,
    "revolving_balance": 8500,
    "revolving_utilization": 28,
    "total_accounts": 20,
}


class ApplicationLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="crediguard-test-")
        data_dir = Path(self.tempdir.name)
        self.previous_paths = (database.DATA_DIR, database.DATABASE_PATH, database.MODEL_DIR)
        database.DATA_DIR = data_dir
        database.DATABASE_PATH = data_dir / "test.sqlite3"
        database.MODEL_DIR = data_dir / "models"
        main.app.state.session_secret = b"test-session-secret-that-is-long-enough"
        database.init_database()
        self.admin = self._add_user("admin", "ADMIN")
        self.applicant = self._add_user("applicant", "APPLICANT")
        self.other_applicant = self._add_user("other-applicant", "APPLICANT")

    def tearDown(self) -> None:
        database.DATA_DIR, database.DATABASE_PATH, database.MODEL_DIR = self.previous_paths
        self.tempdir.cleanup()

    def _add_user(self, user_id: str, role: str) -> dict[str, object]:
        full_name = user_id.replace("-", " ").title()
        email = f"{user_id}@example.test"
        with database.connect() as db:
            db.execute(
                "INSERT INTO users(id,full_name,email,password_hash,role,active,created_at) VALUES(?,?,?,?,?,?,?)",
                (user_id, full_name, email, hash_password("unit-test-password-long"), role, 1, database.utc_now()),
            )
            row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return database.user_public(row)

    def _application(self, status: str = "AI_ASSESSED", with_prediction: bool = False) -> str:
        application_id = str(uuid.uuid4())
        now = database.utc_now()
        prediction_id = str(uuid.uuid4()) if with_prediction else None
        with database.connect() as db:
            db.execute(
                """INSERT INTO applications(id,applicant_id,status,applicant_name,application_data,latest_prediction_id,created_at,updated_at,status_updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (application_id, self.applicant["id"], status, self.applicant["full_name"], json.dumps(APPLICATION), prediction_id, now, now, now),
            )
            if prediction_id:
                db.execute(
                    """INSERT INTO predictions(id,application_id,model_version,probability_of_default,risk_score,risk_category,thresholds,factors,range_warnings,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (prediction_id, application_id, "test-v1", 0.2, 20.0, "LOW", json.dumps({"low_risk_maximum": 0.3, "medium_risk_maximum": 0.6}), "[]", "[]", now),
                )
        return application_id

    def _assert_counts(self, application_id: str, *, decisions: int, notifications: int) -> None:
        with database.connect() as db:
            decision_count = db.execute(
                "SELECT COUNT(*) AS n FROM audit_events WHERE resource_type='application' AND resource_id=? AND action='ANALYST_DECISION_RECORDED'",
                (application_id,),
            ).fetchone()["n"]
            notification_count = db.execute(
                "SELECT COUNT(*) AS n FROM notifications WHERE application_id=?",
                (application_id,),
            ).fetchone()["n"]
        self.assertEqual(decision_count, decisions)
        self.assertEqual(notification_count, notifications)

    def _http_request(self, method: str, path: str, body: dict[str, object] | None = None, cookie: str | None = None):
        body_bytes = json.dumps(body).encode("utf-8") if body is not None else b""
        parsed = urlsplit(path)
        headers = [(b"host", b"testserver")]
        if body is not None:
            headers.append((b"content-type", b"application/json"))
        if cookie:
            headers.append((b"cookie", cookie.encode("ascii")))

        async def send_request():
            request_sent = False
            response_finished = asyncio.Event()
            messages = []

            async def receive():
                nonlocal request_sent
                if not request_sent:
                    request_sent = True
                    return {"type": "http.request", "body": body_bytes, "more_body": False}
                await response_finished.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                messages.append(message)
                if message["type"] == "http.response.body" and not message.get("more_body", False):
                    response_finished.set()

            scope = {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": method,
                "scheme": "http",
                "path": parsed.path,
                "raw_path": parsed.path.encode("ascii"),
                "query_string": parsed.query.encode("ascii"),
                "root_path": "",
                "headers": headers,
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 49152),
                "state": {},
            }
            await main.app(scope, receive, send)
            start = next(message for message in messages if message["type"] == "http.response.start")
            response_body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
            response_headers = {key.decode("latin1").lower(): value.decode("latin1") for key, value in start["headers"]}
            response_data = json.loads(response_body) if response_headers.get("content-type", "").startswith("application/json") else response_body.decode("utf-8")
            return start["status"], response_headers, response_data

        return asyncio.run(send_request())

    def test_approval_persists_reviewer_audit_and_private_note(self) -> None:
        model_result = {"probability_of_default": 0.25, "model_version": "test-v1", "factors": [], "range_warnings": []}
        with patch.object(main.model_service, "predict", return_value=model_result):
            submitted = main.create_application(ApplicationInput(**APPLICATION), self.applicant)
        application_id = submitted["id"]
        self.assertEqual(submitted["status"], "AI_ASSESSED")
        reviewed = main.start_review(application_id, self.admin)
        self.assertEqual(reviewed["status"], "UNDER_REVIEW")

        result = main.record_decision(application_id, DecisionInput(decision="APPROVED", notes="Internal verification note"), self.admin)
        self.assertEqual(result["status"], "APPROVED")
        self.assertEqual(result["decision"], "APPROVED")
        self.assertEqual(result["analyst_id"], self.admin["id"])
        self.assertIsNotNone(result["decision_at"])
        self.assertEqual(result["analyst_notes"], "Internal verification note")

        applicant_view = main.get_application(application_id, self.applicant)
        self.assertEqual(applicant_view["analyst_notes"], "")
        self.assertIn("authorized member of the credit team", applicant_view["applicant_status"]["summary"])
        self.assertIn("does not create a binding loan offer", applicant_view["applicant_status"]["next_action"])
        self.assertTrue(all("notes" not in event["details"] for event in applicant_view["timeline"]))
        self._assert_counts(application_id, decisions=1, notifications=1)

        inbox = main.list_notifications(self.applicant, page=1, page_size=50)
        self.assertEqual(inbox["unread_count"], 1)
        self.assertIn("approval decision", inbox["items"][0]["message"])
        self.assertNotIn("Internal verification note", inbox["items"][0]["message"])

    def test_rejection_is_visible_without_leaking_staff_note(self) -> None:
        application_id = self._application("UNDER_REVIEW")
        main.record_decision(application_id, DecisionInput(decision="REJECTED", notes="Confidential staff assessment"), self.admin)
        applicant_view = main.get_application(application_id, self.applicant)
        self.assertEqual(applicant_view["status"], "REJECTED")
        self.assertEqual(applicant_view["analyst_notes"], "")
        decision_events = [event for event in applicant_view["timeline"] if event["action"] == "ANALYST_DECISION_RECORDED"]
        self.assertEqual(decision_events[0]["details"], {"decision": "REJECTED"})
        self.assertNotIn("Confidential staff assessment", str(applicant_view["timeline"]))
        self._assert_counts(application_id, decisions=1, notifications=1)

    def test_request_information_then_resubmission_keeps_prediction_history(self) -> None:
        application_id = self._application("UNDER_REVIEW", with_prediction=True)
        with self.assertRaises(HTTPException) as missing_note:
            main.record_decision(application_id, DecisionInput(decision="NEEDS_MORE_INFORMATION", notes="   "), self.admin)
        self.assertEqual(missing_note.exception.status_code, 422)

        request_note = "Please confirm your annual income."
        main.record_decision(application_id, DecisionInput(decision="NEEDS_MORE_INFORMATION", notes=request_note), self.admin)
        awaiting = main.get_application(application_id, self.applicant)
        self.assertEqual(awaiting["status"], "NEEDS_MORE_INFORMATION")
        self.assertEqual(awaiting["analyst_notes"], request_note)
        self.assertEqual(awaiting["prediction"]["model_version"], "test-v1")
        self._assert_counts(application_id, decisions=1, notifications=1)

        model_result = {"probability_of_default": 0.4, "model_version": "test-v2", "factors": [], "range_warnings": []}
        with patch.object(main.model_service, "predict", return_value=model_result):
            resubmitted = main.resubmit_information(application_id, ApplicationInput(**{**APPLICATION, "annual_income": 80000}), self.applicant)

        self.assertEqual(resubmitted["status"], "AI_ASSESSED")
        self.assertEqual(resubmitted["decision"], None)
        self.assertEqual(resubmitted["analyst_notes"], "")
        detail = main.get_application(application_id, self.applicant)
        self.assertEqual(len(detail["prediction_history"]), 2)
        self.assertIn(request_note, str(detail["timeline"]))

    def test_applicant_cannot_decide_and_unauthenticated_session_is_rejected(self) -> None:
        application_id = self._application("UNDER_REVIEW")
        with self.assertRaises(HTTPException) as denied:
            main.record_decision(application_id, DecisionInput(decision="APPROVED"), self.applicant)
        self.assertEqual(denied.exception.status_code, 403)

        request = SimpleNamespace(
            cookies={},
            app=SimpleNamespace(state=SimpleNamespace(session_secret=main.app.state.session_secret)),
        )
        with self.assertRaises(HTTPException) as unauthenticated:
            main._current_user(request)
        self.assertEqual(unauthenticated.exception.status_code, 401)
        with database.connect() as db:
            status = db.execute("SELECT status FROM applications WHERE id=?", (application_id,)).fetchone()["status"]
        self.assertEqual(status, "UNDER_REVIEW")
        self._assert_counts(application_id, decisions=0, notifications=0)

    def test_invalid_transitions_and_duplicate_decisions_are_rejected(self) -> None:
        application_id = self._application("AI_ASSESSED")
        with self.assertRaises(HTTPException) as not_reviewed:
            main.record_decision(application_id, DecisionInput(decision="APPROVED"), self.admin)
        self.assertEqual(not_reviewed.exception.status_code, 409)

        main.start_review(application_id, self.admin)
        main.record_decision(application_id, DecisionInput(decision="APPROVED"), self.admin)
        with self.assertRaises(HTTPException) as contradictory:
            main.record_decision(application_id, DecisionInput(decision="REJECTED"), self.admin)
        self.assertEqual(contradictory.exception.status_code, 409)
        self._assert_counts(application_id, decisions=1, notifications=1)

    def test_concurrent_decisions_create_one_outcome_and_one_notification(self) -> None:
        application_id = self._application("UNDER_REVIEW")

        def approve() -> str:
            try:
                main.record_decision(application_id, DecisionInput(decision="APPROVED"), self.admin)
                return "saved"
            except HTTPException as error:
                return f"rejected-{error.status_code}"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _index: approve(), range(2)))
        self.assertCountEqual(outcomes, ["saved", "rejected-409"])
        self._assert_counts(application_id, decisions=1, notifications=1)

    def test_decision_audit_and_notification_rollback_together(self) -> None:
        application_id = self._application("UNDER_REVIEW")
        with patch.object(database, "create_notification", side_effect=sqlite3.IntegrityError("notification failure")):
            with self.assertRaises(sqlite3.IntegrityError):
                main.record_decision(application_id, DecisionInput(decision="APPROVED"), self.admin)
        with database.connect() as db:
            row = db.execute("SELECT status,decision,decision_at FROM applications WHERE id=?", (application_id,)).fetchone()
        self.assertEqual(row["status"], "UNDER_REVIEW")
        self.assertIsNone(row["decision"])
        self.assertIsNone(row["decision_at"])
        self._assert_counts(application_id, decisions=0, notifications=0)

    def test_notification_access_is_recipient_scoped_and_read_is_idempotent(self) -> None:
        application_id = self._application("UNDER_REVIEW")
        main.record_decision(application_id, DecisionInput(decision="APPROVED"), self.admin)
        inbox = main.list_notifications(self.applicant, page=1, page_size=50)
        notification_id = inbox["items"][0]["id"]

        other_inbox = main.list_notifications(self.other_applicant, page=1, page_size=50)
        self.assertEqual(other_inbox["items"], [])
        with self.assertRaises(HTTPException) as private:
            main.mark_notification_read(notification_id, self.other_applicant)
        self.assertEqual(private.exception.status_code, 404)
        with self.assertRaises(HTTPException) as private_application:
            main.get_application(application_id, self.other_applicant)
        self.assertEqual(private_application.exception.status_code, 404)

        marked = main.mark_notification_read(notification_id, self.applicant)
        self.assertTrue(marked["is_read"])
        first_read_at = marked["read_at"]
        marked_again = main.mark_notification_read(notification_id, self.applicant)
        self.assertEqual(marked_again["read_at"], first_read_at)
        self.assertEqual(main.list_notifications(self.applicant, page=1, page_size=50)["unread_count"], 0)

    def test_notification_routes_are_registered(self) -> None:
        routes = {
            (route.path, method)
            for route in main.app.routes
            for method in getattr(route, "methods", set())
        }
        self.assertIn(("/api/notifications", "GET"), routes)
        self.assertIn(("/api/notifications/{notification_id}/read", "PATCH"), routes)

    def test_http_flow_uses_sessions_and_updates_applicant_inbox(self) -> None:
        model_result = {"probability_of_default": 0.25, "model_version": "test-v1", "factors": [], "range_warnings": []}
        with patch.object(main.model_service, "predict", return_value=model_result):
            applicant_status, applicant_headers, applicant = self._http_request(
                "POST",
                "/api/auth/register",
                {"full_name": "Example Applicant", "email": "http-applicant@example.test", "password": "test-password-long-enough"},
            )
            self.assertEqual(applicant_status, 201)
            applicant_cookie = applicant_headers["set-cookie"].split(";", 1)[0]
            submit_status, _, submitted = self._http_request("POST", "/api/applications", APPLICATION, applicant_cookie)
            self.assertEqual(submit_status, 201)

        admin_status, admin_headers, _ = self._http_request(
            "POST", "/api/auth/login", {"email": self.admin["email"], "password": "unit-test-password-long"}
        )
        self.assertEqual(admin_status, 200)
        admin_cookie = admin_headers["set-cookie"].split(";", 1)[0]
        app_id = submitted["id"]

        review_status, _, review = self._http_request("POST", f"/api/applications/{app_id}/review", cookie=admin_cookie)
        self.assertEqual(review_status, 200)
        self.assertEqual(review["status"], "UNDER_REVIEW")

        denied_status, _, _ = self._http_request(
            "POST", f"/api/applications/{app_id}/decision", {"decision": "APPROVED", "notes": ""}, applicant_cookie
        )
        self.assertEqual(denied_status, 403)

        decision_status, _, decision = self._http_request(
            "POST", f"/api/applications/{app_id}/decision", {"decision": "APPROVED", "notes": "Private reviewer note"}, admin_cookie
        )
        self.assertEqual(decision_status, 200)
        self.assertEqual(decision["status"], "APPROVED")

        inbox_status, _, inbox = self._http_request("GET", "/api/notifications", cookie=applicant_cookie)
        self.assertEqual(inbox_status, 200)
        self.assertEqual(inbox["unread_count"], 1)
        notification_id = inbox["items"][0]["id"]
        self.assertNotIn("Private reviewer note", inbox["items"][0]["message"])

        detail_status, _, detail = self._http_request("GET", f"/api/applications/{app_id}", cookie=applicant_cookie)
        self.assertEqual(detail_status, 200)
        self.assertEqual(detail["status"], "APPROVED")
        self.assertEqual(detail["analyst_notes"], "")
        self.assertIn("status_updated_at", detail)

        read_status, _, read = self._http_request("PATCH", f"/api/notifications/{notification_id}/read", cookie=applicant_cookie)
        self.assertEqual(read_status, 200)
        self.assertTrue(read["is_read"])

    def test_schema_upgrade_preserves_existing_records(self) -> None:
        application_id = self._application("UNDER_REVIEW")
        with database.connect() as db:
            db.execute("DROP TABLE notifications")
            db.execute("ALTER TABLE applications DROP COLUMN status_updated_at")

        database.init_database()
        with database.connect() as db:
            application = db.execute("SELECT status,status_updated_at FROM applications WHERE id=?", (application_id,)).fetchone()
            user_exists = db.execute("SELECT 1 FROM users WHERE id=?", (self.applicant["id"],)).fetchone()
            notification_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notifications'").fetchone()
        self.assertEqual(application["status"], "UNDER_REVIEW")
        self.assertIsNotNone(application["status_updated_at"])
        self.assertIsNotNone(user_exists)
        self.assertIsNotNone(notification_table)


if __name__ == "__main__":
    unittest.main()
