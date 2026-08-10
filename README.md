# Labor Budget Countdown

A task time-tracker that counts **down** instead of up. Each task carries a fixed
dollar budget, and every worker who clocks in burns that budget at their hourly
rate. Instead of showing *time worked*, the system shows the question managers
actually care about:

> *At the current crew's cost, how much longer can they work before this task's
> labor budget runs out?*

The remaining time recalculates live as workers with different rates clock in and
out, and when the budget is spent the task flips to **Labor Budget Exhausted**
while still recording every additional (over-budget) hour worked.

## How it works

```
combined burn rate = Σ hourly rate of every worker currently clocked in
remaining budget   = labor budget − labor consumed
remaining time     = remaining budget ÷ combined burn rate
```

Consumed cost is not stored as a running total. It is derived on every request by
replaying the session ledger (`Σ hours × rate`). Because the number is always
recomputed from source events:

- it is correct regardless of the order workers clock in and out,
- actual worked hours are never lost, even after the budget reaches zero,
- historical figures stay reproducible (each session snapshots the pay rate used
  at clock-in, so later rate changes never rewrite past cost).

## Tech stack

- **Backend:** Python, FastAPI, SQLite (standard-library `sqlite3`, no ORM)
- **Frontend:** vanilla HTML/CSS/JS, no build step
- **Tests:** plain Python against the running HTTP API

## Getting started

Requires Python 3.9+.

```bash
pip install -r requirements.txt
python -m uvicorn main:app --reload
```

Then open:

- **Admin / management dashboard** — http://127.0.0.1:8000
- **Employee view** — http://127.0.0.1:8000/employee
- **API docs (Swagger)** — http://127.0.0.1:8000/docs

## Features

**Admin / management**
- Create employees and set hourly rates
- Create tasks and set (or later change) the labor budget
- Assign and unassign employees to a task
- Clock workers in and out
- Live dashboard: original budget, consumed, remaining budget, original budgeted
  hours, remaining estimated hours, live countdown, crew on the clock, individual
  rates, combined crew cost, % consumed, % remaining, actual hours worked, labor
  cost by employee, over-budget amount
- Select any task to view that task's stats

**Employee**
- See assigned tasks
- Clock in / clock out
- See a task's countdown when management permits it (per-task toggle, enforced on
  the server)

**Rules enforced**
- A worker can hold only one open session at a time — clocking into a second task
  while clocked in elsewhere is rejected (on both panels).
- A worker who is clocked in cannot be unassigned until they clock out.
- All time records are persisted; worked hours are preserved when the budget hits
  zero.

## Tests

With the server running:

```bash
python test_scenario.py       # 11-step acceptance scenario
python test_requirements.py   # full requirements audit (41 checks)
```

Both simulate elapsed hours by back-dating clock-in timestamps, so they run in
seconds while exercising the real HTTP API and calculation path.

## Project structure

```
main.py               FastAPI app + calculation engine
static/index.html     Admin / management dashboard
static/employee.html  Employee view
test_scenario.py      Acceptance-scenario test
test_requirements.py  Requirements audit
requirements.txt      Dependencies
docs/prior-art.md     Survey of existing tools and why this was built
```

## Notes

- SQLite is used for zero-setup local running; the data layer is small and can be
  pointed at Postgres for multi-user deployment.
- Roles are presented as two separate pages rather than an auth system; adding
  login and per-role access is a natural next step.
