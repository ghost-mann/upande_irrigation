# Upande Irrigation v16 Migration — Phase 1 (Bench Dry-Run) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the whole v15→v16 migration path on `kaitet.local` — the app's schema converted from database fixtures to real code DocTypes, `Block Type` retired in favour of a child table, and all ~7,500 live records loaded and exercised by the engine — before anything touches production.

**Architecture:** The app currently ships nine `custom: 1` DocTypes as fixtures plus 38 Custom Fields added by patches; the effective schema is `doctype.json` + `custom_field.json` merged. We fold that merge into real code DocType JSONs, use a `pre_model_sync` patch to flip existing rows to `custom: 0` so `bench migrate` adopts the tables in place, replace the orphaned `Block Type`/`Block configaration`/`Blocks List` trio with one `Irrigation Shift Block` child table on the `Irrigation Scheduler` Single, and adopt the empty `Irrigation Pump Profile` as schema-only.

**Tech Stack:** Frappe 16.27.0 (bench) / 16.32.0 (destination), MariaDB, Python 3, `FrappeTestCase`, `bench --site kaitet.local`.

**Spec:** `docs/superpowers/specs/2026-09-13-upande-irrigation-v16-migration-design.md`

## Global Constraints

- Site for all work: **`kaitet.local`**. Never run migration commands against `kaitet-group.upande.com` or `kaitet-group.c.frappe.cloud` during Phase 1.
- Source of live data: **c.frappe.cloud**, read-only, via the `Kaitet_2` MCP connection. Never write to it.
- The destination site is **Frappe 16.32.0**; bench is **16.27.0**. Where behaviour differs, the destination wins.
- **Warehouse is never modified.** It is referenced only as the `block` link target.
- Shift attributes (`farm`, `is_active`, `application_rate_mm_hr`, `irrigation_coverage`) repeat across a shift's block rows; the loader **must** fail if a shift's rows disagree.
- Mapping rows load **verbatim**. Defects are reported, never silently repaired.
- Existing test style: `FrappeTestCase`, **tab indentation**, module docstring naming the bug being locked out.
- Run tests with: `bench --site kaitet.local run-tests --module upande_irrigation.tests.<name>`
- Commit after every task. Never amend a commit from a previous task.

---

### Task 1: Effective-schema merger

The fixture DocType is not the runtime schema — patches add fields as Custom Field rows. Conversion must start from the merge, or `required_hours`, `applied_rate_mm_hr`, `measured_from`/`measured_to` and 20 others are silently dropped.

**Files:**
- Create: `upande_irrigation/migration/__init__.py`
- Create: `upande_irrigation/migration/schema.py`
- Test: `upande_irrigation/tests/test_migration_schema.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `merge_custom_fields(doctype_json: dict, custom_fields: list[dict]) -> dict` — returns a new DocType dict with the custom fields inserted into `fields` honouring `insert_after`, and `custom` forced to `0`. Also `load_fixtures(app_path: str) -> tuple[list[dict], list[dict]]` returning `(doctypes, custom_fields)`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the effective-schema merge.

The bug being locked out: the app's DocType fixture carries 16 fields on
Irrigation Planner while the running site has 39 — the other 23 are Custom Field
rows created by patches. Converting from doctype.json alone would ship a DocType
missing required_hours, applied_rate_mm_hr and the measured-window fields, and the
engine writes all of them.
"""

from frappe.tests.utils import FrappeTestCase

from upande_irrigation.migration.schema import merge_custom_fields


DT = {
	"name": "Irrigation Planner",
	"custom": 1,
	"fields": [
		{"fieldname": "block", "fieldtype": "Link", "options": "Block Type"},
		{"fieldname": "to_date", "fieldtype": "Date"},
	],
}

CF = [
	{"dt": "Irrigation Planner", "fieldname": "required_hours",
	 "fieldtype": "Float", "insert_after": "to_date"},
	{"dt": "Irrigation Planner", "fieldname": "measured_from",
	 "fieldtype": "Date", "insert_after": "required_hours"},
]


class TestMergeCustomFields(FrappeTestCase):
	def test_custom_fields_land_after_their_anchor(self):
		out = merge_custom_fields(DT, CF)
		names = [f["fieldname"] for f in out["fields"]]
		self.assertEqual(names, ["block", "to_date", "required_hours", "measured_from"])

	def test_merge_clears_the_custom_flag(self):
		self.assertEqual(merge_custom_fields(DT, CF)["custom"], 0)

	def test_the_input_doctype_is_not_mutated(self):
		merge_custom_fields(DT, CF)
		self.assertEqual(len(DT["fields"]), 2)

	def test_an_unknown_anchor_appends_at_the_end(self):
		cf = [{"dt": "Irrigation Planner", "fieldname": "orphan",
		       "fieldtype": "Data", "insert_after": "does_not_exist"}]
		names = [f["fieldname"] for f in merge_custom_fields(DT, cf)["fields"]]
		self.assertEqual(names[-1], "orphan")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_migration_schema`
Expected: FAIL — `ModuleNotFoundError: No module named 'upande_irrigation.migration'`

- [ ] **Step 3: Write minimal implementation**

Create `upande_irrigation/migration/__init__.py` as an empty file, then `upande_irrigation/migration/schema.py`:

```python
"""Turn the fixture pair into one effective DocType definition.

doctype.json holds the DocType as it was exported; custom_field.json holds the
fields six patches added afterwards. The running site is the sum of the two, so
that sum is what a code DocType has to contain.
"""

import copy
import json
import os

SKIP = {"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx",
        "dt", "doctype", "insert_after", "module"}


def load_fixtures(app_path):
	"""Read doctype.json and custom_field.json from an app's fixtures directory."""
	base = os.path.join(app_path, "fixtures")
	with open(os.path.join(base, "doctype.json")) as fh:
		doctypes = json.load(fh)
	with open(os.path.join(base, "custom_field.json")) as fh:
		custom_fields = json.load(fh)
	return doctypes, custom_fields


def merge_custom_fields(doctype_json, custom_fields):
	"""A copy of doctype_json with its Custom Fields folded into `fields`.

	Each custom field is placed directly after the field named by insert_after.
	An anchor that does not exist appends at the end rather than dropping the
	field — losing a field silently is the failure mode that matters here.
	"""
	out = copy.deepcopy(doctype_json)
	out["custom"] = 0
	fields = out.setdefault("fields", [])

	mine = [cf for cf in custom_fields if cf.get("dt") == doctype_json.get("name")]
	for cf in mine:
		field = {k: v for k, v in cf.items() if k not in SKIP}
		anchor = cf.get("insert_after")
		at = next((i for i, f in enumerate(fields) if f.get("fieldname") == anchor), None)
		if at is None:
			fields.append(field)
		else:
			fields.insert(at + 1, field)
	return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_migration_schema`
Expected: PASS, 4 tests

- [ ] **Step 5: Commit**

```bash
git add upande_irrigation/migration/ upande_irrigation/tests/test_migration_schema.py
git commit -m "feat(migration): merge fixture Custom Fields into the effective DocType"
```

---

### Task 2: Generate the code DocType folders

**Files:**
- Create: `upande_irrigation/migration/generate.py`
- Create: `upande_irrigation/upande_irrigation/doctype/<9 folders>/` (generated)
- Test: `upande_irrigation/tests/test_migration_generate.py`

**Interfaces:**
- Consumes: `merge_custom_fields`, `load_fixtures` from Task 1.
- Produces: `doctype_folder_name(doctype: str) -> str` (e.g. `"Irrigation Planner"` → `"irrigation_planner"`); `write_doctype(effective: dict, module_path: str) -> str` returning the folder path written.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for code-DocType generation.

The bug being locked out: a generated DocType that still says custom: 1, or that
lands in a folder whose name does not match Frappe's scrub() convention, will not
be picked up by bench migrate — the fixture would keep winning and the conversion
would appear to work while changing nothing.
"""

import json
import os
import tempfile

from frappe.tests.utils import FrappeTestCase

from upande_irrigation.migration.generate import doctype_folder_name, write_doctype


EFFECTIVE = {
	"name": "Irrigation Shift Block",
	"custom": 0,
	"module": "Upande Irrigation",
	"istable": 1,
	"fields": [{"fieldname": "shift", "fieldtype": "Data"}],
}


class TestDoctypeFolderName(FrappeTestCase):
	def test_spaces_become_underscores_and_lowercase(self):
		self.assertEqual(doctype_folder_name("Irrigation Planner"), "irrigation_planner")

	def test_multiple_words(self):
		self.assertEqual(
			doctype_folder_name("Irrigation Scheduler Run Shift"),
			"irrigation_scheduler_run_shift",
		)


class TestWriteDoctype(FrappeTestCase):
	def test_it_writes_json_init_and_controller(self):
		with tempfile.TemporaryDirectory() as tmp:
			path = write_doctype(EFFECTIVE, tmp)
			self.assertTrue(os.path.exists(os.path.join(path, "irrigation_shift_block.json")))
			self.assertTrue(os.path.exists(os.path.join(path, "__init__.py")))
			self.assertTrue(os.path.exists(os.path.join(path, "irrigation_shift_block.py")))

	def test_the_written_json_is_not_custom(self):
		with tempfile.TemporaryDirectory() as tmp:
			path = write_doctype(EFFECTIVE, tmp)
			with open(os.path.join(path, "irrigation_shift_block.json")) as fh:
				self.assertEqual(json.load(fh)["custom"], 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_migration_generate`
Expected: FAIL — `No module named 'upande_irrigation.migration.generate'`

- [ ] **Step 3: Write minimal implementation**

Create `upande_irrigation/migration/generate.py`:

```python
"""Write an effective DocType out as a code DocType folder."""

import json
import os

CONTROLLER = '''"""Controller for {name}."""

from frappe.model.document import Document


class {klass}(Document):
	pass
'''


def doctype_folder_name(doctype):
	"""Frappe's scrub(): lowercase, spaces and hyphens to underscores."""
	return doctype.lower().replace(" ", "_").replace("-", "_")


def write_doctype(effective, module_path):
	"""Write <folder>/{json,__init__.py,controller.py}; returns the folder path."""
	folder = doctype_folder_name(effective["name"])
	path = os.path.join(module_path, folder)
	os.makedirs(path, exist_ok=True)

	with open(os.path.join(path, f"{folder}.json"), "w") as fh:
		json.dump(effective, fh, indent=1, sort_keys=True)
		fh.write("\\n")

	open(os.path.join(path, "__init__.py"), "a").close()

	klass = effective["name"].replace(" ", "").replace("-", "")
	with open(os.path.join(path, f"{folder}.py"), "w") as fh:
		fh.write(CONTROLLER.format(name=effective["name"], klass=klass))

	return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_migration_generate`
Expected: PASS, 4 tests

- [ ] **Step 5: Generate the nine folders**

```bash
cd /home/austin/frappe-v16-bench/apps/upande_irrigation
../../env/bin/python - <<'PY'
import os
from upande_irrigation.migration.schema import load_fixtures, merge_custom_fields
from upande_irrigation.migration.generate import write_doctype

APP = "upande_irrigation"
MODULE = os.path.join(APP, "upande_irrigation", "doctype")
RETIRE = {"Block Type", "Block configaration", "Blocks List"}

doctypes, custom_fields = load_fixtures(APP)
for dt in doctypes:
    if dt["name"] in RETIRE:
        continue
    eff = merge_custom_fields(dt, custom_fields)
    eff["module"] = "Upande Irrigation"
    print("wrote", write_doctype(eff, MODULE), len(eff["fields"]), "fields")
PY
```

Expected: 10 folders written (the 9 custom ones plus `tank_and_valve`, which is regenerated from the same source so the two agree). `Irrigation Planner` should report **43** fields — 20 DocFields (4 of them layout breaks) plus 23 Custom Fields; `Irrigation Settings` **41** (31 + 10).

- [ ] **Step 6: Reconcile the two disagreeing DocTypes**

Two of the ten do not match between code and database, and the generator in Step 5
just overwrote both folders from the fixture. Confirm the fixture won, and that the
result is what the live site actually has:

```bash
../../env/bin/python - <<'PY'
import json
for folder, name in [("irrometer_reading", "Irrometer Reading"),
                     ("tank_and_valve", "Tank And Valve")]:
    d = json.load(open(f"upande_irrigation/upande_irrigation/doctype/{folder}/{folder}.json"))
    print(f"{name:20} istable={d.get('istable', 0)} custom={d.get('custom')} "
          f"fields={len(d['fields'])}")
PY
```

Expected: `Irrometer Reading istable=1`, `Tank And Valve istable=0`, both `custom=0`.

`Irrometer Reading` is a **child table** on the live site (`istable: 1`), even though
the app had it as a standalone doctype folder. The fixture is right and the old
folder was wrong — a standalone DocType folder for a child table produces a
DocType with no parent, which is why nothing could ever list it. If the printed
`istable` is `0`, the generator read the wrong source; stop and fix Task 1 before
continuing.

- [ ] **Step 7: Verify no field was lost**

```bash
../../env/bin/python - <<'PY'
import json, glob, os
from upande_irrigation.migration.schema import load_fixtures
doctypes, cfs = load_fixtures("upande_irrigation")
want = {d["name"]: len(d.get("fields") or []) + sum(1 for c in cfs if c["dt"] == d["name"])
        for d in doctypes}
for p in sorted(glob.glob("upande_irrigation/upande_irrigation/doctype/*/*.json")):
    d = json.load(open(p))
    n = d["name"]
    if n in want:
        got = len(d["fields"])
        print(f"{'OK ' if got == want[n] else 'BAD'} {n:32} {got} (expected {want[n]})")
PY
```

Expected: every line `OK`.

- [ ] **Step 8: Commit**

```bash
git add upande_irrigation/migration/generate.py upande_irrigation/tests/test_migration_generate.py upande_irrigation/upande_irrigation/doctype/
git commit -m "feat(migration): generate code DocTypes from the effective schema"
```

---

### Task 3: `pre_model_sync` patch to adopt existing tables

Without this, `bench migrate` on a site that already holds these as `custom: 1` will not adopt the code definition, and the two can diverge silently.

**Files:**
- Create: `upande_irrigation/patches/v1_0/adopt_custom_doctypes_as_code.py`
- Modify: `upande_irrigation/patches.txt`
- Test: `upande_irrigation/tests/test_adopt_patch.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `execute()`; `MANAGED: tuple[str, ...]` — the nine DocType names the patch flips.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the custom-to-code adoption patch.

The bug being locked out: flipping custom to 0 while the DocType still has its
Custom Field rows would make bench migrate drop columns the fixture-era rows still
use. The patch must delete the Custom Field rows it has already folded into the
code DocType, and it must be safe to run twice.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation.patches.v1_0.adopt_custom_doctypes_as_code import MANAGED, execute


class TestAdoptPatch(FrappeTestCase):
	def test_it_manages_exactly_the_nine_converted_doctypes(self):
		self.assertEqual(len(MANAGED), 9)
		self.assertIn("Irrigation Planner", MANAGED)
		self.assertNotIn("Block Type", MANAGED)
		self.assertNotIn("Tank And Valve", MANAGED)

	def test_every_managed_doctype_ends_up_not_custom(self):
		execute()
		for dt in MANAGED:
			if frappe.db.exists("DocType", dt):
				self.assertEqual(
					frappe.db.get_value("DocType", dt, "custom"), 0, f"{dt} still custom"
				)

	def test_no_custom_fields_remain_on_managed_doctypes(self):
		execute()
		left = frappe.get_all("Custom Field", filters={"dt": ["in", list(MANAGED)]}, pluck="name")
		self.assertEqual(left, [])

	def test_running_twice_is_harmless(self):
		execute()
		execute()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_adopt_patch`
Expected: FAIL — `No module named '...adopt_custom_doctypes_as_code'`

- [ ] **Step 3: Write minimal implementation**

Create `upande_irrigation/patches/v1_0/adopt_custom_doctypes_as_code.py`:

```python
"""Hand the nine fixture DocTypes over to the code definitions.

They were created through the UI as custom: 1, so their schema lived in the
database and bench migrate ignored them. The code DocTypes generated alongside
this patch carry the same fields — the DocType's own plus the Custom Fields six
patches added — so flipping custom to 0 lets migrate sync the code definition onto
the table that is already there. The table and its rows are untouched; only who
owns the definition changes.

The Custom Field rows go too. They are now duplicated inside the code DocType, and
leaving both would have migrate re-adding columns that already exist.

pre_model_sync, because it has to happen before migrate reads the DocTypes.
"""

import frappe

MANAGED = (
	"Irrigation Planner",
	"Irrigation Scheduler",
	"Irrigation Scheduler Run",
	"Irrigation Scheduler Run Shift",
	"Irrigation Settings",
	"Irrometer Reading",
	"Reservoir Pumping Record",
	"Water Transfer",
	"Weather Reading",
)


def execute():
	for dt in MANAGED:
		if not frappe.db.exists("DocType", dt):
			continue
		if frappe.db.get_value("DocType", dt, "custom"):
			frappe.db.set_value("DocType", dt, "custom", 0, update_modified=False)

	stale = frappe.get_all("Custom Field", filters={"dt": ["in", list(MANAGED)]}, pluck="name")
	for name in stale:
		frappe.delete_doc("Custom Field", name, force=True, ignore_permissions=True)

	frappe.clear_cache()
```

- [ ] **Step 4: Register the patch as pre_model_sync**

In `upande_irrigation/patches.txt`, under `[pre_model_sync]` (above the `[post_model_sync]` line), add:

```
upande_irrigation.patches.v1_0.adopt_custom_doctypes_as_code
```

- [ ] **Step 5: Run test to verify it passes**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_adopt_patch`
Expected: PASS, 4 tests

- [ ] **Step 6: Drop the DocType and Custom Field fixture hooks**

In `upande_irrigation/hooks.py`, the `fixtures` list loses its first two entries — the DocTypes and Custom Fields are code now. Keep Property Setter and Custom HTML Block:

```python
fixtures = [
    {"doctype": "Property Setter", "filters": [["module", "=", "Upande Irrigation"]]},
    {
        "doctype": "Custom HTML Block",
        "filters": [["name", "in", ["Smart Irrigation Dashboard"]]],
    },
]
```

- [ ] **Step 7: Migrate and confirm the tables survived**

```bash
cd /home/austin/frappe-v16-bench
bench --site kaitet.local migrate 2>&1 | tail -30
bench --site kaitet.local mariadb -e "
  SELECT name, custom FROM \`tabDocType\` WHERE module='Upande Irrigation' ORDER BY name;
  SELECT COUNT(*) AS planners FROM \`tabIrrigation Planner\`;"
```

Expected: every row `custom = 0`; the planner count matches what it was before the migrate.

- [ ] **Step 8: Commit**

```bash
git add upande_irrigation/patches/ upande_irrigation/patches.txt upande_irrigation/hooks.py upande_irrigation/tests/test_adopt_patch.py
git commit -m "feat(migration): adopt the nine fixture DocTypes as code"
```

---

### Task 4: Adopt `Irrigation Pump Profile`

It lives in `Upande Kaitet`, which is not installed on the destination, and the engine calls it. Zero records, so this is schema only.

**Files:**
- Create: `upande_irrigation/upande_irrigation/doctype/irrigation_pump_profile/` (3 files)
- Test: `upande_irrigation/tests/test_pump_profile.py`

**Interfaces:**
- Consumes: `write_doctype` from Task 2.
- Produces: the `Irrigation Pump Profile` DocType, module `Upande Irrigation`, fields `farm` (Link Farm, reqd), `irrigation_section` (Link Warehouse, reqd), `pump_name` (Data), `pump_flow_rate_m3_per_hr` (Float, default `25.0`), `pump_kwh_per_hr` (Float), `water_target_m3_per_week` (Float, default `3500.0`), `kwh_target_per_week` (Float), `installation_date` (Date), `notes` (Small Text).

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the adopted pump profile.

The bug being locked out: allocate.py, resources.py and irrigation_planner.py all
query Irrigation Pump Profile, which belongs to upande_kaitet — an app the v16
destination does not have. frappe.db.count on a missing DocType raises, so the
whole scheduler run dies rather than falling back to the farm defaults it was
written to fall back to.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

DOCTYPE = "Irrigation Pump Profile"


class TestPumpProfileIsOurs(FrappeTestCase):
	def test_the_doctype_exists(self):
		self.assertTrue(frappe.db.exists("DocType", DOCTYPE))

	def test_it_belongs_to_upande_irrigation(self):
		self.assertEqual(frappe.db.get_value("DocType", DOCTYPE, "module"), "Upande Irrigation")

	def test_it_is_not_custom(self):
		self.assertEqual(frappe.db.get_value("DocType", DOCTYPE, "custom"), 0)

	def test_counting_it_does_not_raise(self):
		self.assertIsInstance(frappe.db.count(DOCTYPE), int)

	def test_the_section_link_points_at_warehouse(self):
		meta = frappe.get_meta(DOCTYPE)
		self.assertEqual(meta.get_field("irrigation_section").options, "Warehouse")

	def test_flow_rate_defaults_to_the_estate_assumption(self):
		meta = frappe.get_meta(DOCTYPE)
		self.assertEqual(float(meta.get_field("pump_flow_rate_m3_per_hr").default), 25.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_pump_profile`
Expected: FAIL — module is `Upande Kaitet`, not `Upande Irrigation`

- [ ] **Step 3: Write the DocType**

Create `upande_irrigation/upande_irrigation/doctype/irrigation_pump_profile/irrigation_pump_profile.json`:

```json
{
 "actions": [],
 "allow_rename": 1,
 "creation": "2026-09-13 00:00:00.000000",
 "custom": 0,
 "doctype": "DocType",
 "editable_grid": 1,
 "engine": "InnoDB",
 "field_order": ["farm", "irrigation_section", "pump_name", "section_break_specs",
  "pump_flow_rate_m3_per_hr", "pump_kwh_per_hr", "section_break_targets",
  "water_target_m3_per_week", "kwh_target_per_week", "section_break_meta",
  "installation_date", "notes"],
 "fields": [
  {"fieldname": "farm", "fieldtype": "Link", "label": "Farm", "options": "Farm", "reqd": 1},
  {"fieldname": "irrigation_section", "fieldtype": "Link", "label": "Section (Warehouse)",
   "options": "Warehouse", "reqd": 1,
   "description": "The section warehouse this pump serves"},
  {"fieldname": "pump_name", "fieldtype": "Data", "label": "Pump Name / ID",
   "description": "Optional friendly name for the pump"},
  {"fieldname": "section_break_specs", "fieldtype": "Section Break", "label": "Pump Specifications"},
  {"fieldname": "pump_flow_rate_m3_per_hr", "fieldtype": "Float",
   "label": "Pump Flow Rate (m³/hr)", "default": "25.0", "precision": "2",
   "description": "How many m³ the pump delivers per hour at full operation"},
  {"fieldname": "pump_kwh_per_hr", "fieldtype": "Float", "label": "Pump Electricity (kWh/hr)",
   "precision": "2", "description": "Electricity draw per hour of operation"},
  {"fieldname": "section_break_targets", "fieldtype": "Section Break", "label": "Weekly Budgets"},
  {"fieldname": "water_target_m3_per_week", "fieldtype": "Float",
   "label": "Water Budget (m³/week)", "default": "3500.0", "precision": "2",
   "description": "Maximum water this section should use per week. Flagged when exceeded."},
  {"fieldname": "kwh_target_per_week", "fieldtype": "Float", "label": "Energy Budget (kWh/week)",
   "precision": "2",
   "description": "Maximum electricity this section should use per week. Flagged when exceeded."},
  {"fieldname": "section_break_meta", "fieldtype": "Section Break", "label": "Metadata"},
  {"fieldname": "installation_date", "fieldtype": "Date", "label": "Installation Date"},
  {"fieldname": "notes", "fieldtype": "Small Text", "label": "Notes"}
 ],
 "index_web_pages_for_search": 1,
 "links": [],
 "modified": "2026-09-13 00:00:00.000000",
 "modified_by": "Administrator",
 "module": "Upande Irrigation",
 "name": "Irrigation Pump Profile",
 "owner": "Administrator",
 "permissions": [
  {"create": 1, "delete": 1, "email": 1, "export": 1, "print": 1, "read": 1,
   "report": 1, "role": "System Manager", "share": 1, "write": 1}
 ],
 "sort_field": "modified",
 "sort_order": "DESC",
 "states": []
}
```

Create `__init__.py` (empty) and `irrigation_pump_profile.py`:

```python
"""Controller for Irrigation Pump Profile."""

from frappe.model.document import Document


class IrrigationPumpProfile(Document):
	pass
```

- [ ] **Step 4: Reassign the existing DocType's module, then migrate**

The site already has it under `Upande Kaitet`; the code definition must take it over rather than collide.

This goes in its **own patch file**, not appended to Task 3's. Frappe records every applied patch in `tabPatch Log` and never runs it twice, so Task 3's patch has already executed by now and anything added to it would silently never run.

Create `upande_irrigation/patches/v1_0/adopt_pump_profile.py`:

```python
"""Move Irrigation Pump Profile into this app.

It was created under Upande Kaitet, an app the v16 destination does not have,
while allocate.py, resources.py and irrigation_planner.py all query it. A missing
DocType raises rather than degrading, so the module moves with the callers.

pre_model_sync, so the reassignment lands before migrate reads the DocTypes and
syncs the code definition onto the existing table.
"""

import frappe

DOCTYPE = "Irrigation Pump Profile"


def execute():
	if not frappe.db.exists("DocType", DOCTYPE):
		return

	frappe.db.set_value("DocType", DOCTYPE, "module", "Upande Irrigation", update_modified=False)
	frappe.db.set_value("DocType", DOCTYPE, "custom", 0, update_modified=False)
	frappe.clear_cache()
```

Register it under `[pre_model_sync]` in `upande_irrigation/patches.txt`, on the line after Task 3's patch:

```
upande_irrigation.patches.v1_0.adopt_pump_profile
```

Then:

```bash
cd /home/austin/frappe-v16-bench && bench --site kaitet.local migrate 2>&1 | tail -20
```

- [ ] **Step 5: Run test to verify it passes**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_pump_profile`
Expected: PASS, 6 tests

- [ ] **Step 6: Commit**

```bash
git add upande_irrigation/upande_irrigation/doctype/irrigation_pump_profile/ upande_irrigation/patches/v1_0/adopt_custom_doctypes_as_code.py upande_irrigation/tests/test_pump_profile.py
git commit -m "feat(migration): adopt Irrigation Pump Profile from upande_kaitet"
```

---

### Task 5: The `Irrigation Shift Block` child table

**Files:**
- Create: `upande_irrigation/upande_irrigation/doctype/irrigation_shift_block/` (3 files)
- Modify: `upande_irrigation/upande_irrigation/doctype/irrigation_scheduler/irrigation_scheduler.json`
- Test: `upande_irrigation/tests/test_shift_block.py`

**Interfaces:**
- Consumes: nothing.
- Produces: child DocType `Irrigation Shift Block` (`istable: 1`) with fields `shift` (Data, reqd), `block` (Link Warehouse, reqd), `farm` (Link Farm), `is_active` (Check, default `1`), `application_rate_mm_hr` (Float), `irrigation_coverage` (Float). Attached to `Irrigation Scheduler` as fieldname **`shift_blocks`**.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the shift/block child table.

Two bugs being locked out: a row pointing at a group warehouse — which is what the
three bad 23HA rows on the live site are, they name 23HA_SECTION rather than a
block — and losing the per-shift rate and coverage overrides, which shift_settings()
reads and which commit 22fc836 added precisely because every shift was otherwise
computing identical hours.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

DOCTYPE = "Irrigation Shift Block"


class TestShiftBlockSchema(FrappeTestCase):
	def test_it_is_a_child_table(self):
		self.assertEqual(frappe.db.get_value("DocType", DOCTYPE, "istable"), 1)

	def test_block_links_to_warehouse(self):
		self.assertEqual(frappe.get_meta(DOCTYPE).get_field("block").options, "Warehouse")

	def test_it_carries_the_shift_overrides(self):
		meta = frappe.get_meta(DOCTYPE)
		for fieldname in ("application_rate_mm_hr", "irrigation_coverage"):
			self.assertIsNotNone(meta.get_field(fieldname), f"{fieldname} missing")

	def test_the_scheduler_single_holds_the_table(self):
		field = frappe.get_meta("Irrigation Scheduler").get_field("shift_blocks")
		self.assertIsNotNone(field, "Irrigation Scheduler has no shift_blocks table")
		self.assertEqual(field.options, DOCTYPE)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_shift_block`
Expected: FAIL — `DocType Irrigation Shift Block not found`

- [ ] **Step 3: Write the child DocType**

Create `upande_irrigation/upande_irrigation/doctype/irrigation_shift_block/irrigation_shift_block.json`:

```json
{
 "actions": [],
 "creation": "2026-09-13 00:00:00.000000",
 "custom": 0,
 "doctype": "DocType",
 "editable_grid": 1,
 "engine": "InnoDB",
 "field_order": ["shift", "block", "farm", "is_active",
  "application_rate_mm_hr", "irrigation_coverage"],
 "fields": [
  {"fieldname": "shift", "fieldtype": "Data", "label": "Shift", "reqd": 1,
   "in_list_view": 1, "columns": 2,
   "description": "Shift name as it appears on Irrigation Planner, e.g. 70HA - SHIFT 20"},
  {"fieldname": "block", "fieldtype": "Link", "label": "Block", "options": "Warehouse",
   "reqd": 1, "in_list_view": 1, "columns": 3,
   "description": "A leaf block warehouse. Group warehouses are rejected."},
  {"fieldname": "farm", "fieldtype": "Link", "label": "Farm", "options": "Farm",
   "in_list_view": 1, "columns": 2},
  {"fieldname": "is_active", "fieldtype": "Check", "label": "Active", "default": "1",
   "in_list_view": 1, "columns": 1},
  {"fieldname": "application_rate_mm_hr", "fieldtype": "Float",
   "label": "Application Rate (mm/hr)", "precision": "2",
   "description": "Blank or zero inherits the farm default."},
  {"fieldname": "irrigation_coverage", "fieldtype": "Float",
   "label": "Irrigation Coverage (%)", "precision": "2",
   "description": "Blank or zero inherits the farm default."}
 ],
 "index_web_pages_for_search": 1,
 "istable": 1,
 "links": [],
 "modified": "2026-09-13 00:00:00.000000",
 "modified_by": "Administrator",
 "module": "Upande Irrigation",
 "name": "Irrigation Shift Block",
 "owner": "Administrator",
 "permissions": [],
 "sort_field": "modified",
 "sort_order": "DESC",
 "states": []
}
```

Create `__init__.py` (empty) and `irrigation_shift_block.py`:

```python
"""Controller for Irrigation Shift Block."""

import frappe
from frappe.model.document import Document


class IrrigationShiftBlock(Document):
	def validate(self):
		"""A shift waters blocks, never a whole section.

		The live v15 data has three rows naming 23HA_SECTION - KL, which is a group
		warehouse holding twelve blocks. Letting that through would have the engine
		plan one shift for an entire section.
		"""
		if self.block and frappe.db.get_value("Warehouse", self.block, "is_group"):
			frappe.throw(
				f"{self.block} is a group warehouse. A shift block must be a leaf warehouse."
			)
```

- [ ] **Step 4: Attach the table to `Irrigation Scheduler`**

In `upande_irrigation/upande_irrigation/doctype/irrigation_scheduler/irrigation_scheduler.json`, append to `fields` and to `field_order`:

```json
  {"fieldname": "sec_shift_blocks", "fieldtype": "Section Break", "label": "Shift Blocks"},
  {"fieldname": "shift_blocks", "fieldtype": "Table", "label": "Shift Blocks",
   "options": "Irrigation Shift Block",
   "description": "Which block warehouses each shift waters. One row per shift and block."}
```

Then: `cd /home/austin/frappe-v16-bench && bench --site kaitet.local migrate 2>&1 | tail -15`

- [ ] **Step 5: Run test to verify it passes**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_shift_block`
Expected: PASS, 4 tests

- [ ] **Step 6: Commit**

```bash
git add upande_irrigation/upande_irrigation/doctype/irrigation_shift_block/ upande_irrigation/upande_irrigation/doctype/irrigation_scheduler/ upande_irrigation/tests/test_shift_block.py
git commit -m "feat(migration): add the Irrigation Shift Block child table"
```

---

### Task 6: Repoint the engine off `Block Type`

Four call sites read `Block Type`. All must move to `shift_blocks` before the doctype can go.

**Files:**
- Create: `upande_irrigation/shifts.py`
- Modify: `upande_irrigation/events/irrigation_planner.py:50-75` (`shift_settings`), `:200-205` (`active_shift_count`)
- Modify: `upande_irrigation/api/scheduler.py:359-366` (raw SQL)
- Test: `upande_irrigation/tests/test_shifts.py`

**Interfaces:**
- Consumes: `Irrigation Shift Block` from Task 5.
- Produces, all in `upande_irrigation.shifts`:
  - `shift_overrides(shift: str) -> dict` — `{"application_rate_mm_hr": float, "irrigation_coverage": float}`, zeros when unknown.
  - `active_shifts(farm: str, prefix: str) -> list[str]` — active shift names for a section prefix, ordered by trailing shift number.
  - `count_active_shifts(farm: str, prefix: str) -> int`
  - `blocks_of(shift: str) -> list[str]` — the block warehouse names.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for shift lookup after Block Type is retired.

The bug being locked out: Block Type rows were ordered by CAST(SUBSTRING_INDEX(...))
so SHIFT 10 sorted after SHIFT 9, not between SHIFT 1 and SHIFT 2. A naive string
sort over the new table silently reorders every section's irrigation.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation import shifts

FARM = "Lokitela"
ROWS = [
	{"shift": "ZZ - SHIFT 1", "block": None, "farm": FARM, "is_active": 1,
	 "application_rate_mm_hr": 0, "irrigation_coverage": 0},
	{"shift": "ZZ - SHIFT 2", "block": None, "farm": FARM, "is_active": 1,
	 "application_rate_mm_hr": 4.5, "irrigation_coverage": 70.0},
	{"shift": "ZZ - SHIFT 10", "block": None, "farm": FARM, "is_active": 1,
	 "application_rate_mm_hr": 0, "irrigation_coverage": 0},
	{"shift": "ZZ - SHIFT 3", "block": None, "farm": FARM, "is_active": 0,
	 "application_rate_mm_hr": 0, "irrigation_coverage": 0},
]


class TestShiftLookup(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		wh = frappe.get_all(
			"Warehouse", filters={"is_group": 0}, pluck="name", limit=1
		)
		cls.wh = wh[0] if wh else None
		sched = frappe.get_single("Irrigation Scheduler")
		cls.saved = [r.as_dict() for r in (sched.shift_blocks or [])]
		sched.set("shift_blocks", [])
		for r in ROWS:
			sched.append("shift_blocks", {**r, "block": cls.wh})
		sched.save(ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		sched = frappe.get_single("Irrigation Scheduler")
		sched.set("shift_blocks", [])
		for r in cls.saved:
			sched.append("shift_blocks", r)
		sched.save(ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def test_shifts_sort_numerically_not_lexically(self):
		self.assertEqual(
			shifts.active_shifts(FARM, "ZZ"), ["ZZ - SHIFT 1", "ZZ - SHIFT 2", "ZZ - SHIFT 10"]
		)

	def test_inactive_shifts_are_excluded(self):
		self.assertNotIn("ZZ - SHIFT 3", shifts.active_shifts(FARM, "ZZ"))

	def test_count_matches_the_list(self):
		self.assertEqual(shifts.count_active_shifts(FARM, "ZZ"), 3)

	def test_overrides_come_back_for_a_shift_that_has_them(self):
		self.assertEqual(
			shifts.shift_overrides("ZZ - SHIFT 2"),
			{"application_rate_mm_hr": 4.5, "irrigation_coverage": 70.0},
		)

	def test_an_unknown_shift_gives_zeros_not_an_error(self):
		self.assertEqual(
			shifts.shift_overrides("NOPE - SHIFT 1"),
			{"application_rate_mm_hr": 0.0, "irrigation_coverage": 0.0},
		)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_shifts`
Expected: FAIL — `cannot import name 'shifts'`

- [ ] **Step 3: Write the lookup module**

Create `upande_irrigation/shifts.py`:

```python
"""Shift lookup, reading the Irrigation Scheduler's shift_blocks table.

Replaces the Block Type queries. A shift is one row per block, so the shift-level
columns repeat across a shift's rows; every reader here takes the first row of a
shift and ignores the rest, which is safe because the loader refuses to import a
shift whose rows disagree.
"""

import frappe

SCHEDULER = "Irrigation Scheduler"
SEPARATOR = " - SHIFT "


def _rows():
	return frappe.get_single(SCHEDULER).shift_blocks or []


def _shift_number(shift):
	"""Trailing integer of '70HA - SHIFT 20'. Block Type sorted on this, so we do too."""
	tail = (shift or "").rsplit(SEPARATOR, 1)[-1]
	try:
		return int(tail)
	except (TypeError, ValueError):
		return 0


def active_shifts(farm, prefix):
	"""Active shift names for one section prefix, in irrigation order."""
	wanted = f"{prefix}{SEPARATOR}"
	names = {
		r.shift
		for r in _rows()
		if r.is_active and r.farm == farm and (r.shift or "").startswith(wanted)
	}
	return sorted(names, key=_shift_number)


def count_active_shifts(farm, prefix):
	return len(active_shifts(farm, prefix))


def shift_overrides(shift):
	"""This shift's rate and coverage. Zero means inherit the farm default."""
	for r in _rows():
		if r.shift == shift:
			return {
				"application_rate_mm_hr": float(r.application_rate_mm_hr or 0),
				"irrigation_coverage": float(r.irrigation_coverage or 0),
			}
	return {"application_rate_mm_hr": 0.0, "irrigation_coverage": 0.0}


def blocks_of(shift):
	"""Block warehouse names this shift waters."""
	return [r.block for r in _rows() if r.shift == shift and r.block]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_shifts`
Expected: PASS, 5 tests

- [ ] **Step 5: Repoint the three call sites**

In `upande_irrigation/events/irrigation_planner.py`, replace the body of `shift_settings` after `if not block: return out` — the `frappe.db.get_value("Block Type", ...)` call becomes:

```python
	from upande_irrigation.shifts import shift_overrides

	row = shift_overrides(block)
```

Leave the `rate`/`coverage` logic below it unchanged — `shift_overrides` returns the same two keys.

In the same file, replace the `doc.active_shift_count = frappe.db.count("Block Type", {...})` call with:

```python
	from upande_irrigation.shifts import count_active_shifts

	doc.active_shift_count = count_active_shifts(doc.farm, section_prefix)
```

In `upande_irrigation/api/scheduler.py`, replace the `frappe.db.sql("""SELECT name FROM \`tabBlock Type\` ...""")` block with:

```python
			from upande_irrigation.shifts import active_shifts

			shifts = [{"name": s} for s in active_shifts(farm, prefix)]
```

- [ ] **Step 6: Run the whole suite**

Run: `bench --site kaitet.local run-tests --app upande_irrigation`
Expected: PASS. `test_planner_hook` and `test_planner_integration` still reference `Block Type` and will skip or fail — that is Task 7's job, note which.

- [ ] **Step 7: Commit**

```bash
git add upande_irrigation/shifts.py upande_irrigation/events/irrigation_planner.py upande_irrigation/api/scheduler.py upande_irrigation/tests/test_shifts.py
git commit -m "feat(migration): read shifts from shift_blocks, not Block Type"
```

---

### Task 7: Load the 57-shift mapping with validation

**Files:**
- Create: `upande_irrigation/migration/shift_loader.py`
- Create: `upande_irrigation/migration/data/shift_mapping.json` (the 89 rows)
- Modify: `upande_irrigation/tests/test_planner_hook.py`, `upande_irrigation/tests/test_planner_integration.py` (drop `Block Type`)
- Test: `upande_irrigation/tests/test_shift_loader.py`

**Interfaces:**
- Consumes: `Irrigation Shift Block` (Task 5), `shifts` module (Task 6).
- Produces: `validate_mapping(rows: list[dict]) -> list[str]` returning human-readable defect strings (empty when clean); `load_mapping(rows: list[dict], strict: bool = True) -> dict` returning `{"loaded": int, "defects": list[str]}`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the shift-mapping loader.

Two bugs being locked out: a shift whose rows disagree on rate or coverage — the
table is denormalised, so a disagreement means one of the rows is wrong and
picking either silently changes a shift's hours — and a row naming a group
warehouse, which is what three of the live 23HA rows do.
"""

from frappe.tests.utils import FrappeTestCase

from upande_irrigation.migration.shift_loader import validate_mapping

GOOD = [
	{"shift": "A - SHIFT 1", "block": "BLK 1 - KL", "farm": "Lokitela",
	 "is_active": 1, "application_rate_mm_hr": 4.0, "irrigation_coverage": 70.0},
	{"shift": "A - SHIFT 1", "block": "BLK 2 - KL", "farm": "Lokitela",
	 "is_active": 1, "application_rate_mm_hr": 4.0, "irrigation_coverage": 70.0},
]


class TestValidateMapping(FrappeTestCase):
	def test_consistent_rows_report_no_defect(self):
		self.assertEqual(validate_mapping(GOOD), [])

	def test_disagreeing_rate_within_a_shift_is_a_defect(self):
		rows = [dict(GOOD[0]), dict(GOOD[1], application_rate_mm_hr=9.0)]
		defects = validate_mapping(rows)
		self.assertEqual(len(defects), 1)
		self.assertIn("application_rate_mm_hr", defects[0])
		self.assertIn("A - SHIFT 1", defects[0])

	def test_disagreeing_farm_within_a_shift_is_a_defect(self):
		rows = [dict(GOOD[0]), dict(GOOD[1], farm="Elsewhere")]
		self.assertIn("farm", validate_mapping(rows)[0])

	def test_a_duplicate_block_in_one_shift_is_a_defect(self):
		rows = [dict(GOOD[0]), dict(GOOD[0])]
		self.assertIn("duplicate", validate_mapping(rows)[0].lower())

	def test_a_shift_with_no_rows_at_all_is_not_a_crash(self):
		self.assertEqual(validate_mapping([]), [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_shift_loader`
Expected: FAIL — `No module named '...shift_loader'`

- [ ] **Step 3: Write the loader**

Create `upande_irrigation/migration/shift_loader.py`:

```python
"""Load the shift→block mapping into Irrigation Scheduler.shift_blocks.

The table is denormalised — farm, is_active, rate and coverage are attributes of
the shift but live on every one of its block rows. That is only safe if the rows
of a shift agree, so disagreement is a hard failure rather than a silent pick.

Group warehouses are caught by the child DocType's own validate(); this module
reports them up front so the whole defect list arrives at once instead of one
exception at a time.
"""

import collections
import json
import os

import frappe

SCHEDULER = "Irrigation Scheduler"
SHIFT_LEVEL = ("farm", "is_active", "application_rate_mm_hr", "irrigation_coverage")


def validate_mapping(rows):
	"""Human-readable defects. Empty list means the mapping is internally consistent."""
	defects = []
	by_shift = collections.defaultdict(list)
	for r in rows:
		by_shift[r["shift"]].append(r)

	for shift, group in sorted(by_shift.items()):
		for field in SHIFT_LEVEL:
			seen = {r.get(field) for r in group}
			if len(seen) > 1:
				defects.append(
					f"{shift}: rows disagree on {field} — {sorted(seen, key=str)}"
				)
		blocks = [r.get("block") for r in group]
		for block, n in collections.Counter(blocks).items():
			if n > 1:
				defects.append(f"{shift}: duplicate block row for {block} (×{n})")
	return defects


def load_mapping(rows, strict=True):
	"""Replace shift_blocks with these rows. Returns {"loaded", "defects"}."""
	defects = validate_mapping(rows)
	if defects and strict:
		frappe.throw("Shift mapping is inconsistent:\n" + "\n".join(defects))

	sched = frappe.get_single(SCHEDULER)
	sched.set("shift_blocks", [])
	for r in rows:
		sched.append("shift_blocks", {
			"shift": r["shift"],
			"block": r["block"],
			"farm": r.get("farm"),
			"is_active": 1 if r.get("is_active") else 0,
			"application_rate_mm_hr": r.get("application_rate_mm_hr") or 0,
			"irrigation_coverage": r.get("irrigation_coverage") or 0,
		})
	sched.save(ignore_permissions=True)
	frappe.db.commit()
	return {"loaded": len(rows), "defects": defects}


def load_from_file(path=None, strict=True):
	path = path or os.path.join(os.path.dirname(__file__), "data", "shift_mapping.json")
	with open(path) as fh:
		return load_mapping(json.load(fh), strict=strict)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_shift_loader`
Expected: PASS, 5 tests

- [ ] **Step 5: Build the mapping file from the live site**

The 57 shifts and their 89 rows are already captured in the session scratchpad at `shifts.json` (shift → `[[block, section], …]` plus `a` for is_active). Convert it, adding `farm` (all `Lokitela`) and the rate/coverage overrides read from c.frappe.cloud's `Block Type` Custom Fields — which are all blank today, so zeros.

```bash
mkdir -p upande_irrigation/migration/data
../../env/bin/python - <<'PY'
import json
SRC = "/path/to/export/shifts.json"
rows = []
for shift, v in json.load(open(SRC)).items():
    for block, _section in v["b"]:
        rows.append({
            "shift": shift, "block": block, "farm": "Lokitela",
            "is_active": v["a"],
            "application_rate_mm_hr": 0, "irrigation_coverage": 0,
        })
json.dump(rows, open("upande_irrigation/migration/data/shift_mapping.json", "w"), indent=1)
print(len(rows), "rows")
PY
```

Expected: `89 rows`.

- [ ] **Step 6: Load it and read the defect report**

```bash
cd /home/austin/frappe-v16-bench
bench --site kaitet.local execute upande_irrigation.migration.shift_loader.load_from_file --kwargs '{"strict": false}'
```

Expected: `{"loaded": 89, "defects": [...]}` — the defect list should name `23HA - SHIFT 1` for its duplicate block row. Group-warehouse rows will additionally be rejected by the child DocType's `validate()`; if the save throws, record which rows and re-run with those three rows removed, noting the removal in the commit message. **Do not "fix" the data — the spec defers that to Phase 3.**

- [ ] **Step 7: Drop `Block Type` from the two tests**

In `upande_irrigation/tests/test_planner_hook.py`, replace both
`frappe.db.get_value("Block Type", {"is_active": 1}, "name")` lookups with:

```python
		from upande_irrigation import shifts
		names = shifts.active_shifts("Lokitela", "70HA")
		block = names[0] if names else None
```

and change the two `frappe.db.set_value("Block Type", block, ...)` calls to set the
override on the matching `shift_blocks` rows instead:

```python
		sched = frappe.get_single("Irrigation Scheduler")
		for row in sched.shift_blocks:
			if row.shift == block:
				row.application_rate_mm_hr = rate
				row.irrigation_coverage = coverage
		sched.save(ignore_permissions=True)
```

In `upande_irrigation/tests/test_planner_integration.py:29`, replace the
`frappe.db.get_value("Block Type", {...}, "name")` call with:

```python
		from upande_irrigation import shifts
		names = shifts.active_shifts(FARM, "70HA")
		cls.block = names[0] if names else None
```

- [ ] **Step 8: Run the whole suite**

Run: `bench --site kaitet.local run-tests --app upande_irrigation`
Expected: PASS, no test referencing `Block Type`.

- [ ] **Step 9: Commit**

```bash
git add upande_irrigation/migration/ upande_irrigation/tests/
git commit -m "feat(migration): load the 89-row shift mapping with consistency checks"
```

---

### Task 8: Repoint `Irrigation Planner.block` and retire the three doctypes

**Files:**
- Modify: `upande_irrigation/upande_irrigation/doctype/irrigation_planner/irrigation_planner.json`
- Create: `upande_irrigation/patches/v1_0/retire_block_type.py`
- Modify: `upande_irrigation/patches.txt`
- Test: `upande_irrigation/tests/test_retire_block_type.py`

**Interfaces:**
- Consumes: Tasks 5–7.
- Produces: `Irrigation Planner.block` as `Data`; `execute()` dropping `Block Type`, `Block configaration`, `Blocks List`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for retiring Block Type.

The bug being locked out: dropping the DocType while Irrigation Planner.block is
still a Link to it leaves 1,346 records pointing at nothing, and Frappe will
happily render a broken link rather than error. The field has to become Data
first, keeping its values byte-for-byte.
"""

import frappe
from frappe.tests.utils import FrappeTestCase


class TestBlockTypeRetired(FrappeTestCase):
	def test_planner_block_is_now_data(self):
		self.assertEqual(frappe.get_meta("Irrigation Planner").get_field("block").fieldtype, "Data")

	def test_the_three_doctypes_are_gone(self):
		for dt in ("Block Type", "Block configaration", "Blocks List"):
			self.assertFalse(frappe.db.exists("DocType", dt), f"{dt} still present")

	def test_planner_block_values_survived(self):
		blanks = frappe.db.count("Irrigation Planner", {"block": ["in", ["", None]]})
		self.assertEqual(blanks, 0, "planners lost their shift label")

	def test_every_planner_block_names_a_known_shift(self):
		from upande_irrigation import shifts
		known = {r.shift for r in frappe.get_single("Irrigation Scheduler").shift_blocks}
		used = set(frappe.get_all("Irrigation Planner", pluck="block"))
		self.assertEqual(used - known, set(), "planners reference unknown shifts")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_retire_block_type`
Expected: FAIL — `block` is still `Link`

- [ ] **Step 3: Change the field type in the code DocType**

In `upande_irrigation/upande_irrigation/doctype/irrigation_planner/irrigation_planner.json`, the `block` field becomes:

```json
  {"fieldname": "block", "fieldtype": "Data", "label": "Shift", "reqd": 1,
   "in_list_view": 1,
   "description": "Shift name, e.g. 70HA - SHIFT 20. Blocks are on Irrigation Scheduler."}
```

- [ ] **Step 4: Write the retirement patch**

Create `upande_irrigation/patches/v1_0/retire_block_type.py`:

```python
"""Drop Block Type and its two satellites.

Block Type modelled an irrigation shift, not a block — its child rows already
named Warehouses, all 89 of which exist on the v16 destination. The grouping now
lives on Irrigation Scheduler.shift_blocks, so the trio is dead weight, and two of
the three belong to modules the destination does not have.

post_model_sync: Irrigation Planner.block must already be Data, which the code
DocType does during the model sync in the same migrate.
"""

import frappe

RETIRED = ("Blocks List", "Block Type", "Block configaration")


def execute():
	fieldtype = frappe.db.get_value(
		"DocField", {"parent": "Irrigation Planner", "fieldname": "block"}, "fieldtype"
	)
	if fieldtype != "Data":
		frappe.throw(
			f"Irrigation Planner.block is {fieldtype}, expected Data. "
			"Refusing to drop Block Type while planners still link to it."
		)

	for dt in RETIRED:
		if frappe.db.exists("DocType", dt):
			frappe.delete_doc("DocType", dt, force=True, ignore_permissions=True)

	frappe.clear_cache()
```

- [ ] **Step 5: Register it under `[post_model_sync]`**

Append to `upande_irrigation/patches.txt`:

```
upande_irrigation.patches.v1_0.retire_block_type
```

- [ ] **Step 6: Migrate**

```bash
cd /home/austin/frappe-v16-bench && bench --site kaitet.local migrate 2>&1 | tail -25
```

Expected: no errors; the patch runs once.

- [ ] **Step 7: Run test to verify it passes**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_retire_block_type`
Expected: PASS, 4 tests

- [ ] **Step 8: Delete the two dead fixture files**

Task 3 established that `frappe.utils.fixtures.import_fixtures` reads the fixtures **directory**, not the `fixtures` list in `hooks.py` — that hook governs only export. So any `.json` left in `upande_irrigation/fixtures/` is re-imported on every single migrate, and a stale one will overwrite the code definition it duplicates.

After this task, both remaining files are dead weight:

- `custom_field.json` holds only `Block Type` entries, and `Block Type` no longer exists.
- `doctype.json` holds only `Tank And Valve`, which has been a code DocType since Task 2. Leaving it means every migrate re-imports a fixture copy over the code definition — harmless while the two agree, a silent revert the first time someone edits the code JSON.

```bash
git rm upande_irrigation/fixtures/doctype.json upande_irrigation/fixtures/custom_field.json
cd /home/austin/frappe-v16-bench && bench --site kaitet.local migrate 2>&1 | tail -15
bench --site kaitet.local mariadb -e "
  SELECT name, custom FROM \`tabDocType\` WHERE module='Upande Irrigation' ORDER BY name;
  SELECT COUNT(*) AS tank_and_valve FROM \`tabTank And Valve\`;"
```

Expected: migrate clean, every DocType still `custom = 0`, `Tank And Valve` still 58 rows. `property_setter.json` and `custom_html_block.json` stay — they carry the Pump Profile defaults and the dashboard block, neither of which is a DocType definition.

- [ ] **Step 9: Commit**

```bash
git add -A upande_irrigation/upande_irrigation/doctype/irrigation_planner/ upande_irrigation/patches/ upande_irrigation/patches.txt upande_irrigation/tests/test_retire_block_type.py upande_irrigation/fixtures/
git commit -m "feat(migration): retire Block Type, planner.block becomes Data"
```

---

### Task 9: Pull the ~7,500 live records

**Files:**
- Create: `upande_irrigation/migration/pull.py`
- Test: `upande_irrigation/tests/test_pull.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `insert_preserving(doctype: str, records: list[dict]) -> int` — inserts with `name`, `creation`, `owner` preserved, skipping records that already exist; returns the count inserted.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for record insertion that keeps identity.

The bug being locked out: frappe.get_doc().insert() mints a fresh name and stamps
creation with now(). Irrigation Planner names are referenced in Irrigation
Scheduler Run Shift.planner, and every dashboard chart is drawn over creation
dates, so both have to survive the move.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation.migration.pull import insert_preserving

RECORD = {
	"name": "TEST-WX-0001",
	"creation": "2026-01-02 03:04:05.000000",
	"owner": "Administrator",
	"farm": "Lokitela",
	"date": "2026-01-02",
}


class TestInsertPreserving(FrappeTestCase):
	def tearDown(self):
		frappe.db.delete("Weather Reading", {"name": RECORD["name"]})
		frappe.db.commit()

	def test_it_keeps_the_source_name(self):
		insert_preserving("Weather Reading", [RECORD])
		self.assertTrue(frappe.db.exists("Weather Reading", RECORD["name"]))

	def test_it_keeps_the_source_creation(self):
		insert_preserving("Weather Reading", [RECORD])
		creation = str(frappe.db.get_value("Weather Reading", RECORD["name"], "creation"))
		self.assertTrue(creation.startswith("2026-01-02 03:04:05"))

	def test_an_existing_record_is_skipped_not_duplicated(self):
		insert_preserving("Weather Reading", [RECORD])
		self.assertEqual(insert_preserving("Weather Reading", [RECORD]), 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_pull`
Expected: FAIL — `No module named '...pull'`

- [ ] **Step 3: Write the inserter**

Create `upande_irrigation/migration/pull.py`:

```python
"""Insert exported records while keeping their identity.

insert() would mint a new name and a new creation timestamp. Planner names are
referenced from Irrigation Scheduler Run Shift, and every chart is drawn over
creation, so both are carried across verbatim.
"""

import frappe


def insert_preserving(doctype, records):
	"""Insert records keeping name/creation/owner. Returns how many were inserted."""
	inserted = 0
	for record in records:
		if frappe.db.exists(doctype, record["name"]):
			continue

		doc = frappe.get_doc({**record, "doctype": doctype})
		doc.flags.name_set = True
		doc.insert(ignore_permissions=True, set_name=record["name"], ignore_links=True)

		frappe.db.set_value(
			doctype, record["name"],
			{"creation": record.get("creation"), "owner": record.get("owner")},
			update_modified=False,
		)
		inserted += 1

	frappe.db.commit()
	return inserted


# Load order matters: Tank And Valve and Weather Reading are referenced by the
# records that follow, and Irrigation Scheduler Run Shift rows name planners.
LOAD_ORDER = (
	("Tank And Valve", "tank_and_valve.json", 58),
	("Weather Reading", "weather_reading.json", 5431),
	("Irrigation Planner", "irrigation_planner.json", 1346),
	("Reservoir Pumping Record", "reservoir_pumping_record.json", 369),
	("Water Transfer", "water_transfer.json", 279),
	("Irrigation Scheduler Run", "irrigation_scheduler_run.json", 32),
)


def load_all(data_dir=None):
	"""Load every exported file in dependency order. Returns per-doctype counts."""
	data_dir = data_dir or os.path.join(os.path.dirname(__file__), "data")
	report = {}
	for doctype, filename, expected in LOAD_ORDER:
		path = os.path.join(data_dir, filename)
		if not os.path.exists(path):
			report[doctype] = f"MISSING {filename}"
			continue
		with open(path) as fh:
			records = json.load(fh)
		inserted = insert_preserving(doctype, records)
		total = frappe.db.count(doctype)
		report[doctype] = {
			"in_file": len(records), "inserted": inserted,
			"now_on_site": total, "expected": expected,
			"ok": total == expected,
		}
	print(json.dumps(report, indent=1))
	return report
```

Add `import json` and `import os` to the top of the module alongside `import frappe`.

- [ ] **Step 4: Run test to verify it passes**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_pull`
Expected: PASS, 3 tests

- [ ] **Step 5: Export from c.frappe.cloud**

Read-only, via the `Kaitet_2` MCP connection, which caps at 1000 records per call —
so Weather Reading takes six pages and Irrigation Planner two. Write each to
`upande_irrigation/migration/data/<filename>` using the exact filenames in
`LOAD_ORDER` above, as a JSON list of record dicts.

Each record must carry **`name`, `creation`, `owner`** plus every data field —
request `fields=["*"]` and page with `limit_start`, ordering by `creation asc` so
pages do not overlap. For `Irrigation Scheduler Run`, fetch each of the 32 with
`get_document` rather than `list_documents`, so the `shift_results` child rows come
with it; store them under the `shift_results` key of the parent record.

Singles are exported separately as one dict each. When loading
`Irrigation Scheduler`, **do not overwrite `shift_blocks`** — Task 7 populated it,
and the source site has no such field.

- [ ] **Step 6: Clear the bench's divergent irrigation data**

`kaitet.local` holds data that matches neither the source nor a clean site — 1,650 planners against the source's 1,346, 5,394 weather readings against 5,431, zero water transfers against 279, 35 scheduler runs against 32. The extra records are local test-run debris. `insert_preserving` skips names that already exist, so loading on top would produce a blend belonging to no real site, and the count assertions in this task and Task 12 would be meaningless.

upande.com is a clean install, so the dry-run starts clean too.

**This is destructive and has been explicitly approved.** The backup at `/home/austin/frappe-v16-bench/pre-v16-migration-irrigation.sql.gz` covers exactly these tables.

```bash
cd /home/austin/frappe-v16-bench
bench --site kaitet.local mariadb -e "
  DELETE FROM \`tabIrrigation Scheduler Run Shift\`;
  DELETE FROM \`tabIrrigation Scheduler Run\`;
  DELETE FROM \`tabIrrometer Reading\`;
  DELETE FROM \`tabIrrigation Planner\`;
  DELETE FROM \`tabWeather Reading\`;
  DELETE FROM \`tabWater Transfer\`;
  DELETE FROM \`tabReservoir Pumping Record\`;
  DELETE FROM \`tabTank And Valve\`;"
bench --site kaitet.local mariadb -e "
  SELECT 'planner' k, COUNT(*) n FROM \`tabIrrigation Planner\`
  UNION SELECT 'weather', COUNT(*) FROM \`tabWeather Reading\`;"
```

Expected: both counts `0`. Child tables are deleted before their parents so no orphan rows survive. **Do not** delete `Irrigation Scheduler` or `Irrigation Settings` — they are Singles, and `Irrigation Scheduler` holds the `shift_blocks` rows Task 7 loaded.

- [ ] **Step 7: Load and reconcile counts**

```bash
cd /home/austin/frappe-v16-bench
bench --site kaitet.local execute upande_irrigation.migration.pull.load_all
bench --site kaitet.local mariadb -e "
  SELECT 'Weather Reading' dt, COUNT(*) n FROM \`tabWeather Reading\`
  UNION SELECT 'Irrigation Planner', COUNT(*) FROM \`tabIrrigation Planner\`
  UNION SELECT 'Reservoir Pumping Record', COUNT(*) FROM \`tabReservoir Pumping Record\`
  UNION SELECT 'Water Transfer', COUNT(*) FROM \`tabWater Transfer\`
  UNION SELECT 'Tank And Valve', COUNT(*) FROM \`tabTank And Valve\`
  UNION SELECT 'Irrigation Scheduler Run', COUNT(*) FROM \`tabIrrigation Scheduler Run\`;"
```

Expected: 5431 / 1346 / 369 / 279 / 58 / 32. Any shortfall is a failed insert — investigate before continuing, do not proceed on a partial load.

- [ ] **Step 7: Commit**

```bash
git add upande_irrigation/migration/pull.py upande_irrigation/tests/test_pull.py
git commit -m "feat(migration): insert exported records preserving name and creation"
```

Note: `migration/data/*.json` is production data — add it to `.gitignore` rather than committing it.

---

### Task 10: v16 SQL audit

33 raw `frappe.db.sql` calls. The one in `api/scheduler.py` is already gone (Task 6); the rest need checking against 16.32.0 behaviour.

**Files:**
- Modify: `upande_irrigation/api/overview.py`, `sensors.py`, `planner.py`, `valves.py`, `events/weather_reading.py`
- Test: `upande_irrigation/tests/test_api_smoke.py`

**Interfaces:**
- Consumes: loaded data from Task 9.
- Produces: no new interfaces — every endpoint keeps its signature.

- [ ] **Step 1: Write the failing test**

```python
"""Smoke tests for every whitelisted endpoint against real data.

The bug being locked out: these endpoints are reached only from the dashboard, so
a v16 SQL incompatibility surfaces as an empty panel in a browser rather than a
failing test. Each one is called here with the arguments the dashboard sends.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation.api import (
	overview, planner, resources, scheduler, sensors, valves, weather
)

FARM = "Lokitela"


class TestEndpointsDoNotRaise(FrappeTestCase):
	def test_overview_fetch(self):
		self.assertIsNotNone(overview.fetch(farm=FARM))

	def test_planner_fetch(self):
		self.assertIsNotNone(planner.fetch(farm=FARM))

	def test_planner_explain(self):
		name = frappe.db.get_value("Irrigation Planner", {}, "name")
		if not name:
			self.skipTest("no planners on this site")
		self.assertIsNotNone(planner.explain(planner=name))

	def test_resources_fetch(self):
		self.assertIsNotNone(resources.fetch(days=30, farm=FARM))

	def test_sensors_fetch(self):
		self.assertIsNotNone(sensors.fetch(days=30))

	def test_valves_list_states(self):
		self.assertIsNotNone(valves.list_states(farm=FARM))

	def test_valves_geojson(self):
		self.assertIsNotNone(valves.geojson(farm=FARM))

	def test_weather_fetch(self):
		self.assertIsNotNone(weather.fetch(days=30, farm=FARM))

	def test_scheduler_live_sections(self):
		self.assertIsNotNone(scheduler.live_sections(farm=FARM))
```

These are the real signatures as of this plan — `fetch` in overview/planner/
resources/sensors/weather, `list_states` and `geojson` in valves, `live_sections`
in scheduler. `scheduler.run` is deliberately not smoke-tested here; Task 12
exercises it for real.

- [ ] **Step 2: Run test to see which endpoints break**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_api_smoke`
Expected: some FAIL. Record each failure's exact exception.

- [ ] **Step 3: Fix each failure**

For every failing call, convert the offending raw SQL to `frappe.qb` or `frappe.get_all`. Do not rewrite SQL that passes — this task fixes v16 breakage, not style.

- [ ] **Step 4: Run test to verify it passes**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_api_smoke`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add upande_irrigation/api/ upande_irrigation/events/ upande_irrigation/tests/test_api_smoke.py
git commit -m "fix(v16): bring the dashboard endpoints up to 16.32"
```

---

### Task 11: Apps-screen tile, sidebar and icon

**Files:**
- Create: `upande_irrigation/public/images/upande-logo.png` (copied)
- Create: `upande_irrigation/workspace_sidebar/upande_irrigation.json`
- Modify: `upande_irrigation/hooks.py`
- Test: `upande_irrigation/tests/test_ui_surfaces.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `add_to_apps_screen` entry named `upande_irrigation`; `Workspace Sidebar` named `Upande Irrigation`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the desk surfaces.

The bug being locked out: referencing /assets/upande_core/images/ for the logo
breaks the tile on any site without upande_core. The CRM's own hooks comment says
the same thing; here it matters more, because whether upande_core ships alongside
this app on every future site is not guaranteed.
"""

import os

import frappe
from frappe.tests.utils import FrappeTestCase

import upande_irrigation


class TestAppsScreen(FrappeTestCase):
	def test_there_is_one_apps_screen_entry(self):
		from upande_irrigation import hooks
		self.assertEqual(len(hooks.add_to_apps_screen), 1)

	def test_the_logo_is_served_from_this_app(self):
		from upande_irrigation import hooks
		self.assertTrue(hooks.add_to_apps_screen[0]["logo"].startswith("/assets/upande_irrigation/"))

	def test_the_logo_file_actually_exists(self):
		path = os.path.join(os.path.dirname(upande_irrigation.__file__), "public", "images", "upande-logo.png")
		self.assertTrue(os.path.exists(path), f"missing {path}")


class TestWorkspaceSidebar(FrappeTestCase):
	def test_the_sidebar_exists(self):
		self.assertTrue(frappe.db.exists("Workspace Sidebar", "Upande Irrigation"))

	def test_it_links_the_smart_irrigation_workspace(self):
		doc = frappe.get_doc("Workspace Sidebar", "Upande Irrigation")
		targets = {i.link_to for i in doc.items if i.link_type == "Workspace"}
		self.assertIn("Smart Irrigation", targets)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_ui_surfaces`
Expected: FAIL — `hooks has no attribute 'add_to_apps_screen'`

- [ ] **Step 3: Copy the logo**

```bash
cd /home/austin/frappe-v16-bench/apps
mkdir -p upande_irrigation/upande_irrigation/public/images
cp upande_core/upande_core/public/images/upande-logo.png \
   upande_irrigation/upande_irrigation/public/images/upande-logo.png
```

- [ ] **Step 4: Add the hooks entry**

In `upande_irrigation/hooks.py`, after `website_redirects`:

```python
# The /apps launcher tile. The logo is copied into this app rather than read from
# /assets/upande_core/ so the tile does not break on a site that has irrigation
# without upande_core — the destination has both today, but the app should not
# depend on that.
add_to_apps_screen = [
	{
		"name": "upande_irrigation",
		"logo": "/assets/upande_irrigation/images/upande-logo.png",
		"title": "Upande Irrigation",
		"route": "/app/smart-irrigation",
	}
]
```

The route is `/app/smart-irrigation`, not `/app/upande-irrigation`: the workspace this app ships is named **Smart Irrigation**, and `/upande-irrigation` (no `/app`) is the www dashboard, which the sidebar already pins at the bottom. A tile pointing at a route with no workspace behind it renders an empty desk page.

- [ ] **Step 5: Write the sidebar**

Create `upande_irrigation/workspace_sidebar/upande_irrigation.json`:

```json
{
 "app": "upande_irrigation",
 "creation": "2026-09-13 00:00:00.000000",
 "docstatus": 0,
 "doctype": "Workspace Sidebar",
 "header_icon": "droplet",
 "idx": 0,
 "items": [
  {"child": 0, "collapsible": 1, "icon": "droplet", "indent": 0, "keep_closed": 0,
   "label": "Overview", "link_to": "Smart Irrigation", "link_type": "Workspace",
   "show_arrow": 0, "type": "Link"},
  {"child": 0, "collapsible": 1, "icon": "calendar-check", "indent": 1, "keep_closed": 0,
   "label": "Planning", "link_type": "DocType", "show_arrow": 0, "type": "Section Break"},
  {"child": 1, "collapsible": 1, "indent": 0, "keep_closed": 0, "label": "Irrigation Planner",
   "link_to": "Irrigation Planner", "link_type": "DocType", "show_arrow": 0, "type": "Link"},
  {"child": 1, "collapsible": 1, "indent": 0, "keep_closed": 0, "label": "Scheduler Runs",
   "link_to": "Irrigation Scheduler Run", "link_type": "DocType", "show_arrow": 0, "type": "Link"},
  {"child": 0, "collapsible": 1, "icon": "waves", "indent": 1, "keep_closed": 0,
   "label": "Operations", "link_type": "DocType", "show_arrow": 0, "type": "Section Break"},
  {"child": 1, "collapsible": 1, "indent": 0, "keep_closed": 0, "label": "Water Transfers",
   "link_to": "Water Transfer", "link_type": "DocType", "show_arrow": 0, "type": "Link"},
  {"child": 1, "collapsible": 1, "indent": 0, "keep_closed": 0, "label": "Reservoir Pumping",
   "link_to": "Reservoir Pumping Record", "link_type": "DocType", "show_arrow": 0, "type": "Link"},
  {"child": 1, "collapsible": 1, "indent": 0, "keep_closed": 0, "label": "Tanks and Valves",
   "link_to": "Tank And Valve", "link_type": "DocType", "show_arrow": 0, "type": "Link"},
  {"child": 0, "collapsible": 1, "icon": "cloud-sun", "indent": 1, "keep_closed": 0,
   "label": "Monitoring", "link_type": "DocType", "show_arrow": 0, "type": "Section Break"},
  {"child": 1, "collapsible": 1, "indent": 0, "keep_closed": 0, "label": "Weather Readings",
   "link_to": "Weather Reading", "link_type": "DocType", "show_arrow": 0, "type": "Link"},
  {"child": 0, "collapsible": 1, "icon": "settings", "indent": 1, "keep_closed": 0,
   "label": "Settings", "link_type": "DocType", "show_arrow": 0, "type": "Section Break"},
  {"child": 1, "collapsible": 1, "indent": 0, "keep_closed": 0, "label": "Irrigation Settings",
   "link_to": "Irrigation Settings", "link_type": "DocType", "show_arrow": 0, "type": "Link"},
  {"child": 1, "collapsible": 1, "indent": 0, "keep_closed": 0, "label": "Irrigation Scheduler",
   "link_to": "Irrigation Scheduler", "link_type": "DocType", "show_arrow": 0, "type": "Link"},
  {"child": 1, "collapsible": 1, "indent": 0, "keep_closed": 0, "label": "Pump Profiles",
   "link_to": "Irrigation Pump Profile", "link_type": "DocType", "show_arrow": 0, "type": "Link"},
  {"child": 0, "collapsible": 1, "icon": "layout-dashboard", "indent": 0, "keep_closed": 0,
   "label": "Irrigation Dashboard", "link_type": "URL", "show_arrow": 0, "type": "Link",
   "url": "/upande-irrigation"}
 ],
 "modified": "2026-09-13 00:00:00.000000",
 "modified_by": "Administrator",
 "module": "Upande Irrigation",
 "name": "Upande Irrigation",
 "owner": "Administrator",
 "standard": 1,
 "title": "Upande Irrigation"
}
```

- [ ] **Step 6: Build and migrate**

```bash
cd /home/austin/frappe-v16-bench
bench build --app upande_irrigation
bench --site kaitet.local migrate 2>&1 | tail -15
bench --site kaitet.local clear-cache
```

- [ ] **Step 7: Run test to verify it passes**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_ui_surfaces`
Expected: PASS, 5 tests

- [ ] **Step 8: Commit**

```bash
git add upande_irrigation/hooks.py upande_irrigation/workspace_sidebar/ upande_irrigation/public/images/ upande_irrigation/tests/test_ui_surfaces.py
git commit -m "feat(desk): apps-screen tile, workspace sidebar and app icon"
```

---

### Task 12: End-to-end verification

**Files:**
- Test: `upande_irrigation/tests/test_migration_e2e.py`

**Interfaces:**
- Consumes: everything.
- Produces: nothing — this is the gate on Phase 2.

- [ ] **Step 1: Write the end-to-end test**

```python
"""The gate on the production cutover.

Everything below has to hold on real migrated data before the same path is run
against upande.com. A green suite with an empty database proves nothing, so each
assertion names the volume it expects.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_irrigation import shifts

FARM = "Lokitela"


class TestMigratedSiteIsWhole(FrappeTestCase):
	def test_every_record_arrived(self):
		expected = {
			"Weather Reading": 5431, "Irrigation Planner": 1346,
			"Reservoir Pumping Record": 369, "Water Transfer": 279,
			"Tank And Valve": 58, "Irrigation Scheduler Run": 32,
		}
		for doctype, count in expected.items():
			self.assertEqual(frappe.db.count(doctype), count, f"{doctype} short")

	def test_no_doctype_is_still_custom(self):
		custom = frappe.get_all(
			"DocType", filters={"module": "Upande Irrigation", "custom": 1}, pluck="name"
		)
		self.assertEqual(custom, [])

	def test_the_retired_doctypes_are_gone(self):
		for dt in ("Block Type", "Block configaration", "Blocks List"):
			self.assertFalse(frappe.db.exists("DocType", dt))

	def test_every_shift_a_planner_names_is_known(self):
		known = {r.shift for r in frappe.get_single("Irrigation Scheduler").shift_blocks}
		used = set(frappe.get_all("Irrigation Planner", pluck="block"))
		self.assertEqual(used - known, set())

	def test_every_shift_block_is_a_real_leaf_warehouse(self):
		for row in frappe.get_single("Irrigation Scheduler").shift_blocks:
			self.assertTrue(frappe.db.exists("Warehouse", row.block), f"{row.block} missing")
			self.assertFalse(
				frappe.db.get_value("Warehouse", row.block, "is_group"),
				f"{row.block} is a group",
			)

	def test_the_four_sections_each_report_active_shifts(self):
		for prefix in ("23HA", "56HA", "65HA", "70HA"):
			self.assertGreater(len(shifts.active_shifts(FARM, prefix)), 0, prefix)
```

- [ ] **Step 2: Run it**

Run: `bench --site kaitet.local run-tests --module upande_irrigation.tests.test_migration_e2e`
Expected: PASS, 6 tests. `test_every_shift_block_is_a_real_leaf_warehouse` will fail if the three group-warehouse rows were loaded — that is the defect surfacing correctly, and it is resolved by excluding those rows, not by editing the warehouse.

- [ ] **Step 3: Run a real scheduler pass**

```bash
cd /home/austin/frappe-v16-bench
bench --site kaitet.local execute upande_irrigation.scheduled.weekly.run_weekly_scheduler
bench --site kaitet.local mariadb -e "
  SELECT name, status, shifts_attempted, planners_created, planners_failed
  FROM \`tabIrrigation Scheduler Run\` ORDER BY creation DESC LIMIT 1;"
```

Expected: `status` of `Success` or `Partial`, `shifts_attempted` > 0, `planners_failed` = 0. A `Failed` run or any `planners_failed` blocks Phase 2.

- [ ] **Step 4: Check the dashboard renders**

Open `http://kaitet.local:8000/upande-irrigation` and confirm the Overview, Planning, Resources and Weather views all draw with data. A blank panel means a Task 10 endpoint is still broken.

- [ ] **Step 5: Run the whole suite one final time**

Run: `bench --site kaitet.local run-tests --app upande_irrigation`
Expected: PASS, every module.

- [ ] **Step 6: Commit**

```bash
git add upande_irrigation/tests/test_migration_e2e.py
git commit -m "test: end-to-end gate on the migrated bench site"
```

---

## Phase 1 exit criteria

All of the following, or Phase 2 does not start:

- [ ] Every DocType in `Upande Irrigation` has `custom = 0` and a folder in the app.
- [ ] `Block Type`, `Block configaration`, `Blocks List` do not exist.
- [ ] `Irrigation Pump Profile` belongs to `Upande Irrigation`.
- [ ] Record counts match the source exactly: 5431 / 1346 / 369 / 279 / 58 / 32.
- [ ] Every `Irrigation Planner.block` names a shift present in `shift_blocks`.
- [ ] Every `shift_blocks.block` is an existing non-group Warehouse.
- [ ] A live scheduler run completes with zero failed planners.
- [ ] All four dashboard views render.
- [ ] `bench --site kaitet.local run-tests --app upande_irrigation` is green.
- [ ] The defect list from Task 7 is written down for Phase 3.
