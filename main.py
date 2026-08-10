"""
Labor Budget Countdown System — FastAPI backend.

Core idea: a task has a fixed DOLLAR budget. Every clocked-in worker burns
money at their hourly rate. The system shows how much *time* is left at the
current crew's combined burn rate, counting DOWN to zero instead of up.

Consumed labor cost is never stored as a running total — it is DERIVED by
replaying the session ledger. That means actual worked hours can never be lost
and every historical calculation is reproducible, even after the budget hits 0.
"""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

DB_PATH = Path(__file__).parent / "labor.db"
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Labor Budget Countdown System")


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS employees (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                hourly_rate REAL    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                name           TEXT    NOT NULL,
                labor_budget   REAL    NOT NULL,
                -- whether management lets employees see the live countdown
                show_countdown INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS assignments (
                task_id     INTEGER NOT NULL REFERENCES tasks(id),
                employee_id INTEGER NOT NULL REFERENCES employees(id),
                PRIMARY KEY (task_id, employee_id)
            );

            -- The session ledger. One row per clock-in. clock_out is NULL while
            -- the worker is still on the clock. rate_snapshot freezes the pay
            -- rate at clock-in time so changing a rate later cannot rewrite
            -- historical cost.
            CREATE TABLE IF NOT EXISTS sessions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id       INTEGER NOT NULL REFERENCES tasks(id),
                employee_id   INTEGER NOT NULL REFERENCES employees(id),
                rate_snapshot REAL    NOT NULL,
                clock_in      REAL    NOT NULL,
                clock_out     REAL
            );
            """
        )
        # Lightweight migration for DBs created before show_countdown existed.
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(tasks)")]
        if "show_countdown" not in cols:
            conn.execute(
                "ALTER TABLE tasks ADD COLUMN show_countdown INTEGER NOT NULL DEFAULT 1"
            )


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    name: str = Field(..., min_length=1)
    hourly_rate: float = Field(..., gt=0)


class TaskIn(BaseModel):
    name: str = Field(..., min_length=1)
    labor_budget: float = Field(..., gt=0)
    show_countdown: bool = True


class BudgetPatch(BaseModel):
    labor_budget: float | None = Field(None, gt=0)
    show_countdown: bool | None = None


class AssignIn(BaseModel):
    employee_id: int


class ClockIn(BaseModel):
    task_id: int
    employee_id: int


# --------------------------------------------------------------------------- #
# Core calculation
# --------------------------------------------------------------------------- #
def compute_task_state(conn, task_id: int) -> dict:
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if task is None:
        raise HTTPException(404, "Task not found")

    now = time.time()
    budget = task["labor_budget"]

    sessions = conn.execute(
        "SELECT * FROM sessions WHERE task_id=? ORDER BY clock_in", (task_id,)
    ).fetchall()

    consumed = 0.0
    per_employee = {}          # employee_id -> {cost, seconds}
    burn_rate = 0.0            # $/hour of currently-open sessions
    crew = []                  # employees currently clocked in

    for s in sessions:
        end = s["clock_out"] if s["clock_out"] is not None else now
        seconds = max(0.0, end - s["clock_in"])
        hours = seconds / 3600.0
        cost = hours * s["rate_snapshot"]
        consumed += cost

        agg = per_employee.setdefault(
            s["employee_id"], {"cost": 0.0, "seconds": 0.0}
        )
        agg["cost"] += cost
        agg["seconds"] += seconds

        if s["clock_out"] is None:                       # still on the clock
            burn_rate += s["rate_snapshot"]
            emp = conn.execute(
                "SELECT * FROM employees WHERE id=?", (s["employee_id"],)
            ).fetchone()
            crew.append(
                {
                    "employee_id": s["employee_id"],
                    "name": emp["name"],
                    "hourly_rate": s["rate_snapshot"],
                    "clock_in": s["clock_in"],
                }
            )

    remaining_budget = budget - consumed
    over_budget = max(0.0, -remaining_budget)
    show_countdown = bool(task["show_countdown"])

    # Remaining time = remaining budget / combined burn rate.
    # If nobody is clocked in, the clock is frozen (infinite time left).
    if burn_rate > 0:
        remaining_seconds = (remaining_budget / burn_rate) * 3600.0
        original_seconds = (budget / burn_rate) * 3600.0
    else:
        remaining_seconds = None
        original_seconds = None

    exhausted = remaining_budget <= 0

    # Enrich per-employee labor breakdown with names/rates.
    labor_by_employee = []
    for emp_id, agg in per_employee.items():
        emp = conn.execute(
            "SELECT * FROM employees WHERE id=?", (emp_id,)
        ).fetchone()
        labor_by_employee.append(
            {
                "employee_id": emp_id,
                "name": emp["name"],
                "current_rate": emp["hourly_rate"],
                "cost": round(agg["cost"], 2),
                "hours_worked": round(agg["seconds"] / 3600.0, 4),
            }
        )

    return {
        "server_now": now,
        "task_id": task_id,
        "task_name": task["name"],
        "show_countdown": show_countdown,
        "labor_budget": round(budget, 2),
        "consumed": round(consumed, 2),
        "remaining_budget": round(remaining_budget, 2),
        "burn_rate": round(burn_rate, 2),
        "remaining_seconds": remaining_seconds,
        "original_seconds": original_seconds,
        "exhausted": exhausted,
        "over_budget": round(over_budget, 2),
        "pct_consumed": round(min(100.0, consumed / budget * 100), 1) if budget else 0,
        "pct_remaining": round(max(0.0, remaining_budget / budget * 100), 1) if budget else 0,
        "crew": crew,
        "crew_size": len(crew),
        "labor_by_employee": labor_by_employee,
    }


# --------------------------------------------------------------------------- #
# Employee endpoints
# --------------------------------------------------------------------------- #
@app.post("/api/employees")
def create_employee(body: EmployeeIn):
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO employees(name, hourly_rate) VALUES(?,?)",
            (body.name, body.hourly_rate),
        )
        return {"id": cur.lastrowid, "name": body.name, "hourly_rate": body.hourly_rate}


@app.get("/api/employees")
def list_employees():
    with db() as conn:
        rows = conn.execute("SELECT * FROM employees ORDER BY id").fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Task endpoints
# --------------------------------------------------------------------------- #
@app.post("/api/tasks")
def create_task(body: TaskIn):
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO tasks(name, labor_budget, show_countdown) VALUES(?,?,?)",
            (body.name, body.labor_budget, int(body.show_countdown)),
        )
        return {
            "id": cur.lastrowid,
            "name": body.name,
            "labor_budget": body.labor_budget,
            "show_countdown": body.show_countdown,
        }


@app.get("/api/tasks")
def list_tasks():
    with db() as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
        return [dict(r) for r in rows]


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, body: BudgetPatch):
    with db() as conn:
        r = conn.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone()
        if r is None:
            raise HTTPException(404, "Task not found")
        if body.labor_budget is not None:
            conn.execute(
                "UPDATE tasks SET labor_budget=? WHERE id=?", (body.labor_budget, task_id)
            )
        if body.show_countdown is not None:
            conn.execute(
                "UPDATE tasks SET show_countdown=? WHERE id=?",
                (int(body.show_countdown), task_id),
            )
        return compute_task_state(conn, task_id)


@app.post("/api/tasks/{task_id}/assign")
def assign(task_id: int, body: AssignIn):
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO assignments(task_id, employee_id) VALUES(?,?)",
            (task_id, body.employee_id),
        )
        return {"ok": True}


@app.post("/api/tasks/{task_id}/unassign")
def unassign(task_id: int, body: AssignIn):
    """Remove an employee from a task. Blocked while they are clocked in so we
    never orphan an open session; their past sessions (and consumed cost) are
    kept intact."""
    with db() as conn:
        open_session = conn.execute(
            "SELECT id FROM sessions WHERE task_id=? AND employee_id=? AND clock_out IS NULL",
            (task_id, body.employee_id),
        ).fetchone()
        if open_session:
            raise HTTPException(409, "Clock the employee out before unassigning them")
        conn.execute(
            "DELETE FROM assignments WHERE task_id=? AND employee_id=?",
            (task_id, body.employee_id),
        )
        return {"ok": True}


@app.get("/api/tasks/{task_id}/assignments")
def list_assignments(task_id: int):
    with db() as conn:
        rows = conn.execute(
            """SELECT e.* FROM assignments a
               JOIN employees e ON e.id = a.employee_id
               WHERE a.task_id=? ORDER BY e.id""",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]


@app.get("/api/tasks/{task_id}/state")
def task_state(task_id: int):
    with db() as conn:
        return compute_task_state(conn, task_id)


@app.get("/api/active-sessions")
def active_sessions():
    """Every currently-open session: which employee is clocked in on which task.
    Used by the admin UI to block clocking a worker into a second task."""
    with db() as conn:
        rows = conn.execute(
            """SELECT s.employee_id, s.task_id, t.name AS task_name FROM sessions s
               JOIN tasks t ON t.id = s.task_id
               WHERE s.clock_out IS NULL"""
        ).fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Employee module — what a single worker sees
# --------------------------------------------------------------------------- #
@app.get("/api/employees/{employee_id}/tasks")
def employee_tasks(employee_id: int):
    """Tasks assigned to this employee, with their clock status and — only if
    management permits it — the live countdown for each task."""
    with db() as conn:
        emp = conn.execute(
            "SELECT * FROM employees WHERE id=?", (employee_id,)
        ).fetchone()
        if emp is None:
            raise HTTPException(404, "Employee not found")

        # Which task (if any) this employee is currently clocked in on.
        active = conn.execute(
            """SELECT s.task_id, t.name FROM sessions s
               JOIN tasks t ON t.id = s.task_id
               WHERE s.employee_id=? AND s.clock_out IS NULL""",
            (employee_id,),
        ).fetchone()

        rows = conn.execute(
            """SELECT t.id FROM assignments a
               JOIN tasks t ON t.id = a.task_id
               WHERE a.employee_id=? ORDER BY t.id""",
            (employee_id,),
        ).fetchall()

        out = []
        for row in rows:
            state = compute_task_state(conn, row["id"])
            clocked_in = any(c["employee_id"] == employee_id for c in state["crew"])
            item = {
                "task_id": state["task_id"],
                "task_name": state["task_name"],
                "clocked_in": clocked_in,
                "show_countdown": state["show_countdown"],
            }
            # Only expose countdown/budget figures if management allows it.
            if state["show_countdown"]:
                item.update(
                    {
                        "server_now": state["server_now"],
                        "remaining_budget": state["remaining_budget"],
                        "burn_rate": state["burn_rate"],
                        "remaining_seconds": state["remaining_seconds"],
                        "exhausted": state["exhausted"],
                        "crew_size": state["crew_size"],
                    }
                )
            out.append(item)
        return {
            "employee": dict(emp),
            "active_task_id": active["task_id"] if active else None,
            "active_task_name": active["name"] if active else None,
            "tasks": out,
        }


# --------------------------------------------------------------------------- #
# Clock in / out
# --------------------------------------------------------------------------- #
@app.post("/api/clock-in")
def clock_in(body: ClockIn):
    with db() as conn:
        emp = conn.execute(
            "SELECT * FROM employees WHERE id=?", (body.employee_id,)
        ).fetchone()
        if emp is None:
            raise HTTPException(404, "Employee not found")
        if conn.execute("SELECT id FROM tasks WHERE id=?", (body.task_id,)).fetchone() is None:
            raise HTTPException(404, "Task not found")

        # An employee may have at most ONE open session across ALL tasks. This
        # is enforced here so it holds no matter which panel (admin or employee)
        # initiated the clock-in.
        open_session = conn.execute(
            """SELECT s.task_id, t.name FROM sessions s
               JOIN tasks t ON t.id = s.task_id
               WHERE s.employee_id=? AND s.clock_out IS NULL""",
            (body.employee_id,),
        ).fetchone()
        if open_session:
            if open_session["task_id"] == body.task_id:
                raise HTTPException(409, "Employee is already clocked in on this task")
            raise HTTPException(
                409,
                f"Employee is already clocked in on '{open_session['name']}' — "
                "clock out there first",
            )

        # Auto-assign on first clock-in for convenience.
        conn.execute(
            "INSERT OR IGNORE INTO assignments(task_id, employee_id) VALUES(?,?)",
            (body.task_id, body.employee_id),
        )
        conn.execute(
            """INSERT INTO sessions(task_id, employee_id, rate_snapshot, clock_in)
               VALUES(?,?,?,?)""",
            (body.task_id, body.employee_id, emp["hourly_rate"], time.time()),
        )
        return compute_task_state(conn, body.task_id)


@app.post("/api/clock-out")
def clock_out(body: ClockIn):
    with db() as conn:
        session = conn.execute(
            "SELECT id FROM sessions WHERE task_id=? AND employee_id=? AND clock_out IS NULL",
            (body.task_id, body.employee_id),
        ).fetchone()
        if session is None:
            raise HTTPException(409, "Employee is not clocked in on this task")
        conn.execute(
            "UPDATE sessions SET clock_out=? WHERE id=?", (time.time(), session["id"])
        )
        return compute_task_state(conn, body.task_id)


# --------------------------------------------------------------------------- #
# Static frontend
# --------------------------------------------------------------------------- #
@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/employee")
def employee_page():
    return FileResponse(STATIC_DIR / "employee.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

init_db()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
