from __future__ import annotations

import asyncio
import csv
import html
import io
import json
import logging
import os
import re
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app import database
from app.schemas import (
    ApplicationInput,
    DecisionInput,
    LoginInput,
    RegisterInput,
    RiskThresholdInput,
    UserActiveInput,
    UserRoleInput,
)
from app.security import create_session, hash_password, read_session, session_secret, verify_password
from ml.service import model_service


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("credit_risk")
SESSION_COOKIE = "crediguard_session"
COOKIE_LIFETIME = 8 * 60 * 60
COOKIE_SECURE_CONFIG = os.getenv("COOKIE_SECURE", "auto").strip().lower()
DOMAIN = os.getenv("APP_DOMAIN", "http://localhost").strip().lower()
COOKIE_SECURE = COOKIE_SECURE_CONFIG == "true" or (
    COOKIE_SECURE_CONFIG == "auto" and DOMAIN.startswith("https://")
)
RATE_LIMITS: dict[str, tuple[int, int]] = {
    "/api/auth/login": (10, 60),
    "/api/auth/register": (5, 60),
    "/api/applications": (20, 60),
}
_request_times: defaultdict[str, deque[float]] = defaultdict(deque)
_rate_lock = asyncio.Lock()
APPLICANT_STATUS_GUIDANCE = {
    "AI_ASSESSED": {
        "summary": "Your application has been submitted and assessed. It is waiting for an authorized member of the credit team to review it.",
        "next_action": "No action is needed right now. Check this page for updates.",
    },
    "UNDER_REVIEW": {
        "summary": "An authorized member of the credit team is reviewing your application. A final decision has not been recorded yet.",
        "next_action": "Please wait for the review to finish. The current status will update here.",
    },
    "APPROVED": {
        "summary": "Your application has been reviewed, and an authorized member of the credit team has recorded an approval decision.",
        "next_action": "Review this status for any further instructions. This demonstration does not create a binding loan offer, complete verification, sign an agreement, or transfer money.",
    },
    "REJECTED": {
        "summary": "An authorized member of the credit team has recorded a rejection decision for your application.",
        "next_action": "This page records the decision in the CrediGuard demonstration. No funds or financial agreement are created by this application.",
    },
    "NEEDS_MORE_INFORMATION": {
        "summary": "The credit team needs more information before it can continue reviewing your application.",
        "next_action": "Read the request below, update the application details, and resubmit them for a new assessment.",
    },
}


@asynccontextmanager
async def lifespan(application: FastAPI):
    database.init_database()
    database.ensure_bootstrap_admin()
    application.state.session_secret = session_secret(database.DATA_DIR)
    model_service.startup()
    logger.info("Application started with active model %s", model_service.info()["model_version"])
    yield


app = FastAPI(
    title="CrediGuard AI",
    description=(
        "Credit risk decision-support demo. The supplied dataset is one historical LendingClub cohort; "
        "predictions are not lending decisions."
    ),
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.middleware("http")
async def security_and_request_logging(request: Request, call_next):
    path = request.url.path
    limit = RATE_LIMITS.get(path) if request.method == "POST" else None
    if limit:
        forwarded = request.headers.get("x-forwarded-for", "")
        ip = forwarded.split(",")[-1].strip() if forwarded else (request.client.host if request.client else "unknown")
        bucket = f"{path}:{ip}"
        maximum, period = limit
        now = time.monotonic()
        async with _rate_lock:
            recent = _request_times[bucket]
            while recent and recent[0] < now - period:
                recent.popleft()
            if len(recent) >= maximum:
                return JSONResponse(status_code=429, content={"detail": "Too many requests. Please wait and try again."})
            recent.append(now)
            if len(_request_times) > 5000:
                for _ in range(1000):
                    if len(_request_times) <= 4000:
                        break
                    oldest_key = next(iter(_request_times))
                    _request_times.pop(oldest_key, None)

    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled request error for %s %s", request.method, path)
        response = JSONResponse(status_code=500, content={"detail": "An unexpected server error occurred."})
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    )
    if DOMAIN.startswith("https://"):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    elapsed_ms = (time.monotonic() - started) * 1000
    logger.info("%s %s %s %.1fms", request.method, path, response.status_code, elapsed_ms)
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    errors = []
    for error in exc.errors():
        location = ".".join(str(piece) for piece in error.get("loc", ()) if piece != "body")
        errors.append({"field": location, "message": error.get("msg", "Invalid value")})
    return JSONResponse(status_code=422, content={"detail": "Please check the submitted fields.", "errors": errors})


def _current_user(request: Request) -> dict[str, Any]:
    token = request.cookies.get(SESSION_COOKIE)
    payload = read_session(token or "", request.app.state.session_secret)
    if not payload:
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    with database.connect() as db:
        row = db.execute("SELECT * FROM users WHERE id=? AND active=1", (payload["sub"],)).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Your session is no longer active. Please sign in again.")
    return database.user_public(row)


def _staff(user: dict[str, Any]) -> None:
    if user["role"] not in {"ANALYST", "ADMIN"}:
        raise HTTPException(status_code=403, detail="This action is available to analysts and administrators.")


def _admin(user: dict[str, Any]) -> None:
    if user["role"] != "ADMIN":
        raise HTTPException(status_code=403, detail="Administrator access is required.")


def _set_session(response: Response, user: dict[str, Any], request: Request) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        create_session(user, request.app.state.session_secret, COOKIE_LIFETIME),
        max_age=COOKIE_LIFETIME,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
        path="/",
    )


def _feature_payload(payload: ApplicationInput) -> dict[str, Any]:
    return payload.model_dump()


def _create_prediction(
    db,
    application_id: str,
    features: dict[str, Any],
    actor: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    model_result = model_service.predict(features)
    thresholds = database.get_thresholds(db)
    probability = model_result["probability_of_default"]
    risk_category = database.classify_risk(probability, thresholds)
    prediction_id = str(uuid.uuid4())
    created_at = database.utc_now()
    db.execute(
        """INSERT INTO predictions(id,application_id,model_version,probability_of_default,risk_score,risk_category,thresholds,factors,range_warnings,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            prediction_id,
            application_id,
            model_result["model_version"],
            probability,
            probability * 100,
            risk_category,
            json.dumps(thresholds, separators=(",", ":")),
            json.dumps(model_result["factors"], separators=(",", ":")),
            json.dumps(model_result["range_warnings"], separators=(",", ":")),
            created_at,
        ),
    )
    db.execute(
        "UPDATE applications SET latest_prediction_id=?,updated_at=? WHERE id=?",
        (prediction_id, created_at, application_id),
    )
    database.audit(
        db,
        actor,
        "PREDICTION_GENERATED",
        "application",
        application_id,
        {"model_version": model_result["model_version"], "risk_category": risk_category, "thresholds": thresholds},
    )
    return prediction_id, {
        "id": prediction_id,
        "model_version": model_result["model_version"],
        "probability_of_default": probability,
        "risk_score": probability * 100,
        "risk_category": risk_category,
        "thresholds": thresholds,
        "factors": model_result["factors"],
        "range_warnings": model_result["range_warnings"],
        "created_at": created_at,
    }


def _get_application(db, application_id: str):
    return db.execute(database.APPLICATION_SELECT + " WHERE a.id=?", (application_id,)).fetchone()


def _check_application_access(row, user: dict[str, Any]) -> None:
    if user["role"] == "APPLICANT" and row["applicant_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="Application not found.")


def _application_audit(db, application_id: str, applicant_view: bool = False) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT actor_email,actor_role,action,details,created_at FROM audit_events WHERE resource_type='application' AND resource_id=? ORDER BY id ASC",
        (application_id,),
    ).fetchall()
    events = []
    for row in rows:
        details = json.loads(row["details"])
        if applicant_view:
            if row["action"] not in {
                "APPLICATION_SUBMITTED",
                "PREDICTION_GENERATED",
                "ANALYST_REVIEW_STARTED",
                "ANALYST_DECISION_RECORDED",
                "APPLICANT_INFORMATION_RESUBMITTED",
            }:
                continue
            decision = details.get("decision")
            details = {"decision": decision} if row["action"] == "ANALYST_DECISION_RECORDED" else {}
            if decision == "NEEDS_MORE_INFORMATION":
                original = json.loads(row["details"])
                details["notes"] = original.get("notes", "")
        events.append(
            {
                "actor": "You" if applicant_view and row["actor_role"] == "APPLICANT" else "Credit team" if applicant_view else row["actor_email"],
                "action": row["action"],
                "details": details,
                "created_at": row["created_at"],
            }
        )
    return events


def _prediction_history(db, application_id: str) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT * FROM predictions WHERE application_id=? ORDER BY created_at DESC,id DESC",
        (application_id,),
    ).fetchall()
    return [database.prediction_public(row) for row in rows]


def _visible_application(row, user: dict[str, Any]) -> dict[str, Any]:
    result = database.application_public(row)
    if user["role"] == "APPLICANT":
        result["applicant_email"] = None
        if result["status"] != "NEEDS_MORE_INFORMATION":
            result["analyst_notes"] = ""
        result["analyst_id"] = None
        result["analyst_name"] = None
        result["applicant_status"] = APPLICANT_STATUS_GUIDANCE.get(
            result["status"],
            {"summary": "Your application status is available here.", "next_action": "Check this page for updates."},
        )
    return result


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/docs", include_in_schema=False)
def api_reference():
    return FileResponse(Path(__file__).parent / "static" / "api-docs.html")


@app.get("/api/health")
def health():
    try:
        with database.connect() as db:
            db.execute("SELECT 1").fetchone()
        db_status = "ok"
    except Exception:
        db_status = "unavailable"
    model_status = "ok" if model_service.is_ready() else "unavailable"
    status = "ok" if db_status == "ok" and model_status == "ok" else "degraded"
    return {"status": status, "database": db_status, "model": model_status}


@app.post("/api/auth/register", status_code=201)
def register(payload: RegisterInput, request: Request, response: Response):
    if os.getenv("ALLOW_REGISTRATION", "true").strip().lower() not in {"true", "1", "yes"}:
        raise HTTPException(status_code=403, detail="Applicant registration is currently closed.")
    user_id = str(uuid.uuid4())
    with database.connect() as db:
        try:
            db.execute(
                "INSERT INTO users(id,full_name,email,password_hash,role,active,created_at) VALUES(?,?,?,?,?,?,?)",
                (user_id, payload.full_name, payload.email, hash_password(payload.password), "APPLICANT", 1, database.utc_now()),
            )
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise HTTPException(status_code=409, detail="An account with that email already exists.") from None
            raise
        user_row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        user = database.user_public(user_row)
        database.audit(db, user, "USER_REGISTERED", "user", user_id, {"role": "APPLICANT"})
    _set_session(response, user, request)
    return {"user": user}


@app.post("/api/auth/login")
def login(payload: LoginInput, request: Request, response: Response):
    user = None
    with database.connect() as db:
        row = db.execute("SELECT * FROM users WHERE email=? COLLATE NOCASE", (payload.email.strip().lower(),)).fetchone()
        if not row or not row["active"] or not verify_password(payload.password, row["password_hash"]):
            database.audit(db, None, "LOGIN_FAILED", "authentication", None, {})
        else:
            user = database.user_public(row)
            database.audit(db, user, "USER_LOGIN", "user", user["id"], {})
    if user is None:
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    _set_session(response, user, request)
    return {"user": user}


@app.post("/api/auth/logout")
def logout(response: Response, user: dict[str, Any] = Depends(_current_user)):
    with database.connect() as db:
        database.audit(db, user, "USER_LOGOUT", "user", user["id"], {})
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=COOKIE_SECURE, samesite="strict")
    return {"ok": True}


@app.get("/api/auth/me")
def who_am_i(user: dict[str, Any] = Depends(_current_user)):
    return {"user": user}


@app.get("/api/notifications")
def list_notifications(
    user: dict[str, Any] = Depends(_current_user),
    page: int = Query(default=1, ge=1, le=100000),
    page_size: int = Query(default=50, ge=1, le=100),
):
    with database.connect() as db:
        total = db.execute(
            "SELECT COUNT(*) AS n FROM notifications WHERE recipient_id=?",
            (user["id"],),
        ).fetchone()["n"]
        unread_count = db.execute(
            "SELECT COUNT(*) AS n FROM notifications WHERE recipient_id=? AND read_at IS NULL",
            (user["id"],),
        ).fetchone()["n"]
        rows = db.execute(
            "SELECT * FROM notifications WHERE recipient_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
            (user["id"], page_size, (page - 1) * page_size),
        ).fetchall()
    return {
        "items": [database.notification_public(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": max(1, (total + page_size - 1) // page_size),
        "unread_count": unread_count,
    }


@app.patch("/api/notifications/{notification_id}/read")
def mark_notification_read(notification_id: str, user: dict[str, Any] = Depends(_current_user)):
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM notifications WHERE id=? AND recipient_id=?",
            (notification_id, user["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Notification not found.")
        if row["read_at"] is None:
            db.execute(
                "UPDATE notifications SET read_at=? WHERE id=? AND recipient_id=? AND read_at IS NULL",
                (database.utc_now(), notification_id, user["id"]),
            )
            database.audit(
                db,
                user,
                "NOTIFICATION_READ",
                "notification",
                notification_id,
                {"application_id": row["application_id"]},
            )
        updated = db.execute(
            "SELECT * FROM notifications WHERE id=? AND recipient_id=?",
            (notification_id, user["id"]),
        ).fetchone()
    return database.notification_public(updated)


@app.get("/api/dashboard/summary")
def dashboard_summary(user: dict[str, Any] = Depends(_current_user)):
    if user["role"] == "APPLICANT":
        with database.connect() as db:
            rows = db.execute(database.APPLICATION_SELECT + " WHERE a.applicant_id=? ORDER BY a.created_at DESC", (user["id"],)).fetchall()
        applications = [_visible_application(row, user) for row in rows]
        pending = sum(1 for item in applications if item["status"] in {"AI_ASSESSED", "UNDER_REVIEW", "NEEDS_MORE_INFORMATION"})
        return {"scope": "applicant", "total_applications": len(applications), "in_progress": pending, "applications": applications[:5]}

    _staff(user)
    with database.connect() as db:
        counts = db.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN status IN ('AI_ASSESSED','UNDER_REVIEW','NEEDS_MORE_INFORMATION') THEN 1 ELSE 0 END) AS pending,
                      SUM(CASE WHEN status='APPROVED' THEN 1 ELSE 0 END) AS approved,
                      SUM(CASE WHEN status='REJECTED' THEN 1 ELSE 0 END) AS rejected,
                      AVG(CAST(json_extract(application_data,'$.loan_amount') AS REAL)) AS avg_loan
               FROM applications"""
        ).fetchone()
        risks = db.execute(
            """SELECT p.risk_category,COUNT(*) AS count FROM applications a
               JOIN predictions p ON p.id=a.latest_prediction_id GROUP BY p.risk_category"""
        ).fetchall()
        recent = db.execute(
            """SELECT substr(created_at,1,7) AS month,COUNT(*) AS count FROM applications
               GROUP BY substr(created_at,1,7) ORDER BY month DESC LIMIT 6"""
        ).fetchall()
        average_risk = db.execute("SELECT AVG(risk_score) AS value FROM predictions p JOIN applications a ON a.latest_prediction_id=p.id").fetchone()["value"]
        model_row = db.execute("SELECT metadata FROM model_runs WHERE active=1 LIMIT 1").fetchone()
    metadata = json.loads(model_row["metadata"]) if model_row else {}
    return {
        "scope": "portfolio",
        "total_applications": counts["total"] or 0,
        "pending_review": counts["pending"] or 0,
        "approved": counts["approved"] or 0,
        "rejected": counts["rejected"] or 0,
        "risk_distribution": {row["risk_category"]: row["count"] for row in risks},
        "average_loan_amount": round(counts["avg_loan"], 2) if counts["avg_loan"] is not None else None,
        "average_risk_score": round(average_risk, 2) if average_risk is not None else None,
        "applications_by_month": list(reversed([dict(row) for row in recent])),
        "historical_default_rate": metadata.get("dataset", {}).get("default_rate_among_matured"),
        "historical_default_rate_note": "Share of Charged Off loans among completed loans in the supplied historical cohort; not the portfolio default rate.",
    }


@app.get("/api/applications")
def list_applications(
    user: dict[str, Any] = Depends(_current_user),
    status: str | None = Query(default=None, max_length=40),
    risk: str | None = Query(default=None, pattern="^(LOW|MEDIUM|HIGH)$"),
    search: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1, le=100000),
    page_size: int = Query(default=20, ge=1, le=50),
):
    clauses: list[str] = []
    params: list[Any] = []
    if user["role"] == "APPLICANT":
        clauses.append("a.applicant_id=?")
        params.append(user["id"])
    else:
        _staff(user)
    if status:
        allowed = {"AI_ASSESSED", "UNDER_REVIEW", "APPROVED", "REJECTED", "NEEDS_MORE_INFORMATION"}
        if status not in allowed:
            raise HTTPException(status_code=422, detail="Unknown application status.")
        clauses.append("a.status=?")
        params.append(status)
    if risk:
        clauses.append("p.risk_category=?")
        params.append(risk)
    if search:
        safe_search = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("(a.id LIKE ? ESCAPE '\\' OR a.applicant_name LIKE ? ESCAPE '\\' OR u.email LIKE ? ESCAPE '\\')")
        params.extend([f"%{safe_search}%"] * 3)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with database.connect() as db:
        total = db.execute("SELECT COUNT(*) AS n FROM applications a JOIN users u ON u.id=a.applicant_id LEFT JOIN predictions p ON p.id=a.latest_prediction_id" + where, params).fetchone()["n"]
        rows = db.execute(
            database.APPLICATION_SELECT + where + " ORDER BY a.created_at DESC LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    return {"items": [_visible_application(row, user) for row in rows], "page": page, "page_size": page_size, "total": total, "pages": max(1, (total + page_size - 1) // page_size)}


@app.post("/api/applications", status_code=201)
def create_application(payload: ApplicationInput, user: dict[str, Any] = Depends(_current_user)):
    if user["role"] != "APPLICANT":
        raise HTTPException(status_code=403, detail="Only applicant accounts can submit an application.")
    application_id = str(uuid.uuid4())
    now = database.utc_now()
    values = _feature_payload(payload)
    with database.connect() as db:
        db.execute(
            """INSERT INTO applications(id,applicant_id,status,applicant_name,application_data,created_at,updated_at,status_updated_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (application_id, user["id"], "AI_ASSESSED", user["full_name"], json.dumps(values, separators=(",", ":")), now, now, now),
        )
        _prediction_id, prediction = _create_prediction(db, application_id, values, user)
        database.audit(db, user, "APPLICATION_SUBMITTED", "application", application_id, {"status": "AI_ASSESSED"})
        row = _get_application(db, application_id)
    result = _visible_application(row, user)
    result["prediction"] = prediction
    return result


@app.get("/api/applications/{application_id}")
def get_application(application_id: str, user: dict[str, Any] = Depends(_current_user)):
    with database.connect() as db:
        row = _get_application(db, application_id)
        if not row:
            raise HTTPException(status_code=404, detail="Application not found.")
        _check_application_access(row, user)
        if user["role"] in {"ANALYST", "ADMIN"}:
            database.audit(db, user, "ANALYST_APPLICATION_VIEWED", "application", application_id, {})
        result = _visible_application(row, user)
        result["prediction_history"] = _prediction_history(db, application_id)
        if user["role"] in {"ANALYST", "ADMIN"} or row["applicant_id"] == user["id"]:
            result["timeline"] = _application_audit(db, application_id, applicant_view=user["role"] == "APPLICANT")
    return result


@app.post("/api/applications/{application_id}/predict")
def reassess_application(application_id: str, user: dict[str, Any] = Depends(_current_user)):
    _staff(user)
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _get_application(db, application_id)
        if not row:
            raise HTTPException(status_code=404, detail="Application not found.")
        if row["status"] not in {"AI_ASSESSED", "UNDER_REVIEW"}:
            raise HTTPException(status_code=409, detail="This application is not available for a new assessment in its current state.")
        values = json.loads(row["application_data"])
        _prediction_id, prediction = _create_prediction(db, application_id, values, user)
        database.audit(db, user, "APPLICATION_REASSESSED", "application", application_id, {"model_version": prediction["model_version"]})
        updated = _get_application(db, application_id)
    result = _visible_application(updated, user)
    result["prediction"] = prediction
    return result


@app.post("/api/applications/{application_id}/review")
def start_review(application_id: str, user: dict[str, Any] = Depends(_current_user)):
    _staff(user)
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _get_application(db, application_id)
        if not row:
            raise HTTPException(status_code=404, detail="Application not found.")
        if row["status"] != "AI_ASSESSED":
            raise HTTPException(status_code=409, detail="This application is not waiting for analyst review.")
        now = database.utc_now()
        changed = db.execute(
            "UPDATE applications SET status='UNDER_REVIEW',analyst_id=?,updated_at=?,status_updated_at=? WHERE id=? AND status='AI_ASSESSED'",
            (user["id"], now, now, application_id),
        )
        if changed.rowcount != 1:
            raise HTTPException(status_code=409, detail="This application is no longer waiting for analyst review.")
        database.audit(db, user, "ANALYST_REVIEW_STARTED", "application", application_id, {"status": "UNDER_REVIEW"})
        row = _get_application(db, application_id)
    return _visible_application(row, user)


@app.post("/api/applications/{application_id}/decision")
def record_decision(application_id: str, payload: DecisionInput, user: dict[str, Any] = Depends(_current_user)):
    _staff(user)
    if payload.decision == "NEEDS_MORE_INFORMATION" and not payload.notes:
        raise HTTPException(status_code=422, detail="Add a note explaining what information is required.")
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _get_application(db, application_id)
        if not row:
            raise HTTPException(status_code=404, detail="Application not found.")
        if row["status"] != "UNDER_REVIEW":
            raise HTTPException(status_code=409, detail="Start analyst review before recording a decision. This application may already have a decision or be awaiting the applicant.")
        now = database.utc_now()
        changed = db.execute(
            "UPDATE applications SET status=?,decision=?,decision_at=?,analyst_id=?,analyst_notes=?,updated_at=?,status_updated_at=? WHERE id=? AND status='UNDER_REVIEW'",
            (payload.decision, payload.decision, now, user["id"], payload.notes, now, now, application_id),
        )
        if changed.rowcount != 1:
            raise HTTPException(status_code=409, detail="Another reviewer updated this application. Reload it before trying again.")
        event_id = database.audit(
            db,
            user,
            "ANALYST_DECISION_RECORDED",
            "application",
            application_id,
            {"decision": payload.decision, "notes": payload.notes},
        )
        notification_type, notification_message = {
            "APPROVED": (
                "APPLICATION_APPROVED",
                "Your application has been reviewed, and an authorized member of the credit team has recorded an approval decision. Please review the application status for any further instructions. This demonstration does not create a binding loan offer or transfer funds.",
            ),
            "REJECTED": (
                "APPLICATION_REJECTED",
                "An authorized member of the credit team has recorded a rejection decision for your application. View the application status page for the recorded outcome.",
            ),
            "NEEDS_MORE_INFORMATION": (
                "APPLICATION_INFORMATION_REQUESTED",
                "The credit team needs more information to continue reviewing your application. Open the application status page to read the request and respond.",
            ),
        }[payload.decision]
        database.create_notification(
            db,
            row["applicant_id"],
            application_id,
            event_id,
            notification_type,
            notification_message,
        )
        row = _get_application(db, application_id)
    return _visible_application(row, user)


@app.put("/api/applications/{application_id}/information")
def resubmit_information(application_id: str, payload: ApplicationInput, user: dict[str, Any] = Depends(_current_user)):
    if user["role"] != "APPLICANT":
        raise HTTPException(status_code=403, detail="Only the applicant can update requested information.")
    values = _feature_payload(payload)
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _get_application(db, application_id)
        if not row:
            raise HTTPException(status_code=404, detail="Application not found.")
        _check_application_access(row, user)
        if row["status"] != "NEEDS_MORE_INFORMATION":
            raise HTTPException(status_code=409, detail="This application is not awaiting more information.")
        now = database.utc_now()
        changed = db.execute(
            "UPDATE applications SET application_data=?,status='AI_ASSESSED',analyst_id=NULL,analyst_notes='',decision=NULL,decision_at=NULL,updated_at=?,status_updated_at=? WHERE id=? AND status='NEEDS_MORE_INFORMATION'",
            (json.dumps(values, separators=(",", ":")), now, now, application_id),
        )
        if changed.rowcount != 1:
            raise HTTPException(status_code=409, detail="This application is no longer awaiting more information.")
        _prediction_id, prediction = _create_prediction(db, application_id, values, user)
        database.audit(db, user, "APPLICANT_INFORMATION_RESUBMITTED", "application", application_id, {"status": "AI_ASSESSED"})
        row = _get_application(db, application_id)
    result = _visible_application(row, user)
    result["prediction"] = prediction
    return result


@app.get("/api/models/active")
def active_model(user: dict[str, Any] = Depends(_current_user)):
    _staff(user)
    metadata = model_service.info()
    with database.connect() as db:
        predictions = db.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()["n"]
        versions = [
            {"version": row["version"], "algorithm": row["algorithm"], "trained_at": row["trained_at"], "active": bool(row["active"])}
            for row in db.execute("SELECT version,algorithm,trained_at,active FROM model_runs ORDER BY id DESC LIMIT 10").fetchall()
        ]
    return {"metadata": metadata, "prediction_count": predictions, "versions": versions}


@app.post("/api/models/retrain")
def retrain_model(user: dict[str, Any] = Depends(_current_user)):
    _admin(user)
    try:
        metadata = model_service.train_and_activate()
    except Exception as exc:
        logger.exception("Model retraining failed")
        raise HTTPException(status_code=500, detail="Model retraining failed. The active model is unchanged.") from exc
    with database.connect() as db:
        database.audit(db, user, "MODEL_ACTIVATED", "model", metadata["model_version"], {"algorithm": metadata["algorithm"]})
    return {"metadata": metadata}


@app.get("/api/config/risk")
def get_risk_config(user: dict[str, Any] = Depends(_current_user)):
    _staff(user)
    with database.connect() as db:
        return {"thresholds": database.get_thresholds(db)}


@app.put("/api/config/risk")
def update_risk_config(payload: RiskThresholdInput, user: dict[str, Any] = Depends(_current_user)):
    _admin(user)
    thresholds = payload.model_dump()
    with database.connect() as db:
        previous = database.get_thresholds(db)
        db.execute(
            "UPDATE settings SET value=?,updated_at=? WHERE key='risk_thresholds'",
            (json.dumps(thresholds, separators=(",", ":")), database.utc_now()),
        )
        database.audit(db, user, "RISK_THRESHOLDS_CHANGED", "configuration", "risk_thresholds", {"previous": previous, "updated": thresholds})
    return {"thresholds": thresholds}


@app.get("/api/users")
def list_users(user: dict[str, Any] = Depends(_current_user), search: str | None = Query(default=None, max_length=100)):
    _admin(user)
    with database.connect() as db:
        if search:
            needle = f"%{search.strip()}%"
            rows = db.execute("SELECT * FROM users WHERE full_name LIKE ? OR email LIKE ? ORDER BY created_at DESC LIMIT 200", (needle, needle)).fetchall()
        else:
            rows = db.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT 200").fetchall()
    return {"items": [database.user_public(row) for row in rows]}


@app.patch("/api/users/{user_id}/role")
def change_user_role(user_id: str, payload: UserRoleInput, user: dict[str, Any] = Depends(_current_user)):
    _admin(user)
    if user_id == user["id"]:
        raise HTTPException(status_code=409, detail="You cannot change your own account role.")
    with database.connect() as db:
        target = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="User not found.")
        if target["role"] == "ADMIN":
            raise HTTPException(status_code=409, detail="Administrator roles are managed through server bootstrap configuration.")
        db.execute("UPDATE users SET role=? WHERE id=?", (payload.role, user_id))
        database.audit(db, user, "USER_ROLE_CHANGED", "user", user_id, {"from": target["role"], "to": payload.role})
        updated = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return {"user": database.user_public(updated)}


@app.patch("/api/users/{user_id}/active")
def change_user_active(user_id: str, payload: UserActiveInput, user: dict[str, Any] = Depends(_current_user)):
    _admin(user)
    if user_id == user["id"]:
        raise HTTPException(status_code=409, detail="You cannot deactivate your own account.")
    with database.connect() as db:
        target = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="User not found.")
        if target["role"] == "ADMIN":
            raise HTTPException(status_code=409, detail="Administrator accounts are managed through server bootstrap configuration.")
        db.execute("UPDATE users SET active=? WHERE id=?", (1 if payload.active else 0, user_id))
        database.audit(db, user, "USER_ACTIVATED" if payload.active else "USER_DEACTIVATED", "user", user_id, {})
        updated = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return {"user": database.user_public(updated)}


@app.get("/api/audit")
def audit_log(user: dict[str, Any] = Depends(_current_user), page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=100)):
    _admin(user)
    offset = (page - 1) * page_size
    with database.connect() as db:
        total = db.execute("SELECT COUNT(*) AS n FROM audit_events").fetchone()["n"]
        rows = db.execute("SELECT * FROM audit_events ORDER BY id DESC LIMIT ? OFFSET ?", (page_size, offset)).fetchall()
    return {
        "items": [
            {"id": row["id"], "actor_email": row["actor_email"], "actor_role": row["actor_role"], "action": row["action"], "resource_type": row["resource_type"], "resource_id": row["resource_id"], "details": json.loads(row["details"]), "created_at": row["created_at"]}
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


def _csv_cell(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + value
    return value


@app.get("/api/reports/applications.csv")
def report_applications(user: dict[str, Any] = Depends(_current_user)):
    _staff(user)
    with database.connect() as db:
        rows = db.execute(database.APPLICATION_SELECT + " ORDER BY a.created_at DESC").fetchall()
        database.audit(db, user, "REPORT_EXPORTED", "report", "applications_csv", {"records": len(rows)})
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["application_id", "application_date", "applicant_name", "applicant_email", "loan_amount", "loan_term_months", "annual_income", "purpose", "status", "probability_of_default", "risk_score", "risk_category", "low_risk_maximum", "medium_risk_maximum", "model_version", "analyst_decision", "analyst_notes", "decision_at"])
    for row in rows:
        app_data = json.loads(row["application_data"])
        item = database.application_public(row)
        prediction = item["prediction"] or {}
        risk_thresholds = prediction.get("thresholds") or {}
        writer.writerow([_csv_cell(item["id"]), item["created_at"], _csv_cell(item["applicant_name"]), _csv_cell(item["applicant_email"]), app_data.get("loan_amount"), app_data.get("term_months"), app_data.get("annual_income"), app_data.get("purpose"), item["status"], prediction.get("probability_of_default"), prediction.get("risk_score"), prediction.get("risk_category"), risk_thresholds.get("low_risk_maximum"), risk_thresholds.get("medium_risk_maximum"), prediction.get("model_version"), item["decision"], _csv_cell(item["analyst_notes"]), item["decision_at"]])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=creditguard-applications.csv"})


@app.get("/api/reports/applications/{application_id}.pdf")
def report_application_pdf(application_id: str, user: dict[str, Any] = Depends(_current_user)):
    with database.connect() as db:
        row = _get_application(db, application_id)
        if not row:
            raise HTTPException(status_code=404, detail="Application not found.")
        _check_application_access(row, user)
        item = _visible_application(row, user)
        database.audit(db, user, "REPORT_EXPORTED", "application", application_id, {"format": "pdf"})
    data = item["application_data"]
    prediction = item["prediction"] or {}
    output = io.BytesIO()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=20, leading=25, textColor=colors.HexColor("#102a43"), alignment=TA_CENTER, spaceAfter=4))
    styles.add(ParagraphStyle(name="ReportMeta", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#748896"), alignment=TA_CENTER, spaceAfter=13))
    styles.add(ParagraphStyle(name="SectionHeading", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=11, textColor=colors.HexColor("#087e78"), spaceBefore=11, spaceAfter=7))
    styles.add(ParagraphStyle(name="Cell", parent=styles["BodyText"], fontSize=8.5, leading=12, textColor=colors.HexColor("#27465a")))
    styles.add(ParagraphStyle(name="CellMuted", parent=styles["BodyText"], fontSize=7.5, leading=10, textColor=colors.HexColor("#718496")))
    styles.add(ParagraphStyle(name="Disclaimer", parent=styles["BodyText"], fontSize=8.5, leading=12, textColor=colors.HexColor("#5f6f78"), backColor=colors.HexColor("#f0f6f8"), borderPadding=8, spaceAfter=10))
    document = SimpleDocTemplate(output, pagesize=letter, rightMargin=0.62 * inch, leftMargin=0.62 * inch, topMargin=0.55 * inch, bottomMargin=0.55 * inch, title=f"Credit assessment {application_id[:8]}", author="CrediGuard AI")
    def paragraph(value, style="Cell"):
        text = str(value if value not in (None, "") else "—")
        text = "".join(character if character in "\n\t" or 32 <= ord(character) <= 255 else "?" for character in text)
        return Paragraph(html.escape(text), styles[style])
    story = [
        Paragraph("CrediGuard AI", styles["ReportTitle"]),
        Paragraph("CREDIT RISK ASSESSMENT · DECISION SUPPORT", styles["ReportMeta"]),
        Paragraph("This assessment is a probabilistic model output for human review. It is not a lending decision or a guarantee of repayment.", styles["Disclaimer"]),
        Paragraph("Application overview", styles["SectionHeading"]),
    ]
    summary_data = [
        [paragraph("Application ID", "CellMuted"), paragraph(item["id"]), paragraph("Status", "CellMuted"), paragraph(item["status"].replace("_", " "))],
        [paragraph("Applicant", "CellMuted"), paragraph(item["applicant_name"]), paragraph("Submitted", "CellMuted"), paragraph(item["created_at"])],
        [paragraph("Analyst decision", "CellMuted"), paragraph(item["decision"]), paragraph("Decision time", "CellMuted"), paragraph(item["decision_at"])],
    ]
    if user["role"] in {"ANALYST", "ADMIN"}:
        summary_data.insert(1, [paragraph("Applicant email", "CellMuted"), paragraph(item["applicant_email"]), paragraph("Analyst", "CellMuted"), paragraph(item["analyst_name"])])
    summary_table = Table(summary_data, colWidths=[1.0 * inch, 2.25 * inch, 1.0 * inch, 2.25 * inch], hAlign="LEFT")
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafb")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#e3eaee")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([summary_table, Paragraph("Applicant and financial profile", styles["SectionHeading"])])
    field_rows = [
        ("Requested loan amount", f"${float(data.get('loan_amount', 0)):,.2f}"), ("Term", f"{data.get('term_months', '—')} months"),
        ("Annual income", f"${float(data.get('annual_income', 0)):,.2f}"), ("Purpose", data.get("purpose")),
        ("Home ownership", data.get("home_ownership")), ("Employment length", data.get("employment_length") or "Not provided"),
        ("Debt-to-income", f"{data.get('dti', '—')}%"), ("FICO score", data.get("fico_score")),
        ("Prior delinquencies", data.get("prior_delinquencies")), ("Recent inquiries", data.get("recent_credit_inquiries")),
        ("Open accounts", data.get("open_accounts")), ("Public records", data.get("public_records")),
        ("Revolving balance", f"${float(data.get('revolving_balance', 0)):,.2f}"), ("Revolving utilization", f"{data.get('revolving_utilization', '—')}%"),
        ("Total accounts", data.get("total_accounts")),
    ]
    field_data = []
    for index in range(0, len(field_rows), 2):
        left = field_rows[index]
        right = field_rows[index + 1] if index + 1 < len(field_rows) else ("", "")
        field_data.append([paragraph(left[0], "CellMuted"), paragraph(left[1]), paragraph(right[0], "CellMuted"), paragraph(right[1])])
    field_table = Table(field_data, colWidths=[1.25 * inch, 2.0 * inch, 1.25 * inch, 2.0 * inch], hAlign="LEFT")
    field_table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#e7edf0")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.extend([field_table, Paragraph("Machine-learning assessment", styles["SectionHeading"])])
    if prediction:
        prediction_thresholds = prediction.get("thresholds") or {}
        score_data = [[paragraph("Probability of default", "CellMuted"), paragraph(f"{prediction['probability_of_default'] * 100:.2f}%"), paragraph("Risk score", "CellMuted"), paragraph(f"{prediction['risk_score']:.2f} / 100")], [paragraph("Risk category", "CellMuted"), paragraph(prediction["risk_category"]), paragraph("Model version", "CellMuted"), paragraph(prediction["model_version"])], [paragraph("Assessment generated", "CellMuted"), paragraph(prediction["created_at"]), paragraph("Analyst decision", "CellMuted"), paragraph(item["decision"])], [paragraph("LOW threshold", "CellMuted"), paragraph(f"Up to {prediction_thresholds['low_risk_maximum'] * 100:.0f}%" if "low_risk_maximum" in prediction_thresholds else "Unknown"), paragraph("MEDIUM threshold", "CellMuted"), paragraph(f"Up to {prediction_thresholds['medium_risk_maximum'] * 100:.0f}%" if "medium_risk_maximum" in prediction_thresholds else "Unknown")]]
        score_table = Table(score_data, colWidths=[1.25 * inch, 2.0 * inch, 1.25 * inch, 2.0 * inch], hAlign="LEFT")
        score_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f2f8f6")), ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#dcebe8")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
        story.extend([score_table, Paragraph("Top model sensitivities", styles["SectionHeading"])])
        factor_rows = [[paragraph("Feature", "CellMuted"), paragraph("Submitted value", "CellMuted"), paragraph("Sensitivity vs. reference", "CellMuted")]]
        for factor in prediction["factors"]:
            delta = factor["probability_change"] * 100
            factor_rows.append([paragraph(factor["label"]), paragraph(factor.get("value")), paragraph(f"{delta:+.1f} percentage points" if abs(delta) >= 0.05 else "Little change")])
        factor_table = Table(factor_rows, colWidths=[2.3 * inch, 1.8 * inch, 2.4 * inch], hAlign="LEFT")
        factor_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f6f8fa")), ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#e5ebef")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
        story.extend([factor_table, Spacer(1, 7), Paragraph(html.escape(model_service.info()["explanation_method"]), styles["CellMuted"])])
    else:
        story.append(Paragraph("No prediction is attached to this application.", styles["Cell"]))
    notes = item["analyst_notes"]
    if notes:
        story.extend([Paragraph("Analyst notes", styles["SectionHeading"]), paragraph(notes.replace("\n", " "), "Cell")])
    story.extend([Spacer(1, 12), Paragraph("Generated by CrediGuard AI. Model outputs are estimates, not causal explanations or lending decisions.", styles["CellMuted"])])
    document.build(story)
    output.seek(0)
    filename = f"creditguard-assessment-{application_id[:8]}.pdf"
    return StreamingResponse(iter([output.getvalue()]), media_type="application/pdf", headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.get("/api/reports/powerbi.csv")
def report_powerbi(user: dict[str, Any] = Depends(_current_user)):
    _staff(user)
    with database.connect() as db:
        rows = db.execute(database.APPLICATION_SELECT + " ORDER BY a.created_at DESC").fetchall()
        database.audit(db, user, "REPORT_EXPORTED", "report", "powerbi_csv", {"records": len(rows)})
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["application_id", "application_date", "loan_amount", "loan_term_months", "annual_income", "purpose", "debt_to_income_ratio", "fico_score", "prior_delinquencies", "probability_of_default", "risk_score", "risk_category", "low_risk_maximum", "medium_risk_maximum", "application_status", "final_decision", "actual_default_status", "model_version"])
    for row in rows:
        item = database.application_public(row)
        data = item["application_data"]
        prediction = item["prediction"] or {}
        risk_thresholds = prediction.get("thresholds") or {}
        writer.writerow([item["id"], item["created_at"], data.get("loan_amount"), data.get("term_months"), data.get("annual_income"), data.get("purpose"), data.get("dti"), data.get("fico_score"), data.get("prior_delinquencies"), prediction.get("probability_of_default"), prediction.get("risk_score"), prediction.get("risk_category"), risk_thresholds.get("low_risk_maximum"), risk_thresholds.get("medium_risk_maximum"), item["status"], item["decision"], "NOT_OBSERVED", prediction.get("model_version")])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=creditguard-powerbi.csv"})
