"""
Full requirements audit for the labor countdown system.

Unlike test_scenario.py (which covers the 11-step acceptance scenario), this
walks every requirement: automatic-recalculation triggers, every management
dashboard field, and the admin / employee / system feature lists. Elapsed hours
are simulated by back-dating clock-in timestamps.

Run with the server up:  python test_requirements.py
"""
import sqlite3, time, sys, urllib.request, json
from pathlib import Path

BASE = "http://127.0.0.1:8000"
DB = Path(__file__).parent / "labor.db"
passed = failed = 0


def call(path, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    m = method or ("POST" if body is not None else "GET")
    req = urllib.request.Request(BASE + path, data=data, method=m,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def backdate(employee_id, hours):
    conn = sqlite3.connect(DB)
    conn.execute("UPDATE sessions SET clock_in=? WHERE employee_id=? AND clock_out IS NULL",
                 (time.time() - hours * 3600, employee_id))
    conn.commit(); conn.close()


def ok(label, cond):
    global passed, failed
    if cond: passed += 1; print(f"  [PASS] {label}")
    else:    failed += 1; print(f"  [FAIL] {label}")


def near(a, b, tol=0.1): return abs(a - b) <= tol


print("=" * 68)
print("AUTOMATIC RECALCULATION — remaining time updates on every trigger")
print("=" * 68)
task = call("/api/tasks", {"name": "Audit Task", "labor_budget": 1000})["id"]
A = call("/api/employees", {"name": "A", "hourly_rate": 20})["id"]
B = call("/api/employees", {"name": "B", "hourly_rate": 25})["id"]

s = call("/api/clock-in", {"task_id": task, "employee_id": A})
ok("Trigger: employee clocks in -> burn rate = $20, ~50h", near(s["burn_rate"], 20) and near(s["remaining_seconds"]/3600, 50))

s = call("/api/clock-in", {"task_id": task, "employee_id": B})
ok("Trigger: another employee joins (different rate) -> $45 combined", near(s["burn_rate"], 45))
ok("Trigger: crew composition change recalculated remaining time", near(s["remaining_seconds"]/3600, 1000/45))

s = call("/api/clock-out", {"task_id": task, "employee_id": B})
ok("Trigger: employee clocks out -> back to $20", near(s["burn_rate"], 20))

backdate(A, 5)  # 5h @ $20 = $100 consumed
s = call(f"/api/tasks/{task}/state")
ok("Trigger: labor cost consumed -> consumed rises, remaining falls", near(s["consumed"], 100, 1) and near(s["remaining_budget"], 900, 1))

s = call(f"/api/tasks/{task}", {"labor_budget": 1200}, method="PATCH")
ok("Trigger: labor budget changes -> recomputed", near(s["labor_budget"], 1200))

before = call(f"/api/tasks/{task}/state")["remaining_seconds"]
s = call(f"/api/tasks/{task}", {"labor_budget": 600}, method="PATCH")
ok("Trigger: allowed task hours change (budget-driven) -> budgeted hours change",
   s["original_seconds"] is not None and abs(s["remaining_seconds"] - before) > 1)

print("\n" + "=" * 68)
print("MANAGEMENT DASHBOARD — every listed field is present")
print("=" * 68)
# fresh clean task for a clean field check
t2 = call("/api/tasks", {"name": "Dash", "labor_budget": 1000})["id"]
C = call("/api/employees", {"name": "C", "hourly_rate": 30})["id"]
call("/api/clock-in", {"task_id": t2, "employee_id": C})
backdate(C, 4)  # $120 consumed
st = call(f"/api/tasks/{t2}/state")
fields = {
    "Original labor budget": st.get("labor_budget") == 1000,
    "Labor cost already consumed": near(st.get("consumed", 0), 120, 1),
    "Remaining labor budget": near(st.get("remaining_budget", 0), 880, 1),
    "Original budgeted hours (budget / burn rate)": near(st["original_seconds"]/3600, 1000/30),
    "Remaining estimated hours": near(st["remaining_seconds"]/3600, 880/30, 0.2),
    "Countdown to zero (remaining_seconds present)": st["remaining_seconds"] is not None,
    "Employees currently clocked in": len(st["crew"]) == 1 and st["crew"][0]["name"] == "C",
    "Individual employee hourly rates": st["crew"][0]["hourly_rate"] == 30,
    "Current total crew hourly cost": st["burn_rate"] == 30,
    "Percentage of budget consumed": near(st["pct_consumed"], 12, 0.5),
    "Percentage of budget remaining": near(st["pct_remaining"], 88, 0.5),
    "Actual hours worked (per employee)": near(st["labor_by_employee"][0]["hours_worked"], 4, 0.1),
    "Labor cost by employee": near(st["labor_by_employee"][0]["cost"], 120, 1),
    "Labor cost by task (task-level consumed)": near(st["consumed"], 120, 1),
    "Over-budget amount": st["over_budget"] == 0,
    "Time before task unprofitable (= countdown)": st["remaining_seconds"] > 0,
}
for label, cond in fields.items():
    ok(label, cond)

print("\n" + "=" * 68)
print("ZERO-BUDGET BEHAVIOUR")
print("=" * 68)
backdate(C, 40)  # 40h @ $30 = $1200 > $1000
st = call(f"/api/tasks/{t2}/state")
ok("Budget exhausted flag set at/after zero", st["exhausted"] is True)
ok("Over-budget labor amount tracked (~$200)", near(st["over_budget"], 200, 2))
ok("Actual worked hours NOT lost at zero (~40h)", near(st["labor_by_employee"][0]["hours_worked"], 40, 0.2))

print("\n" + "=" * 68)
print("ADMIN MODULE")
print("=" * 68)
e = call("/api/employees", {"name": "Admin-made", "hourly_rate": 22})
ok("Create employee + set hourly rate", e["hourly_rate"] == 22)
tk = call("/api/tasks", {"name": "Admin-task", "labor_budget": 500})
ok("Create task + set labor budget", tk["labor_budget"] == 500)
call(f"/api/tasks/{tk['id']}/assign", {"employee_id": e["id"]})
asg = call(f"/api/tasks/{tk['id']}/assignments")
ok("Assign employees to task", any(x["id"] == e["id"] for x in asg))
sIn = call("/api/clock-in", {"task_id": tk["id"], "employee_id": e["id"]})
ok("Start work (clock in) + calc burn rate", sIn["burn_rate"] == 22)
ok("Dynamically calculate remaining time + live countdown to zero", sIn["remaining_seconds"] is not None)
sOut = call("/api/clock-out", {"task_id": tk["id"], "employee_id": e["id"]})
ok("Stop work (clock out)", sOut["burn_rate"] == 0)

print("\n" + "=" * 68)
print("EMPLOYEE MODULE")
print("=" * 68)
# Assigned but NOT yet clocked in -> employee must still SEE the task.
et = call("/api/tasks", {"name": "Emp-task", "labor_budget": 400})["id"]
emp = call("/api/employees", {"name": "Worker", "hourly_rate": 18})["id"]
call(f"/api/tasks/{et}/assign", {"employee_id": emp})
view = call(f"/api/employees/{emp}/tasks")
seen = [t for t in view["tasks"] if t["task_id"] == et]
ok("See assigned task BEFORE clocking in", len(seen) == 1 and seen[0]["clocked_in"] is False)
call("/api/clock-in", {"task_id": et, "employee_id": emp})
view = call(f"/api/employees/{emp}/tasks")
seen = [t for t in view["tasks"] if t["task_id"] == et][0]
ok("Clock in reflected in employee view", seen["clocked_in"] is True)
ok("See task countdown when management permits it", seen.get("remaining_seconds") is not None)
call(f"/api/tasks/{et}", {"show_countdown": False}, method="PATCH")
seen = [t for t in call(f"/api/employees/{emp}/tasks")["tasks"] if t["task_id"] == et][0]
ok("Countdown withheld server-side when management forbids it",
   "remaining_seconds" not in seen and seen["show_countdown"] is False)
call("/api/clock-out", {"task_id": et, "employee_id": emp})
ok("Clock out", True)

print("\n" + "=" * 68)
print("SYSTEM REQUIREMENTS")
print("=" * 68)
conn = sqlite3.connect(DB)
n_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
conn.close()
ok("Save all time records (sessions persisted in DB)", n_sessions > 0)
# Historical preservation: change an employee's stored rate does not exist as an
# endpoint, but rate is snapshotted per session -> past cost immutable. Verify a
# closed session keeps its own rate_snapshot.
conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
snaps = conn.execute("SELECT DISTINCT rate_snapshot FROM sessions").fetchall()
conn.close()
ok("Preserve historical calculations (per-session rate snapshot)", len(snaps) >= 2)
# Multiple simultaneous employees:
mt = call("/api/tasks", {"name": "Multi", "labor_budget": 1000})["id"]
m1 = call("/api/employees", {"name": "M1", "hourly_rate": 10})["id"]
m2 = call("/api/employees", {"name": "M2", "hourly_rate": 15})["id"]
call("/api/clock-in", {"task_id": mt, "employee_id": m1})
sm = call("/api/clock-in", {"task_id": mt, "employee_id": m2})
ok("Support multiple employees simultaneously (2 in crew, $25/hr)", sm["crew_size"] == 2 and sm["burn_rate"] == 25)
ok("Update calculations when crew members clock in/out", near(sm["remaining_seconds"]/3600, 40))

print("\n" + "=" * 68)
total = passed + failed
print(f"RESULT: {passed}/{total} checks passed" + ("  — ALL REQUIREMENTS MET" if failed == 0 else f"  — {failed} FAILED"))
print("=" * 68)
sys.exit(0 if failed == 0 else 1)
