# Phase 2 — Cutover Runbook: `upande_irrigation` → kaitet-group.upande.com

**Date:** 2026-09-13
**Status:** Ready to execute. Not yet run.
**Target:** `kaitet-group.upande.com` — Frappe 16.32.0 / ERPNext 16.33.0, 38 apps, **459 enabled users, live production**
**Source:** `kaitet-group.c.frappe.cloud` (v15) — **read-only throughout**
**Proven on:** `kaitet.local`, Frappe 16.27.0, 176 tests green

This procedure was derived from a full dry-run, not written from the design. Every
step below either ran on the bench or exists because the bench proved it was needed.

---

## What you are deploying

The app converts nine database-owned `custom: 1` DocTypes into code DocTypes, adopts
`Irrigation Pump Profile` out of `upande_kaitet` (not installed on the destination),
replaces the retired `Block Type` with an `Irrigation Shift Block` child table on the
`Irrigation Scheduler` Single, and carries ~7,500 records across.

**Destination prerequisites — all confirmed present:**

| Dependency | Status on upande.com |
|---|---|
| `Farm` | 14 records, module Upande Core |
| `Warehouse` | 601 records |
| `User` | 459 enabled |
| `upande_agriculture` | installed |
| `Irrigation`/`Block Type` DocTypes | **absent — clean install, no collision** |
| stale `Report module='Agriculture'` rows | **absent** (they break migrate on the bench; verified not present here) |

---

## Before you start

### 1. Take a database backup

Non-negotiable. Steps 5–7 write ~7,500 records and mutate two Singles.

```bash
bench --site kaitet-group.upande.com backup
```

If the full backup fails on a large audit table (it did on the bench — a 10.4 GB
`tabVersion`), scope it:

```bash
bench --site kaitet-group.upande.com backup \
  --exclude "Version,Error Log,Access Log,Deleted Document,Data Import Log,Activity Log,Route History,Scheduled Job Log,Email Queue"
```

Note `--exclude` takes **DocType names**, not table names.

### 2. Pick a low-traffic window

The bench showed a Sales Invoice modified mid-afternoon; this is an actively used ERP.
Step 6's load is the long part.

### 3. NEVER run the test suite against production

Several tests mutate live data and commit: `test_shift_loader.py` and `test_shifts.py`
replace `Irrigation Scheduler.shift_blocks` (restoring in teardown — but a kill in
between leaves test rows in place of the real 86), and `test_adopt_patch.py` calls the
real patch `execute()`, which **deletes every Custom Field** on the nine managed
DocTypes. Run tests on a staging copy only.

---

## Step 1 — Deploy the code

Get `main` of `upande_irrigation` onto the destination bench (commit `ce67dbb` or later).

```bash
bench get-app upande_irrigation <your-repo-url>   # or update an existing checkout
bench --site kaitet-group.upande.com install-app upande_irrigation
```

**Know what `install-app` does with patches.** `frappe/installer.py` calls
`set_all_patches_as_completed()`, which writes a Patch Log row for every entry in
`patches.txt` **without executing it**. That is fine here — all three migration patches
are no-ops on a clean site, and the older patches' Custom Fields are now folded into the
code DocType JSONs. But it means *anything that exists on the bench only because a patch
once created it does not exist on the destination.* The two known cases are handled:

- the DocType schemas are code now, not patch-created;
- the Roles the DocPerms name are created by `after_install`, which **does** run.

### Verify before continuing

```bash
bench --site kaitet-group.upande.com mariadb -e "
  SELECT name, custom, istable FROM \`tabDocType\` WHERE module='Upande Irrigation' ORDER BY name;
  SELECT name FROM \`tabRole\` WHERE name IN ('Irrigation User','Agriculture Manager','Agriculture User');"
```

Expect 11 DocTypes all `custom = 0` (including `Irrigation Shift Block` with
`istable = 1` and `Irrigation Pump Profile`), and the three Roles present.

---

## Step 2 — Migrate

```bash
bench --site kaitet-group.upande.com migrate
```

Must exit cleanly. The bench's `migrate` fails at the very end in
`remove_orphan_entities()` with `Module Agriculture not found` — two stale `Report`
rows in a different app. **I verified the destination has no such rows**, so this
should not occur. If it does, the fix is not this app's:

```sql
UPDATE `tabReport` SET module='Upande Agriculture' WHERE module='Agriculture';
```

---

## Step 3 — Export from the source

**The repo does not contain the data.** `migration/data/*.json` is gitignored
(deliberately — it is production data), with `shift_mapping.json` the one tracked
exception. You must re-export at cutover time, and the counts **will differ** from the
dry-run's because the source keeps accruing records. That is expected: the loader
derives its expectations from the files, not from hardcoded numbers.

Export from `kaitet-group.c.frappe.cloud` (read-only) into
`apps/upande_irrigation/upande_irrigation/migration/data/`:

| File | Dry-run count | Notes |
|---|---|---|
| `tank_and_valve.json` | 58 | |
| `weather_reading.json` | 5,431 | pages at 1000/call |
| `irrigation_planner.json` | 1,346 | |
| `reservoir_pumping_record.json` | 369 | |
| `water_transfer.json` | 279 | |
| `irrigation_scheduler_run.json` | 32 | fetch each individually so `shift_results` children come too |
| `water_meter_reading.json` | 82 | added 2026-09-27; source module was Upande Kaitet |
| `electricity_meter_reading.json` | 310 | added 2026-09-27; source module was Upande Kaitet |
| `irrigation_settings.json` | Single | |
| `irrigation_scheduler.json` | Single | |

Every record needs `name`, `creation`, `owner`, `modified`, `modified_by` plus all data
fields. Page ordered by `creation asc` so pages neither overlap nor gap.

`shift_mapping.json` is already in the repo — all 89 source rows, verbatim.

---

## Step 4 — Load the shift mapping

```bash
bench --site kaitet-group.upande.com execute \
  upande_irrigation.migration.shift_loader.load_from_file --kwargs '{"strict": False}'
```

`--kwargs` is evaluated as Python, so it is `False`, not JSON `false`.

**Expect 86 loaded, 3 excluded.** Three rows name `23HA_SECTION - KL`, which is a
*group* warehouse rather than a block; the child table's validation rejects them by
design, and the loader records each exclusion with its reason. `23HA - SHIFT 1` has no
other rows, so **that shift will not exist** — and the planners referencing it become
orphans. This is reported, not repaired, by explicit decision.

The result also carries a `notes` key — non-blocking observations: three shift pairs
with identical block sets (70HA 1≡2, 10≡11, 19≡20), 13 blocks appearing in two shifts,
and block warehouses in no shift at all. **Keep this output**; it is the input to the
Phase 3 data-cleanup decisions.

Verify: 86 rows across 56 distinct shifts.

---

## Step 5 — Load the records

```bash
bench --site kaitet-group.upande.com execute upande_irrigation.migration.pull.load_all
```

This runs in dependency order, preserves `name`/`creation`/`owner`/`modified`,
suppresses business hooks, loads both Singles, and reseeds naming counters.

**Four things it does that you must know about:**

1. **It loads the Singles.** `Irrigation Settings` and `Irrigation Scheduler` are
   overwritten from the source — including `default_irrigation_coverage` (source 70 vs
   the code default 62.5; the difference moves every planner's `required_hours` by ~12%)
   and `plan_for` ("Next Week"). `shift_blocks` is explicitly preserved, so Step 4's
   rows survive.

2. **It arms the scheduler.** The exported `Irrigation Scheduler` carries
   `enabled: 1`, `auto_run_enabled: 1`, `run_day_of_week: "Friday"`, `run_hour: 6`.
   **Loading the data turns the weekly cron on.** If you do not want it running
   immediately, disable it after the load and re-enable deliberately.

3. **It reseeds naming counters** for `IRPL-<year>-` and `IRSR-<year>-` to the highest
   migrated suffix. Without this the destination's counters start at 1 and collide —
   the first scheduler run fails outright, and planner creation fails once it reaches
   the lowest migrated name. The shared `''` counter is **deliberately never touched**:
   137 DocTypes on a site share it, and Reservoir Pumping Record names embed the month
   so they cannot collide anyway.

4. **It refuses short rows.** `assert_complete()` fails loudly *before* inserting if
   any record is missing a `reqd` field. If a field is `reqd` on the destination via a
   Custom Field or Property Setter that the source lacks, the load will refuse — add
   that fieldname to `pull.NULLABLE_LEGACY` with a written reason.

Every doctype must report `ok: true` — meaning `already_present + inserted` accounts
for every row in the file.

---

## Step 6 — Verify

```bash
bench --site kaitet-group.upande.com mariadb -e "
  SELECT 'weather' k, COUNT(*) n FROM \`tabWeather Reading\`
  UNION SELECT 'planner', COUNT(*) FROM \`tabIrrigation Planner\`
  UNION SELECT 'reservoir', COUNT(*) FROM \`tabReservoir Pumping Record\`
  UNION SELECT 'transfer', COUNT(*) FROM \`tabWater Transfer\`
  UNION SELECT 'tankvalve', COUNT(*) FROM \`tabTank And Valve\`
  UNION SELECT 'schedrun', COUNT(*) FROM \`tabIrrigation Scheduler Run\`
  UNION SELECT 'shiftblock', COUNT(*) FROM \`tabIrrigation Shift Block\`;

  SELECT name, current FROM tabSeries WHERE name LIKE 'IRPL-%' OR name LIKE 'IRSR-%';

  SELECT field, value FROM tabSingles
   WHERE doctype='Irrigation Settings' AND field='default_irrigation_coverage';

  SELECT p.block, COUNT(*) orphans FROM \`tabIrrigation Planner\` p
    LEFT JOIN (SELECT DISTINCT shift FROM \`tabIrrigation Shift Block\`) s ON s.shift=p.block
   WHERE s.shift IS NULL GROUP BY p.block;"
```

Check, in order of importance:

- counts match the export files;
- `tabSeries` counters are **at or above** the highest migrated suffix;
- `default_irrigation_coverage` is the **source** value (70), not 62.5;
- the only orphaned shift is `23HA - SHIFT 1`.

Then exercise the endpoints:

```bash
bench --site kaitet-group.upande.com execute upande_irrigation.api.overview.fetch --kwargs '{"farm": "Lokitela"}'
bench --site kaitet-group.upande.com execute upande_irrigation.api.valves.list_states --kwargs '{"farm": "Lokitela"}'
```

`valves.list_states` is the one to watch: it was the defect that survived the bench only
because a deleted DocType left its table behind. On a clean site it either works or
proves the repoint was wrong.

---

## Step 7 — Post-cutover, and what is NOT proven

### Assign the roles

`after_install` **creates** `Irrigation User`, `Agriculture Manager` and
`Agriculture User` — it does not assign them to anyone. Until you grant them, only
System Managers can reach most irrigation DocTypes. Decide who gets what and assign.

### Verify planner creation — this is the real gap

**The dry-run never exercised planner creation end to end.** The bench's newest Weather
Reading is 51 days old, so the scheduler correctly declined to plan, and we only proved
the *refusal* path. All three Critical defects found in the final review lived in the
unexercised creation path — that coverage gap is precisely why they survived nine tasks.

So after cutover, once fresh weather data exists, run one scheduler pass deliberately
and confirm it creates planners with sane names and hours:

```bash
bench --site kaitet-group.upande.com execute upande_irrigation.scheduled.weekly.run_weekly_scheduler
```

Expect `status` Success/Partial and `planners_failed = 0`. **Check the new planner names
continue the migrated series** rather than restarting — that is the counter reseed
proving itself on real data for the first time.

### The weather view will look empty

`weather.fetch` returns nothing for a 30-day window because the newest reading is ~51
days old. Someone will report this as a migration failure. It is data freshness.

### Phase 3 — data decisions, for a human who knows the farm

Carried across deliberately unrepaired:

- `23HA - SHIFT 1` no longer exists; its planners are orphaned. What should it have pointed at?
- Three shift pairs are identical (70HA 1≡2, 10≡11, 19≡20) — duplicates, or deliberate?
- 13 blocks belong to two shifts each; `DAIRY BLK 7` crosses from a 56HA shift into `65HA - SHIFT 7`.
- Four block warehouses are in no shift: `BLOCK BLK 1`, `BLOCK BLK 2`, `BLOCK BLK 8`, `DAIRY BLK 1`.
- Two shifts are inactive: `65HA - SHIFT 2`, `70HA - SHIFT 4`.

---

## Rollback

Nothing here is irreversible except the record load, and that is what the Step 1 backup
is for.

| Problem | Action |
|---|---|
| Install or migrate fails | `bench --site … uninstall-app upande_irrigation`, investigate, retry |
| Load fails partway | Re-run `load_all` — `insert_preserving` skips existing names, so it resumes rather than duplicating |
| Data wrong after load | Restore the Step 1 backup |
| Counters wrong | `NamingSeries(prefix).update_counter(n)` — it only ever advances, never rewinds |

The three retired DocTypes (`Block Type`, `Block configaration`, `Blocks List`) never
existed on the destination, so nothing there is dropped by this migration.

---

## Provenance

Phase 1 dry-run: 12 tasks, 28 commits, 176 tests. Fifteen defects were found and fixed,
every one of them in the plan rather than the implementation. The three that would have
broken production — unmigrated naming counters, a `Farm` filter on a field nothing
ships, and two Singles that were never exported — were all invisible on the bench
because it inherited the relevant state from its own v15 history.

Full detail: `docs/superpowers/specs/2026-09-13-upande-irrigation-v16-migration-design.md`
and `docs/superpowers/plans/2026-09-13-v16-migration-phase1-bench.md`.
