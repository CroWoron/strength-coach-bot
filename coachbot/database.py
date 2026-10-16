from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


def exercise_key(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


class Database:
    def __init__(self, path: Path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def init(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(platform, external_id)
                );
                CREATE TABLE IF NOT EXISTS profiles (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    data_json TEXT NOT NULL DEFAULT '{}',
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS baselines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    exercise_key TEXT NOT NULL,
                    exercise_name TEXT NOT NULL,
                    weight_kg REAL NOT NULL,
                    reps INTEGER NOT NULL,
                    rir REAL NOT NULL,
                    rir_known INTEGER NOT NULL DEFAULT 1,
                    bodyweight_kg REAL,
                    source TEXT NOT NULL,
                    observed_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS baselines_lookup
                    ON baselines(user_id, exercise_key, observed_at DESC);
                CREATE TABLE IF NOT EXISTS programs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('proposed', 'active', 'retired')),
                    title TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    created_at INTEGER NOT NULL,
                    activated_at INTEGER,
                    UNIQUE(user_id, version)
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    program_id INTEGER REFERENCES programs(id),
                    status TEXT NOT NULL CHECK(status IN ('in_progress', 'paused', 'completed', 'aborted')),
                    session_type TEXT NOT NULL,
                    readiness_json TEXT NOT NULL DEFAULT '{}',
                    plan_json TEXT NOT NULL DEFAULT '{}',
                    started_at INTEGER NOT NULL,
                    completed_at INTEGER,
                    duration_minutes INTEGER,
                    session_rpe REAL,
                    notes TEXT NOT NULL DEFAULT ''
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_open_session
                    ON sessions(user_id) WHERE status IN ('in_progress', 'paused');
                CREATE TABLE IF NOT EXISTS sets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    exercise_key TEXT NOT NULL,
                    exercise_name TEXT NOT NULL,
                    set_number INTEGER NOT NULL,
                    is_warmup INTEGER NOT NULL DEFAULT 0,
                    planned_weight_kg REAL,
                    planned_reps INTEGER,
                    target_rir REAL,
                    planned_rest_sec INTEGER,
                    actual_weight_kg REAL,
                    actual_reps INTEGER,
                    actual_rir REAL,
                    bodyweight_kg REAL,
                    quality_ok INTEGER,
                    technique_ok INTEGER,
                    pain INTEGER,
                    pain_location TEXT,
                    created_at INTEGER NOT NULL,
                    completed_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS sets_session ON sets(session_id, id);
                CREATE TABLE IF NOT EXISTS recommendations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
                    kind TEXT NOT NULL,
                    proposal_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    external_event_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    text TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(platform, external_event_id, role)
                );
                CREATE INDEX IF NOT EXISTS messages_recent ON messages(user_id, id DESC);
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            session_columns = {row["name"] for row in connection.execute("PRAGMA table_info(sessions)")}
            if "duration_minutes" not in session_columns:
                connection.execute("ALTER TABLE sessions ADD COLUMN duration_minutes INTEGER")
            set_columns = {row["name"] for row in connection.execute("PRAGMA table_info(sets)")}
            if "bodyweight_kg" not in set_columns:
                connection.execute("ALTER TABLE sets ADD COLUMN bodyweight_kg REAL")
            if "quality_ok" not in set_columns:
                connection.execute("ALTER TABLE sets ADD COLUMN quality_ok INTEGER")
            baseline_columns = {row["name"] for row in connection.execute("PRAGMA table_info(baselines)")}
            if "rir_known" not in baseline_columns:
                connection.execute("ALTER TABLE baselines ADD COLUMN rir_known INTEGER NOT NULL DEFAULT 1")

    def get_or_create_user(self, platform: str, external_id: str, display_name: str) -> int:
        now = int(self.clock())
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO users(platform, external_id, display_name, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(platform, external_id) DO UPDATE SET
                       display_name=excluded.display_name, updated_at=excluded.updated_at""",
                (platform, str(external_id), display_name[:200], now, now),
            )
            row = connection.execute(
                "SELECT id FROM users WHERE platform=? AND external_id=?", (platform, str(external_id))
            ).fetchone()
            return int(row["id"])

    def remember_message(self, platform: str, event_id: str, user_id: int, role: str, text: str) -> bool:
        with self.connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO messages(platform, external_event_id, user_id, role, text, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (platform, str(event_id), user_id, role, text[:12000], int(self.clock())),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def recent_messages(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT role, text, created_at FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def get_profile(self, user_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT data_json FROM profiles WHERE user_id=?", (user_id,)).fetchone()
        return json.loads(row["data_json"]) if row else {}

    def update_profile(self, user_id: int, values: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "age", "height_cm", "bodyweight_kg", "priority", "boxing_schedule", "gym_days",
            "equipment", "constraints", "injury_notes", "timezone", "smallest_weight_increment_kg",
            "experience", "goals",
        }
        clean = {key: value for key, value in values.items() if key in allowed}
        profile = self.get_profile(user_id)
        profile.update(clean)
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO profiles(user_id, data_json, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET data_json=excluded.data_json, updated_at=excluded.updated_at""",
                (user_id, json.dumps(profile, ensure_ascii=False), int(self.clock())),
            )
        return profile

    def save_baseline(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        exercise = str(data["exercise"]).strip()
        record = {
            "exercise": exercise,
            "weight_kg": float(data["weight_kg"]),
            "reps": int(data["reps"]),
            "rir": None if data.get("rir") is None else float(data["rir"]),
            "bodyweight_kg": data.get("bodyweight_kg"),
            "source": str(data.get("source") or "user_reported"),
        }
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO baselines(user_id, exercise_key, exercise_name, weight_kg, reps, rir,
                   rir_known, bodyweight_kg, source, observed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id, exercise_key(exercise), exercise, record["weight_kg"], record["reps"],
                    record["rir"] or 0, int(record["rir"] is not None), record["bodyweight_kg"],
                    record["source"], int(self.clock()),
                ),
            )
        return record

    def baseline(self, user_id: int, exercise: str) -> dict[str, Any] | None:
        key = exercise_key(exercise)
        with self.connect() as connection:
            actual = connection.execute(
                """SELECT s.exercise_name AS exercise, s.actual_weight_kg AS weight_kg,
                          s.actual_reps AS reps, s.actual_rir AS rir, s.bodyweight_kg,
                          s.completed_at AS observed_at
                   FROM sets s JOIN sessions w ON w.id=s.session_id
                   WHERE w.user_id=? AND s.exercise_key=? AND s.actual_weight_kg IS NOT NULL
                     AND s.technique_ok=1 AND COALESCE(s.pain, 0)=0 AND s.actual_reps BETWEEN 1 AND 12
                     AND s.actual_rir BETWEEN 0 AND 5
                   ORDER BY s.completed_at DESC LIMIT 1""",
                (user_id, key),
            ).fetchone()
            row = actual or connection.execute(
                """SELECT exercise_name AS exercise, weight_kg, reps,
                          CASE WHEN rir_known=1 THEN rir ELSE NULL END AS rir,
                          bodyweight_kg, observed_at
                   FROM baselines WHERE user_id=? AND exercise_key=? ORDER BY observed_at DESC LIMIT 1""",
                (user_id, key),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        if "bodyweight_kg" not in result:
            result["bodyweight_kg"] = None
        return result

    def list_baselines(self, user_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT exercise_name AS exercise, weight_kg, reps,
                          CASE WHEN rir_known=1 THEN rir ELSE NULL END AS rir,
                          bodyweight_kg, source, observed_at
                   FROM baselines WHERE user_id=? ORDER BY observed_at DESC LIMIT 30""",
                (user_id,),
            ).fetchall()
        unique: dict[str, dict[str, Any]] = {}
        for row in rows:
            unique.setdefault(exercise_key(row["exercise"]), dict(row))
        return list(unique.values())

    def propose_program(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as connection:
            version = int(connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS value FROM programs WHERE user_id=?", (user_id,)
            ).fetchone()["value"])
            cursor = connection.execute(
                """INSERT INTO programs(user_id, version, status, title, content_json, rationale,
                   evidence_json, created_at) VALUES (?, ?, 'proposed', ?, ?, ?, ?, ?)""",
                (
                    user_id, version, str(data.get("title") or f"Program {version}")[:200],
                    json.dumps(data.get("days") or [], ensure_ascii=False), str(data.get("rationale") or "")[:4000],
                    json.dumps(data.get("evidence_ids") or [], ensure_ascii=False), int(self.clock()),
                ),
            )
            return {"program_id": cursor.lastrowid, "version": version, "status": "proposed"}

    def activate_program(self, user_id: int, program_id: int) -> dict[str, Any]:
        now = int(self.clock())
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, version, title FROM programs WHERE id=? AND user_id=? AND status='proposed'",
                (program_id, user_id),
            ).fetchone()
            if not row:
                raise ValueError("Proposed program not found")
            connection.execute("UPDATE programs SET status='retired' WHERE user_id=? AND status='active'", (user_id,))
            connection.execute(
                "UPDATE programs SET status='active', activated_at=? WHERE id=?", (now, program_id)
            )
        return {**dict(row), "status": "active"}

    def program(self, user_id: int, status: str = "active") -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM programs WHERE user_id=? AND status=? ORDER BY version DESC LIMIT 1",
                (user_id, status),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["days"] = json.loads(item.pop("content_json"))
        item["evidence_ids"] = json.loads(item.pop("evidence_json"))
        return item

    def active_session(self, user_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE user_id=? AND status IN ('in_progress', 'paused') ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            if not row:
                return None
            sets = connection.execute("SELECT * FROM sets WHERE session_id=? ORDER BY id", (row["id"],)).fetchall()
        item = dict(row)
        item["readiness"] = json.loads(item.pop("readiness_json"))
        item["plan"] = json.loads(item.pop("plan_json"))
        item["sets"] = [dict(value) for value in sets]
        return item

    def start_session(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        existing = self.active_session(user_id)
        if existing:
            raise ValueError("An unfinished workout already exists; resume or abort it before starting another")
        program = self.program(user_id)
        session_type = str(data.get("session_type") or "strength")
        if not program and session_type != "assessment":
            raise ValueError("An active program is required before starting a workout")
        now = int(self.clock())
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO sessions(user_id, program_id, status, session_type, readiness_json, started_at)
                   VALUES (?, ?, 'in_progress', ?, ?, ?)""",
                (
                    user_id, program["id"] if program else None, session_type[:50],
                    json.dumps(data.get("readiness") or {}, ensure_ascii=False), now,
                ),
            )
        return self.active_session(user_id) or {"id": cursor.lastrowid}

    def save_session_plan(
        self, user_id: int, plan: dict[str, Any], *, replace: bool = False
    ) -> dict[str, Any]:
        session = self.active_session(user_id)
        if not session:
            raise ValueError("No active workout")
        current = session.get("plan") or {}
        if current and current != plan and not replace:
            raise ValueError("The active workout already has a plan; use an explicit exercise adjustment")
        if current == plan:
            return current
        with self.connect() as connection:
            connection.execute(
                "UPDATE sessions SET plan_json=? WHERE id=?",
                (json.dumps(plan, ensure_ascii=False), session["id"]),
            )
        return plan

    def plan_set(self, user_id: int, plan: dict[str, Any]) -> dict[str, Any]:
        session = self.active_session(user_id)
        if not session:
            raise ValueError("No active workout")
        key = exercise_key(plan["exercise"])
        with self.connect() as connection:
            pending = connection.execute(
                """SELECT id, set_number FROM sets WHERE session_id=? AND exercise_key=?
                   AND actual_weight_kg IS NULL ORDER BY id DESC LIMIT 1""",
                (session["id"], key),
            ).fetchone()
            if pending:
                connection.execute(
                    """UPDATE sets SET exercise_name=?, is_warmup=?, planned_weight_kg=?, planned_reps=?,
                       target_rir=?, planned_rest_sec=? WHERE id=?""",
                    (
                        plan["exercise"], int(plan["is_warmup"]), plan["weight_kg"], plan["reps"],
                        plan["target_rir"], plan["rest_sec"], pending["id"],
                    ),
                )
                set_id, set_number = int(pending["id"]), int(pending["set_number"])
            else:
                set_number = int(connection.execute(
                    "SELECT COUNT(*) + 1 AS value FROM sets WHERE session_id=? AND exercise_key=?",
                    (session["id"], key),
                ).fetchone()["value"])
                cursor = connection.execute(
                    """INSERT INTO sets(session_id, exercise_key, exercise_name, set_number, is_warmup,
                       planned_weight_kg, planned_reps, target_rir, planned_rest_sec, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session["id"], key, plan["exercise"], set_number, int(plan["is_warmup"]),
                        plan["weight_kg"], plan["reps"], plan["target_rir"], plan["rest_sec"], int(self.clock()),
                    ),
                )
                set_id = int(cursor.lastrowid)
        return {"set_id": set_id, "set_number": set_number, **plan}

    def record_set(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        session = self.active_session(user_id)
        if not session:
            raise ValueError("No active workout")
        key = exercise_key(str(data["exercise"]))
        now = int(self.clock())
        bodyweight = data.get("bodyweight_kg")
        if bodyweight is None:
            bodyweight = self.get_profile(user_id).get("bodyweight_kg")
        with self.connect() as connection:
            pending = connection.execute(
                """SELECT * FROM sets WHERE session_id=? AND exercise_key=? AND actual_weight_kg IS NULL
                   ORDER BY id DESC LIMIT 1""",
                (session["id"], key),
            ).fetchone()
            if pending:
                connection.execute(
                    """UPDATE sets SET actual_weight_kg=?, actual_reps=?, actual_rir=?, bodyweight_kg=?,
                       quality_ok=?, technique_ok=?, pain=?, pain_location=?, completed_at=? WHERE id=?""",
                    (
                        float(data["weight_kg"]), int(data["reps"]),
                        None if data.get("rir") is None else float(data["rir"]),
                        bodyweight, None if data.get("quality_ok") is None else int(bool(data["quality_ok"])),
                        int(bool(data.get("technique_ok", True))), int(bool(data.get("pain", False))),
                        str(data.get("pain_location") or "")[:200], now, pending["id"],
                    ),
                )
                set_id = int(pending["id"])
            else:
                set_number = int(connection.execute(
                    "SELECT COUNT(*) + 1 AS value FROM sets WHERE session_id=? AND exercise_key=?",
                    (session["id"], key),
                ).fetchone()["value"])
                cursor = connection.execute(
                    """INSERT INTO sets(session_id, exercise_key, exercise_name, set_number, is_warmup,
                       actual_weight_kg, actual_reps, actual_rir, bodyweight_kg, quality_ok, technique_ok, pain,
                       pain_location, created_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session["id"], key, str(data["exercise"]), set_number, int(bool(data.get("is_warmup", False))),
                        float(data["weight_kg"]), int(data["reps"]),
                        None if data.get("rir") is None else float(data["rir"]),
                        bodyweight, None if data.get("quality_ok") is None else int(bool(data["quality_ok"])),
                        int(bool(data.get("technique_ok", True))), int(bool(data.get("pain", False))),
                        str(data.get("pain_location") or "")[:200], now, now,
                    ),
                )
                set_id = int(cursor.lastrowid)
            row = connection.execute("SELECT * FROM sets WHERE id=?", (set_id,)).fetchone()
        return dict(row)

    def complete_session(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        session = self.active_session(user_id)
        if not session:
            raise ValueError("No active workout")
        status = "aborted" if data.get("aborted") else "completed"
        with self.connect() as connection:
            connection.execute(
                "UPDATE sessions SET status=?, completed_at=?, duration_minutes=?, session_rpe=?, notes=? WHERE id=?",
                (
                    status, int(self.clock()), data.get("duration_minutes"), data.get("session_rpe"),
                    str(data.get("notes") or "")[:4000],
                    session["id"],
                ),
            )
        return {"session_id": session["id"], "status": status}

    def log_activity(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        now = int(self.clock())
        with self.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO sessions(user_id, status, session_type, readiness_json, started_at, completed_at,
                   duration_minutes, session_rpe, notes) VALUES (?, 'completed', ?, '{}', ?, ?, ?, ?, ?)""",
                (
                    user_id, str(data.get("activity_type") or "boxing")[:50],
                    int(data.get("started_at") or now), now, int(data["duration_minutes"]),
                    float(data["session_rpe"]), str(data.get("notes") or "")[:4000],
                ),
            )
        return {"session_id": int(cursor.lastrowid), "status": "completed"}

    def recent_sessions(self, user_id: int, limit: int = 8) -> list[dict[str, Any]]:
        with self.connect() as connection:
            sessions = connection.execute(
                "SELECT * FROM sessions WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)
            ).fetchall()
            result = []
            for session in sessions:
                item = dict(session)
                item["readiness"] = json.loads(item.pop("readiness_json"))
                item["plan"] = json.loads(item.pop("plan_json"))
                item["sets"] = [dict(row) for row in connection.execute(
                    "SELECT * FROM sets WHERE session_id=? ORDER BY id", (session["id"],)
                ).fetchall()]
                result.append(item)
        return result

    def audit(self, user_id: int, kind: str, proposal: dict[str, Any], result: dict[str, Any], evidence: list[str]) -> None:
        session = self.active_session(user_id)
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO recommendations(user_id, session_id, kind, proposal_json, result_json,
                   evidence_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id, session["id"] if session else None, kind,
                    json.dumps(proposal, ensure_ascii=False), json.dumps(result, ensure_ascii=False),
                    json.dumps(evidence, ensure_ascii=False), int(self.clock()),
                ),
            )

    def metadata(self, key: str, default: str = "") -> str:
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_metadata(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )
