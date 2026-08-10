# Prior art & alternatives

Before building, existing tools were reviewed to see whether any already provide a
*dynamic labor-budget countdown* — a task timer that counts **down** based on the
live combined burn rate of whichever crew is clocked in.

**Summary:** no off-the-shelf product does this specific thing. Many tools track
labor cost against a budget and show budget *burn*, but they present it as elapsed
cost or percent used, computed after the fact or on a dashboard refresh — not as a
live countdown of remaining affordable *time* that recalculates the instant a
worker with a different rate clocks in or out. That behaviour is the differentiator
and it is small to build, which is why this was built from scratch.

## Time-tracking with budgets (SaaS)

| Tool | Labor budgets | Live cost | Counts down in time | Recalcs on mixed-rate crew change | Verdict |
|---|---|---|---|---|---|
| Clockify | Yes ($/hrs budget, per-user cost rates) | Near real-time | No — shows used vs. estimate | No | Closest, still wrong direction |
| Toggl Track | Yes | Reports | No | No | Reject |
| Harvest | Yes ($ budget, cost rates, over-budget alerts) | Reports/alerts | No — % of budget used | No | Reject |
| Hubstaff | Yes (time & $ budgets, pay/bill rates) | Yes | Partial — remaining budget as a number | No | Reject |
| Timely / Everhour / TimeCamp | Yes | Reports | No | No | Reject |

These all model estimate vs. actual. They can report "$420 of $1,000 used (42%)"
but none turn *remaining budget ÷ current crew rate* into a live countdown that
jumps when a higher-rate worker joins.

## Construction / field service / workforce management

Procore, Buildertrend, Knowify, Rhumbix, busybusy, ExakTime, Assignar and similar
tools are strong at labor cost vs. budget and productivity reporting (earned-value,
units-per-hour), but are report-oriented, mostly closed and per-seat, and do not
offer a countdown-to-zero-time view. Manufacturing MES labor modules and PM tools
(Jira/Asana time add-ons, ClickUp) share the same gap.

## Open source

| Project | What it is | Gap |
|---|---|---|
| Kimai (PHP/Symfony, self-host, AGPL) | Mature time-tracker with budgets and per-user cost rates | Budgets shown as used vs. total; no countdown, no live combined-burn-rate recalc |
| Traggo / TimeTagger / Kanboard time | Lightweight time trackers | No labor-cost budgeting |
| Agile burndown repos | Story-point burndown charts | Points, not live labor cost; no clock |

No open-source project was found that ships the remaining-budget-÷-live-burn-rate
countdown with over-budget tracking after zero.

## Conclusion

Build. The surrounding pieces (cost rates, budgets, over-budget alerts, % consumed)
are common, but the live self-recalculating countdown is not, and the whole
calculation fits in a single function. If adopting-and-extending were preferred,
Kimai is the only credible base (per-user cost rates, project budgets, auth), but
the countdown layer would still have to be written, and its AGPL license and
PHP/Symfony stack may not fit every environment.
