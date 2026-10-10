"""SQLite persistence for accounts, applications, predictions, notifications, and audit events."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.security import hash_password


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("APP_DATA_DIR", ROOT / "instance")).expanduser().resolve()
DATABASE_PATH = DATA_DIR / "credit-risk.sqlite3"
MODEL_DIR = DATA_DIR / "models"
DATASET_PATH = ROOT / "data" / "raw" / "accepted_2007_to_2018Q4.csv"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_database() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('APPLICANT','ANALYST','ADMIN')),
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                country_code TEXT NOT NULL DEFAULT 'US',
                currency_code TEXT NOT NULL DEFAULT 'USD',
                locale_code TEXT
            );

            CREATE TABLE IF NOT EXISTS applications (
                id TEXT PRIMARY KEY,
                applicant_id TEXT NOT NULL REFERENCES users(id),
                status TEXT NOT NULL,
                applicant_name TEXT NOT NULL,
                application_data TEXT NOT NULL,
                latest_prediction_id TEXT,
                analyst_id TEXT REFERENCES users(id),
                analyst_notes TEXT NOT NULL DEFAULT '',
                decision TEXT,
                decision_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status_updated_at TEXT,
                country_code TEXT NOT NULL DEFAULT 'US',
                currency_code TEXT NOT NULL DEFAULT 'USD'
            );

            CREATE TABLE IF NOT EXISTS predictions (
                id TEXT PRIMARY KEY,
                application_id TEXT NOT NULL REFERENCES applications(id),
                model_version TEXT NOT NULL,
                probability_of_default REAL NOT NULL,
                risk_score REAL NOT NULL,
                risk_category TEXT NOT NULL,
                thresholds TEXT NOT NULL,
                factors TEXT NOT NULL,
                range_warnings TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id TEXT REFERENCES users(id),
                actor_email TEXT,
                actor_role TEXT,
                action TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT,
                details TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                recipient_id TEXT NOT NULL REFERENCES users(id),
                application_id TEXT NOT NULL REFERENCES applications(id),
                audit_event_id INTEGER NOT NULL UNIQUE REFERENCES audit_events(id),
                notification_type TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                read_at TEXT
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS model_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT NOT NULL UNIQUE,
                algorithm TEXT NOT NULL,
                trained_at TEXT NOT NULL,
                metadata TEXT NOT NULL,
                metrics TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_apps_created ON applications(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_apps_status ON applications(status);
            CREATE INDEX IF NOT EXISTS idx_apps_applicant ON applications(applicant_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_apps_prediction ON applications(latest_prediction_id);
            CREATE INDEX IF NOT EXISTS idx_predictions_application ON predictions(application_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_events(resource_type, resource_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_notifications_recipient ON notifications(recipient_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications(recipient_id, read_at, created_at DESC);
            """
        )
        db.execute(
            "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES(?,?,?)",
            ("risk_thresholds", json.dumps({"low_risk_maximum": 0.30, "medium_risk_maximum": 0.60}), utc_now()),
        )
        prediction_columns = {row["name"] for row in db.execute("PRAGMA table_info(predictions)").fetchall()}
        if "thresholds" not in prediction_columns:
            db.execute("ALTER TABLE predictions ADD COLUMN thresholds TEXT NOT NULL DEFAULT '{}' ")
        application_columns = {row["name"] for row in db.execute("PRAGMA table_info(applications)").fetchall()}
        if "status_updated_at" not in application_columns:
            db.execute("ALTER TABLE applications ADD COLUMN status_updated_at TEXT")
        for column, definition in (
            ("country_code", "TEXT NOT NULL DEFAULT 'US'"),
            ("currency_code", "TEXT NOT NULL DEFAULT 'USD'"),
        ):
            if column not in application_columns:
                db.execute(f"ALTER TABLE applications ADD COLUMN {column} {definition}")
        db.execute(
            "UPDATE applications SET status_updated_at=COALESCE(decision_at,updated_at,created_at) WHERE status_updated_at IS NULL"
        )
        user_columns = {row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()}
        for column, definition in (
            ("country_code", "TEXT NOT NULL DEFAULT 'US'"),
            ("currency_code", "TEXT NOT NULL DEFAULT 'USD'"),
            ("locale_code", "TEXT"),
        ):
            if column not in user_columns:
                db.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")


def ensure_bootstrap_admin() -> None:
    name = os.getenv("BOOTSTRAP_ADMIN_NAME", "System Administrator").strip() or "System Administrator"
    email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "admin@example.test").strip().lower()
    password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
    with connect() as db:
        admin_exists = db.execute("SELECT 1 FROM users WHERE role='ADMIN' LIMIT 1").fetchone()
        if admin_exists:
            return
        if len(password) < 12:
            raise RuntimeError(
                "Set BOOTSTRAP_ADMIN_PASSWORD to a unique password of at least 12 characters before first start."
            )
        import uuid

        db.execute(
            "INSERT INTO users(id,full_name,email,password_hash,role,active,created_at) VALUES(?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), name, email, hash_password(password), "ADMIN", 1, utc_now()),
        )


def user_public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "full_name": row["full_name"],
        "email": row["email"],
        "role": row["role"],
        "active": bool(row["active"]),
        "created_at": row["created_at"],
        "country_code": row["country_code"],
        "currency_code": row["currency_code"],
        "locale_code": row["locale_code"],
    }


def audit(
    db: sqlite3.Connection,
    actor: dict[str, Any] | None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> int:
    cursor = db.execute(
        """INSERT INTO audit_events(actor_id,actor_email,actor_role,action,resource_type,resource_id,details,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (
            actor.get("id") if actor else None,
            actor.get("email") if actor else None,
            actor.get("role") if actor else None,
            action,
            resource_type,
            resource_id,
            json.dumps(details or {}, separators=(",", ":")),
            utc_now(),
        ),
    )
    return int(cursor.lastrowid)


def create_notification(
    db: sqlite3.Connection,
    recipient_id: str,
    application_id: str,
    audit_event_id: int,
    notification_type: str,
    message: str,
) -> dict[str, Any]:
    notification = {
        "id": str(uuid.uuid4()),
        "recipient_id": recipient_id,
        "application_id": application_id,
        "audit_event_id": audit_event_id,
        "notification_type": notification_type,
        "message": message,
        "created_at": utc_now(),
    }
    db.execute(
        """INSERT INTO notifications(id,recipient_id,application_id,audit_event_id,notification_type,message,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (
            notification["id"],
            notification["recipient_id"],
            notification["application_id"],
            notification["audit_event_id"],
            notification["notification_type"],
            notification["message"],
            notification["created_at"],
        ),
    )
    return notification


def notification_public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "application_id": row["application_id"],
        "notification_type": row["notification_type"],
        "message": row["message"],
        "created_at": row["created_at"],
        "read_at": row["read_at"],
        "is_read": row["read_at"] is not None,
    }


def get_thresholds(db: sqlite3.Connection) -> dict[str, float]:
    row = db.execute("SELECT value FROM settings WHERE key='risk_thresholds'").fetchone()
    return json.loads(row["value"]) if row else {"low_risk_maximum": 0.30, "medium_risk_maximum": 0.60}


def classify_risk(probability: float, thresholds: dict[str, float]) -> str:
    if probability <= thresholds["low_risk_maximum"]:
        return "LOW"
    if probability <= thresholds["medium_risk_maximum"]:
        return "MEDIUM"
    return "HIGH"


def prediction_public(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": row["id"],
        "model_version": row["model_version"],
        "probability_of_default": row["probability_of_default"],
        "risk_score": row["risk_score"],
        "risk_category": row["risk_category"],
        "thresholds": json.loads(row["thresholds"]) if "thresholds" in row.keys() and row["thresholds"] != "{}" else None,
        "factors": json.loads(row["factors"]),
        "range_warnings": json.loads(row["range_warnings"]),
        "created_at": row["created_at"],
    }


def application_public(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    keys = row.keys()
    country_code = row["country_code"] if "country_code" in keys else "US"
    currency_code = row["currency_code"] if "currency_code" in keys else "USD"
    application_data = json.loads(row["application_data"])
    application_data.setdefault("country_code", country_code)
    application_data.setdefault("currency_code", currency_code)
    prediction = None
    if "prediction_id" in keys and row["prediction_id"]:
        prediction = {
            "id": row["prediction_id"],
            "model_version": row["model_version"],
            "probability_of_default": row["probability_of_default"],
            "risk_score": row["risk_score"],
            "risk_category": row["risk_category"],
            "thresholds": json.loads(row["thresholds"]) if "thresholds" in keys and row["thresholds"] != "{}" else None,
            "factors": json.loads(row["factors"] or "[]"),
            "range_warnings": json.loads(row["range_warnings"] or "[]"),
            "created_at": row["prediction_created_at"],
        }
    return {
        "id": row["id"],
        "status": row["status"],
        "applicant_id": row["applicant_id"],
        "applicant_name": row["applicant_name"],
        "applicant_email": row["applicant_email"] if "applicant_email" in keys else None,
        "application_data": application_data,
        "country_code": country_code,
        "currency_code": currency_code,
        "prediction": prediction,
        "analyst_id": row["analyst_id"],
        "analyst_name": row["analyst_name"] if "analyst_name" in keys else None,
        "analyst_notes": row["analyst_notes"],
        "decision": row["decision"],
        "decision_at": row["decision_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "status_updated_at": row["status_updated_at"] if "status_updated_at" in keys else row["updated_at"],
    }


APPLICATION_SELECT = """
SELECT a.*, u.email AS applicant_email, analyst.full_name AS analyst_name,
       p.id AS prediction_id, p.model_version, p.probability_of_default, p.risk_score,
       p.risk_category, p.thresholds, p.factors, p.range_warnings, p.created_at AS prediction_created_at
FROM applications a
JOIN users u ON u.id=a.applicant_id
LEFT JOIN users analyst ON analyst.id=a.analyst_id
LEFT JOIN predictions p ON p.id=a.latest_prediction_id
"""
