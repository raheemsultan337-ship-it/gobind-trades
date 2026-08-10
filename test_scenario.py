"""
End-to-end test of the 11-step acceptance scenario for the labor countdown.

Elapsed real time is simulated by back-dating session clock_in timestamps in
the SQLite ledger, then reading the LIVE state through the HTTP API (the same
code path the dashboard uses). Consumed cost is derived, never stored, so
back-dating is a faithful simulation of hours actually passing.
"""
import sqlite3, time, sys, urllib.request, json
from pathlib import Path

BASE = "http://127.0.0.1:8000"
DB = Path(__file__).parent / "labor.db"


def call(path, body=None, method="POST"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method if body else "GET",
                                 headers={"Content-Type": "application/json"})
    if body is not None:
        req.method = method
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def backdate(employee_id, hours):
    """Pretend an open session started `hours` ago -> simulates elapsed work."""
    conn = sqlite3.connect(DB)
    conn.execute(
        "UPDATE sessions SET clock_in=? WHERE employee_id=? AND clock_out IS NULL",
        (time.time() - hours * 3600, employee_id),
    )
    conn.commit(); conn.close()


def approx(a, b, tol=0.05):
    return abs(a - b) <= tol


ok = True
def check(label, got, want, tol=0.05):
    global ok
    good = approx(got, want, tol)
    ok = ok and good
    print(f"  [{'PASS' if good else 'FAIL'}] {label}: got {got:.2f}, expected ~{want:.2f}")


print("STEP 1 · Create task with $1,000 budget")
task = call("/api/tasks", {"name": "Frame Unit 101", "labor_budget": 1000})
tid = task["id"]

print("STEP 2 · Worker A = $20/hour")
A = call("/api/employees", {"name": "Worker A", "hourly_rate": 20})

print("STEP 3 · Worker A clocks in")
st = call("/api/clock-in", {"task_id": tid, "employee_id": A["id"]})

print("STEP 4 · System shows ~50 hours remaining")
check("burn rate", st["burn_rate"], 20)
check("remaining hours", st["remaining_seconds"] / 3600, 50, tol=0.1)

print("STEP 5 · Worker A consumes $200 of budget (10h @ $20 simulated)")
backdate(A["id"], 10)
st = call(f"/api/tasks/{tid}/state", method="GET")
check("consumed", st["consumed"], 200, tol=1)
check("remaining budget", st["remaining_budget"], 800, tol=1)
check("remaining hours @ $20", st["remaining_seconds"] / 3600, 40, tol=0.1)

print("STEP 6 & 7 · Worker B = $25/hour clocks in -> recalc at $45/hr")
B = call("/api/employees", {"name": "Worker B", "hourly_rate": 25})
st = call("/api/clock-in", {"task_id": tid, "employee_id": B["id"]})
check("combined burn rate", st["burn_rate"], 45)
check("remaining budget still ~800", st["remaining_budget"], 800, tol=1)
check("remaining hours = 800/45 = 17.78", st["remaining_seconds"] / 3600, 17.78, tol=0.1)
h = int(st["remaining_seconds"] // 3600); m = int((st["remaining_seconds"] % 3600) // 60)
print(f"         -> live display: {h}h {m:02d}m remaining (expected ~17h 47m)")

print("STEP 8 & 9 · Worker B clocks out -> recalc back to $20/hr")
st = call("/api/clock-out", {"task_id": tid, "employee_id": B["id"]})
check("burn rate back to $20", st["burn_rate"], 20)
check("remaining hours back to ~40", st["remaining_seconds"] / 3600, 40, tol=0.5)

print("STEP 10 · Countdown reaches zero (simulate A working 45 more hours)")
backdate(A["id"], 55)  # 55h @ $20 = $1100 total consumed
st = call(f"/api/tasks/{tid}/state", method="GET")
check("consumed exceeds budget", st["consumed"], 1100, tol=2)
print(f"         -> exhausted flag: {st['exhausted']}  (expected True)")
ok = ok and st["exhausted"] is True

print("STEP 11 · Over-budget labor recorded, actual hours preserved")
check("over-budget amount", st["over_budget"], 100, tol=2)
worked = st["labor_by_employee"][0]["hours_worked"]
check("actual hours never lost", worked, 55, tol=0.1)

print("\n" + ("ALL 11 STEPS PASSED" if ok else "SOME CHECKS FAILED"))
sys.exit(0 if ok else 1)
