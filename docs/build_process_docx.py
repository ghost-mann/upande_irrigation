"""Build the end-to-end process document for Upande Irrigation."""

import pathlib

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

OUT = pathlib.Path(
    "/home/austin/frappe-v16-bench/apps/upande_irrigation/docs/"
    "Upande-Irrigation-End-to-End-Process.docx"
)

INK = RGBColor(0x22, 0x22, 0x22)
MUTED = RGBColor(0x6A, 0x67, 0x60)
GOLD = RGBColor(0xA8, 0x7D, 0x0F)
TEAL = RGBColor(0x1E, 0x6F, 0x6B)
RED = RGBColor(0xA3, 0x28, 0x24)

doc = Document()

# ── Base styles ────────────────────────────────────────────────
normal = doc.styles["Normal"]
normal.font.name = "Calibri"
normal.font.size = Pt(10.5)
normal.font.color.rgb = INK
normal.paragraph_format.space_after = Pt(8)
normal.paragraph_format.line_spacing = 1.15

for level, size, colour in ((1, 20, INK), (2, 15, INK), (3, 12, TEAL)):
    st = doc.styles[f"Heading {level}"]
    st.font.name = "Calibri"
    st.font.size = Pt(size)
    st.font.bold = True
    st.font.color.rgb = colour
    st.paragraph_format.space_before = Pt(18 if level < 3 else 12)
    st.paragraph_format.space_after = Pt(6)

for sec in doc.sections:
    sec.top_margin = Inches(0.9)
    sec.bottom_margin = Inches(0.9)
    sec.left_margin = Inches(1.0)
    sec.right_margin = Inches(1.0)


def para(text="", style=None, size=None, bold=False, italic=False, colour=None,
         space_after=None, align=None):
    p = doc.add_paragraph(style=style)
    if align is not None:
        p.alignment = align
    if space_after is not None:
        p.paragraph_format.space_after = Pt(space_after)
    if text:
        r = p.add_run(text)
        r.bold = bold
        r.italic = italic
        if size:
            r.font.size = Pt(size)
        if colour:
            r.font.color.rgb = colour
    return p


def rich(parts, space_after=None):
    """parts: list of (text, {bold/italic/mono/colour})."""
    p = doc.add_paragraph()
    if space_after is not None:
        p.paragraph_format.space_after = Pt(space_after)
    for text, opts in parts:
        r = p.add_run(text)
        r.bold = opts.get("bold", False)
        r.italic = opts.get("italic", False)
        if opts.get("mono"):
            r.font.name = "Consolas"
            r.font.size = Pt(9.5)
        if opts.get("colour"):
            r.font.color.rgb = opts["colour"]
    return p


def bullet(text, level=0):
    p = doc.add_paragraph(text, style="List Bullet")
    p.paragraph_format.left_indent = Inches(0.25 + 0.25 * level)
    p.paragraph_format.space_after = Pt(4)
    return p


def numbered(text):
    p = doc.add_paragraph(text, style="List Number")
    p.paragraph_format.left_indent = Inches(0.3)
    p.paragraph_format.space_after = Pt(4)
    return p


def shade(cell, hex_fill):
    el = OxmlElement("w:shd")
    el.set(qn("w:val"), "clear")
    el.set(qn("w:fill"), hex_fill)
    cell._tc.get_or_add_tcPr().append(el)


def code(lines, fill="F4F3EF"):
    """A monospace block in a single shaded cell."""
    t = doc.add_table(rows=1, cols=1)
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    c = t.rows[0].cells[0]
    shade(c, fill)
    c.paragraphs[0].text = ""
    for i, line in enumerate(lines):
        p = c.paragraphs[0] if i == 0 else c.add_paragraph()
        line = line or " "  # keep the shading continuous across blank lines
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.space_before = Pt(0)
        r = p.add_run(line)
        r.font.name = "Consolas"
        r.font.size = Pt(9)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def fix_layout(t):
    """Make Word honour explicit column widths instead of autofitting."""
    t.autofit = False
    tblPr = t._tbl.tblPr
    for tag in ("w:tblLayout",):
        for el in tblPr.findall(qn(tag)):
            tblPr.remove(el)
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.append(layout)


def table(headers, rows, widths=None, header_fill="2F2C28"):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    trPr = t.rows[0]._tr.get_or_add_trPr()
    hdr_mark = OxmlElement("w:tblHeader")
    hdr_mark.set(qn("w:val"), "true")
    trPr.append(hdr_mark)
    hdr = t.rows[0].cells
    for i, h in enumerate(headers):
        shade(hdr[i], header_fill)
        p = hdr[i].paragraphs[0]
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.space_before = Pt(2)
        r = p.add_run(h)
        r.bold = True
        r.font.size = Pt(9.5)
        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    for ri, row in enumerate(rows):
        cells = t.add_row().cells
        for ci, val in enumerate(row):
            if ri % 2 == 1:
                shade(cells[ci], "FAF9F6")
            p = cells[ci].paragraphs[0]
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.space_before = Pt(2)
            # Backtick-delimited spans become monospace, inline or whole-cell.
            for si, seg in enumerate(str(val).split("`")):
                if not seg:
                    continue
                r = p.add_run(seg)
                if si % 2:
                    r.font.name = "Consolas"
                    r.font.size = Pt(8.5)
                else:
                    r.font.size = Pt(9)
    if widths:
        fix_layout(t)
        for ci, w in enumerate(widths):
            t.columns[ci].width = Inches(w)
            for row in t.rows:
                row.cells[ci].width = Inches(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def callout(title, body, tone=GOLD, fill="FBF6E7"):
    t = doc.add_table(rows=1, cols=1)
    c = t.rows[0].cells[0]
    shade(c, fill)
    p = c.paragraphs[0]
    p.paragraph_format.space_after = Pt(2)
    r = p.add_run(title)
    r.bold = True
    r.font.size = Pt(9.5)
    r.font.color.rgb = tone
    p2 = c.add_paragraph()
    p2.paragraph_format.space_after = Pt(2)
    r2 = p2.add_run(body)
    r2.font.size = Pt(9.5)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


# ══════════════════════════════════════════════════════════════
# Title
# ══════════════════════════════════════════════════════════════
p = para("Upande Irrigation", size=30, bold=True, space_after=2)
p = para("How a week of irrigation is planned, scheduled and executed",
         size=13, colour=MUTED, space_after=14)

rich([
    ("End-to-end process reference", {"bold": True}),
    ("  ·  3 August 2026  ·  ", {"colour": MUTED}),
    ("upande_irrigation", {"mono": True}),
    (" app, Frappe v16", {"colour": MUTED}),
], space_after=16)

para(
    "This document describes the complete irrigation planning process as it now works: "
    "what is measured, how demand is calculated for every section, how a shared pump is "
    "shared out, and how the result reaches an operator. It covers the doctypes involved, "
    "the arithmetic at each step with a fully worked example from live data, the rules "
    "under which the system refuses to plan, and what changed in the August 2026 rebuild.",
)

# ══════════════════════════════════════════════════════════════
doc.add_heading("1. The process in one page", level=1)

para(
    "Irrigation is planned one week at a time, one shift at a time. The week runs Thursday "
    "to Wednesday. The cycle has five stages, and each stage has exactly one owner."
)

table(
    ["Stage", "What happens", "Owner", "Runs"],
    [
        ["1. Measure", "A daily Weather Reading records pan evaporation, rainfall and "
         "temperature per farm. Pan cups become millimetres; ET pan becomes ET crop.",
         "`events/weather_reading.py`", "Daily, on save"],
        ["2. Qualify", "Before anything is created, the farm's elapsed week is checked for "
         "completeness. An incomplete week is refused and the reason recorded.",
         "`engine/demand.py`", "Weekly, per farm"],
        ["3. Demand", "For each shift: apply the crop coefficient, subtract rainfall, add "
         "whatever the shift still owes, convert millimetres to hours.",
         "`engine/demand.py`", "Weekly, per shift"],
        ["4. Allocate", "Group shifts by the pump that serves them. Scale every request to "
         "fit the pump's weekly capacity. Split into cycles, place them in the week.",
         "`engine/allocate.py`", "Weekly, per pump"],
        ["5. Execute", "The operator sees which shift is running now, what is queued, and "
         "what is carrying over. Unmet water becomes next week's opening debt.",
         "`api/scheduler.py`, dashboard", "Continuous"],
    ],
    widths=[1.0, 2.9, 1.5, 1.0],
)

callout(
    "The one rule that governs everything",
    "Demand is measured over the week that has already happened — never the week being "
    "planned. A plan generated on Friday for the week starting the following Thursday "
    "measures the seven days ending the day before that Thursday. Those days are settled; "
    "the planned week has not happened yet and contains no data.",
)

# ══════════════════════════════════════════════════════════════
doc.add_heading("2. The irrigation hierarchy", level=1)

para(
    "Every calculation is anchored to a position in this hierarchy. The unit that is planned "
    "and scheduled is the shift, because a shift is what a pump can actually run at one time."
)

code([
    "Farm                     Lokitela                  weather is recorded here",
    "  └─ Section             23HA_SECTION              a pump serves a section",
    "       └─ Shift          23HA - SHIFT 1            THE PLANNED UNIT",
    "            └─ Block     AIRSTRIP BLK 4 - KL       the valves that open",
])

para(
    "A section's shifts are numbered. Lokitela currently has four sections and 55 active shifts:"
)
table(
    ["Section", "Active shifts"],
    [["23HA", "7"], ["56HA", "15"], ["65HA", "14"], ["70HA", "19"], ["Total", "55"]],
    widths=[1.6, 1.4],
)

para(
    "A shift waters its own trees. It therefore needs the full weekly deficit — not a share of "
    "it. What the shifts share is the pump, and that is a constraint on delivery, not on need. "
    "Keeping those two ideas apart is the central design decision in the rebuild: demand is "
    "what the trees lost, allocation is what the infrastructure can give back.",
)

# ══════════════════════════════════════════════════════════════
doc.add_heading("3. Doctypes involved", level=1)

table(
    ["Doctype", "Role in the process", "Written by"],
    [
        ["Farm", "The estate. Carries `is_irrigation_farm`, which decides whether the "
         "scheduler visits it.", "Manual"],
        ["Warehouse (type Section)", "A section. Named `<PREFIX>_SECTION`. Links a pump "
         "profile to a group of shifts.", "Manual"],
        ["Block Type", "A shift, named `<SECTION> - SHIFT <N>`. `is_active` decides whether "
         "it is planned.", "Manual"],
        ["Blocks List (child)", "The blocks a shift waters — the valves that open.", "Manual"],
        ["Weather Reading", "One row per farm per day: pan cups, rainfall, min/max "
         "temperature. Derives ET pan and ET crop on save.", "Daily entry"],
        ["Irrigation Settings (single)", "Every coefficient and default: crop coefficient, "
         "application rate, coverage, cycle thresholds, minimum weather days.", "Manual"],
        ["Irrigation Pump Profile", "Weekly water target and pump flow rate per section. "
         "Capacity in hours is target ÷ flow.", "Manual"],
        ["Irrigation Scheduler (single)", "When the week starts, which week to plan, which "
         "farms, error tolerance.", "Manual"],
        ["Irrigation Planner", "One document per shift per week. Holds the measured window, "
         "the demand, the allocation and the outcome.", "Engine"],
        ["Irrigation Scheduler Run", "The audit record of one run: the full log, per-shift "
         "results, and every farm-week that was refused.", "Engine"],
    ],
    widths=[1.55, 3.4, 1.0],
)

callout(
    "Removed in this rebuild",
    "The Irrigation Calculation child table (field irrigation_calculations) is gone. It "
    "presented an editable per-block application rate and coverage, but those values never "
    "reached the calculation — hours always came from the farm defaults. Its client script "
    "also multiplied by coverage where the server divides, so a row's hours never matched the "
    "header's. Editable fields that change nothing are worse than no fields at all.",
    tone=RED, fill="FBEEED",
)

# ══════════════════════════════════════════════════════════════
doc.add_heading("4. Stage 1 — Measuring the week", level=1)

para(
    "A Weather Reading is entered once a day per farm. Two derived figures are computed on "
    "save, and every downstream number depends on them."
)

code([
    "pan_depth_mm =  pan_cups x depth_per_cup_mm          depth_per_cup_mm = 0.5",
    "et_pan       =  rainfall_mm + pan_depth_mm",
    "et_crop      =  et_pan x pan_to_crop_coefficient     pan_to_crop = 0.95",
])

para(
    "ET pan adds rainfall back because the pan loses water to evaporation and gains it from "
    "rain; the gross atmospheric demand is the sum. The pan-to-crop coefficient converts an "
    "open water surface into a crop canopy.",
)

rich([
    ("A temperature of 0 °C is treated as ", {}),
    ("not recorded", {"italic": True}),
    (", not as a real measurement. Averaging zeros in would drag the mean down and suppress "
     "the anthracnose index.", {}),
])

# ══════════════════════════════════════════════════════════════
doc.add_heading("5. Stage 2 — Qualifying the week", level=1)

para(
    "The seven days of the measured week are counted before any planner is created. This is "
    "one check per farm, because every shift on a farm shares the same weather."
)

code([
    "measured_to    =  plan_from - 1 day",
    "measured_from  =  measured_to - 6 days",
    "",
    "if days_with_readings < min_weather_days:   REFUSE the whole farm-week",
])

rich([
    ("The threshold is ", {}),
    ("min_weather_days", {"mono": True}),
    (" in Irrigation Settings, default 7 — so a single missed day refuses the week. A refused "
     "farm-week produces ", {}),
    ("no planners at all", {"bold": True}),
    (". The refusal and its reason are written to the Irrigation Scheduler Run.", {}),
])

callout(
    "Why refusing matters more than it sounds",
    "Under the old engine an unmeasurable week still produced a planner for every shift, and "
    "every one of them read \"No irrigation required\" — indistinguishable from a genuinely "
    "wet week. Silence and zero are different answers, and the system must not confuse them.",
)

para("The live site demonstrates both outcomes. Weather for Lokitela ends 2026-07-12:")
table(
    ["Plan week", "Measured week", "Readings found", "Outcome"],
    [
        ["2026-07-02", "2026-06-25 → 2026-07-01", "7 of 7", "Planned — 55 planners"],
        ["2026-07-09", "2026-07-02 → 2026-07-08", "6 of 7 (2026-07-03 missing)", "Refused"],
        ["2026-07-16", "2026-07-09 → 2026-07-15", "4 of 7", "Refused"],
    ],
    widths=[1.1, 1.8, 1.9, 1.6],
)

# ══════════════════════════════════════════════════════════════
doc.add_heading("6. Stage 3 — Demand, for every section", level=1)

para(
    "Demand is computed identically for every shift in every section. The inputs are the "
    "measured week's totals, the crop coefficient, and whatever the shift still owes from "
    "before."
)

doc.add_heading("6.1 The arithmetic", level=2)
code([
    "clean_et_crop   =  SUM(et_crop over measured week) x et_crop_coefficient",
    "week_deficit    =  max(0, clean_et_crop - SUM(rainfall over measured week))",
    "total_needed    =  week_deficit + carried_deficit",
    "",
    "required_hours  =  total_needed / application_rate_mm_hr / (coverage / 100)",
])

table(
    ["Term", "Meaning", "Current value"],
    [
        ["`et_crop_coefficient`", "Crop utilisation — the fraction of atmospheric demand the "
         "canopy actually transpires.", "0.65"],
        ["`application_rate_mm_hr`", "Depth the emitters apply per hour.", "2.8 mm/hr"],
        ["`coverage`", "Fraction of the block the emitters wet. Dividing by it lengthens the "
         "run, because only part of the ground receives water.", "70 %"],
        ["`carried_deficit`", "Unmet millimetres from the most recent prior planner for this "
         "shift.", "per shift"],
    ],
    widths=[1.6, 3.4, 1.0],
)

doc.add_heading("6.2 Carry-forward", level=2)
para(
    "A shift that was not given enough hours ends the week in debt. That debt opens the next "
    "week's demand. The lookup takes the most recent prior planner for the shift, ordered by "
    "date — not the planner dated exactly seven days earlier. A skipped week no longer writes "
    "the debt off."
)
para(
    "On the regenerated 27 weeks the debt behaves as it should: it builds through the dry "
    "start of the year to a peak of 97.3 mm in mid-February, then drains as wetter weeks "
    "arrive and the granted hours chip away at it. It does not run away."
)

doc.add_heading("6.3 Disease risk, on the same window", level=2)
para(
    "The anthracnose index is computed from the same measured week, so it can never disagree "
    "with the irrigation figures about which days it is describing."
)
code([
    "Z  =  -58.99 + (3.22 x mean_temp) + (0.18 x weekly_rainfall)",
    "",
    "Z >= 20   High Risk - Fungicide Required",
    "Z >= 15   Infection Risk - Monitor Closely",
    "Z >=  5   Spore Release - Low Alert",
    "else      Low Risk",
])

# ══════════════════════════════════════════════════════════════
doc.add_heading("7. Stage 4 — Allocating the pump", level=1)

para(
    "Demand is what the trees lost. Allocation is what the pump can return. A single shift "
    "cannot compute this, because the answer depends on every other shift drawing on the same "
    "pump — which is why allocation is the scheduler's job and not the planner's."
)

doc.add_heading("7.1 Capacity", level=2)
code([
    "capacity_hours  =  water_target_m3_per_week / pump_flow_rate_m3_per_hr",
    "",
    "no profile, or either figure missing:  168 hr/wk, flagged UNVERIFIED",
])
rich([
    ("With no Irrigation Pump Profile there is no capacity to allocate against. The engine "
     "assumes a full week and says so on every affected planner. It never presents an assumed "
     "number as a limit. ", {}),
    ("The live site currently has zero pump profiles, so this is the active path.",
     {"bold": True}),
])

doc.add_heading("7.2 Grouping", level=2)
para(
    "Shifts are grouped by the pump named in their section's profile. Sections sharing a pump "
    "are sequenced against one capacity and one timeline. Sections with no profile share a "
    "single unknown-pump group — the pessimistic reading, because an unknown pump might be one "
    "pump, and scheduling them concurrently could overdraw a main that cannot carry it."
)

doc.add_heading("7.3 Proportional capping", level=2)
code([
    "total    =  SUM(required_hours across the pump's shifts)",
    "factor   =  capacity_hours / total        (only when total > capacity)",
    "granted  =  required_hours x factor",
])
para(
    "Every shift is scaled by the same factor, so shares stay proportional to need and a "
    "shift needing nothing reserves nothing. The old rule divided capacity by the number of "
    "shifts, which handed an idle shift the same slice as a parched one and then discarded "
    "the unused slice."
)

doc.add_heading("7.4 Cycles and placement", level=2)
code([
    "count  =  ceil(granted / auto_cycle_threshold_hrs)   floored at cycles_when_above",
    "          or cycles_when_below when granted <= threshold",
    "each   =  granted / count",
    "",
    "cycles are separated by cycle_rest_hours          default 2.0 hr",
    "shifts on one pump are placed end to end, never overlapping",
    "a cycle that will not fit inside the week is not placed - it carries",
])
para(
    "Splitting a long run lets water infiltrate instead of running off. The old engine placed "
    "cycles back to back, which is not cycling, and anchored every section at hour 0, so "
    "sections ran concurrently while capacity was being capped per section."
)

# ══════════════════════════════════════════════════════════════
doc.add_heading("8. Worked example — 23HA · SHIFT 1", level=1)

rich([
    ("Live record ", {}),
    ("IRPL-2026-01019", {"mono": True}),
    (", plan week 2026-01-08 → 2026-01-14, farm Lokitela. Every figure below is read from "
     "the database, not constructed.", {}),
])

table(
    ["Step", "Calculation", "Result"],
    [
        ["Measured window", "plan_from 2026-01-08, minus one day, back seven days",
         "2026-01-01 → 2026-01-07"],
        ["Completeness", "readings found vs min_weather_days", "7 of 7 — proceed"],
        ["Weather totals", "summed over the measured week",
         "rain 0.00 mm, ET pan 34.50 mm, ET crop 32.775 mm"],
        ["Clean ET crop", "32.775 × 0.65", "21.3038 mm"],
        ["Week deficit", "max(0, 21.3038 − 0.00)", "21.3038 mm"],
        ["Carried debt", "most recent prior planner for this shift", "0.00 mm — first week"],
        ["Total needed", "21.3038 + 0.00", "21.3038 mm"],
        ["Required hours", "21.3038 ÷ 2.8 ÷ 0.70", "10.87 hr"],
        ["Pump capacity", "no profile for this section", "168 hr/wk, unverified"],
        ["Capping factor", "168 ÷ (55 shifts × 10.87 hr) = 168 ÷ 597.8", "0.281"],
        ["Granted hours", "10.87 × 0.281", "3.05 hr"],
        ["Cycles", "3.05 ≤ 5.0 hr threshold → cycles_when_below", "1 × 3.05 hr"],
        ["Window", "first in the unknown-pump queue",
         "2026-01-08 00:00 → 03:03"],
        ["Delivered depth", "3.05 × 2.8 × 0.70", "5.99 mm"],
        ["Unmet", "21.3038 − 5.99", "15.32 mm — carries to 2026-01-15"],
        ["Disease index", "Z from mean temp and 0.00 mm rain",
         "5.18 — Spore Release, Low Alert"],
    ],
    widths=[1.25, 2.65, 2.1],
)

callout(
    "What this record is telling the farm",
    "23HA · SHIFT 1 lost 21.3 mm and received 6.0 mm. It is not a scheduling error — it is a "
    "capacity statement. With 55 shifts sharing one assumed 168 hr week, the farm can return "
    "about 28 % of what it loses in a dry week. Creating the Irrigation Pump Profiles is what "
    "turns that assumption into a measured fact, and may well raise the ceiling.",
)

# ══════════════════════════════════════════════════════════════
doc.add_heading("9. What each field means and who writes it", level=1)

para(
    "Demand fields and allocation fields are written by different stages. Opening a scheduled "
    "planner and pressing Save recomputes the demand block only — it no longer overwrites the "
    "scheduler's allocation, which was a defect in the old engine."
)

table(
    ["Field", "Meaning", "Written by"],
    [
        ["`measured_from` / `measured_to`", "The elapsed week the demand came from.", "Planner hook"],
        ["`weather_completeness`", "\"7 of 7 days logged\".", "Planner hook"],
        ["`weekly_rainfall_mm`, `weekly_et_pan_mm`, `raw_et_crop_mm`",
         "Measured-week totals.", "Planner hook"],
        ["`clean_et_crop_mm`", "ET crop after the crop coefficient.", "Planner hook"],
        ["`this_week_deficit`", "Clean ET crop minus rainfall, floored at zero.", "Planner hook"],
        ["`carried_deficit_mm`", "Debt opened from the prior planner.", "Planner hook"],
        ["`total_water_needed_mm`", "This week plus carried.", "Planner hook"],
        ["`required_hours`", "Hours to meet demand, before any cap.", "Planner hook"],
        ["`z_value`, `z_risk_level`", "Anthracnose index and band.", "Planner hook"],
        ["`shift_hours`", "Hours actually granted by the allocator.", "Scheduler"],
        ["`cycles_count`, `cycle_hours_each`, `cycle_plan`", "How the run is split.", "Scheduler"],
        ["`scheduled_start` / `scheduled_end`", "First and last moment of the run.", "Scheduler"],
        ["`delivered_depth_mm`", "Millimetres the granted hours deliver.", "Scheduler"],
        ["`unmet_deficit_mm`", "Shortfall — becomes next week's carried debt.", "Scheduler"],
        ["`capacity_warning`", "Why the grant fell short, in plain words.", "Scheduler"],
    ],
    widths=[2.1, 2.9, 1.0],
)

# ══════════════════════════════════════════════════════════════
doc.add_heading("10. Operating the system", level=1)

doc.add_heading("10.1 Weekly cadence", level=2)
numbered("Enter a Weather Reading every day. This is the single input the whole model rests on; "
         "a missing day refuses that farm's next plan.")
numbered("The scheduler runs automatically on Friday at 06:00 (cron 0 6 * * 5), planning the "
         "week set in Irrigation Scheduler.")
numbered("Review the Irrigation Scheduler Run: it lists every planner created, every farm-week "
         "refused and why, and the capacity warning on each shift.")
numbered("Operators work from the dashboard at /upande-irrigation — what is running now, what "
         "is queued, and what is carrying over.")

doc.add_heading("10.2 Running it by hand", level=2)
code([
    "# plan the configured week now",
    "bench --site <site> execute upande_irrigation.api.scheduler.run \\",
    "      --kwargs \"{'triggered_by':'Manual'}\"",
    "",
    "# plan one specific week",
    "bench --site <site> execute upande_irrigation.api.scheduler.run \\",
    "      --kwargs \"{'for_week':'2026-01-08'}\"",
    "",
    "# regenerate a range of historical weeks, one run each",
    "bench --site <site> execute upande_irrigation.api.scheduler.backfill \\",
    "      --kwargs \"{'from_week':'2026-01-01','to_week':'2026-07-16'}\"",
])

doc.add_heading("10.3 Settings that change the outcome", level=2)
table(
    ["Setting", "Effect", "Current"],
    [
        ["`min_weather_days`", "How complete a week must be before it can be planned.", "7"],
        ["`default_avocado_utilisation`", "Crop coefficient applied to ET crop.", "0.65"],
        ["`default_application_rate_mm_hr`", "Emitter output — divides the deficit into hours.", "2.8"],
        ["`default_irrigation_coverage`", "Wetted fraction — lengthens the run.", "70 %"],
        ["`auto_cycle_threshold_hrs`", "Longest single cycle before a run is split.", "5.0"],
        ["`cycle_rest_hours`", "Gap between a shift's cycles.", "2.0"],
        ["`organisation_name`", "Estate name shown in the dashboard header.", "Kaitet Group"],
    ],
    widths=[2.2, 2.8, 1.0],
)

# ══════════════════════════════════════════════════════════════
doc.add_heading("11. What changed, and the evidence", level=1)

para(
    "The review examined 836 planners across 29 weeks. 819 of them — 98 % — instructed the "
    "farm not to irrigate. The arithmetic was never wrong; it was aimed at the wrong week."
)

table(
    ["Defect", "Evidence", "Fix"],
    [
        ["Demand measured the week being planned, which had not happened yet. "
         "`DATEDIFF(from_date, creation) = 6` on every batch.",
         "819 of 836 planners had zero hours; 709 were computed against \"0 of 7 days "
         "logged\".",
         "Demand now measures the seven days ending the day before the plan week."],
        ["An unmeasurable week still produced planners reading \"No irrigation required\".",
         "Indistinguishable from a genuinely wet week.",
         "The farm-week is refused and the reason recorded."],
        ["The Before Save hook computed both demand and allocation, so saving a scheduled "
         "planner overwrote the grant.",
         "shift_hours recomputed uncapped on every save.",
         "Hook writes demand only; scheduler writes allocation."],
        ["The migrated Server Scripts were never retired, so two engines ran.",
         "\"Irrigation Planner — Compute Shift\" was enabled alongside the Python hook, and "
         "the old cron fired at the same 0 6 * * 5.",
         "All five superseded Server Scripts disabled by patch."],
        ["Capacity divided equally across shifts.",
         "An idle shift reserved as much as a parched one; the unused slice was discarded.",
         "Proportional scaling by capacity ÷ total demand."],
        ["Every section anchored at hour 0 while capping was per-section.",
         "Sections sharing a pump each assumed the whole week.",
         "Shifts grouped by pump and sequenced in one queue."],
        ["Cycles ran back to back.", "No infiltration gap — not cycling at all.",
         "Cycles separated by cycle_rest_hours."],
        ["Carry-forward required to_date = from_date − 1 exactly.",
         "One skipped week wrote the debt off silently.",
         "Most-recent-prior-planner lookup, ordered by date."],
        ["Per-block overrides never affected the result.",
         "Row mm_hr never reached the calculation; the client script multiplied by coverage "
         "where the server divides.",
         "Child table and its doctype removed."],
    ],
    widths=[2.0, 2.2, 1.8],
)

doc.add_heading("11.1 Before and after, on the same site", level=2)
table(
    ["Measure", "Before", "After"],
    [
        ["Planners", "836 across 29 weeks", "1485 across 27 weeks"],
        ["With real demand", "17 (2 %)", "1430 (96 %)"],
        ["Computed against no weather", "709", "0 — those weeks are refused"],
        ["Weeks refused for incomplete data", "0", "2 (2026-07-09, 2026-07-16)"],
        ["Peak carried debt", "not tracked meaningfully", "97.3 mm mid-February, then drains"],
        ["Automated tests", "none", "52, passing"],
    ],
    widths=[2.2, 1.9, 1.9],
)

# ══════════════════════════════════════════════════════════════
doc.add_heading("12. Known limits and next steps", level=1)

bullet("No Irrigation Pump Profile exists on the live site. Until one is created per section, "
       "capacity is an assumed 168 hr/wk, every planner carries an \"unverified\" warning, and "
       "all sections are sequenced as though they share one pump. This is the single highest-"
       "value data entry task outstanding.")
bullet("Every shift on a farm currently produces identical demand, because nothing in the "
       "model is shift-specific — no per-shift area, crop age or coefficient. Proportional "
       "capping therefore degenerates to equal shares. Adding per-shift area would make the "
       "allocation genuinely differentiated.")
bullet("Weather data ends 2026-07-12, so the current week cannot be planned. The dashboard "
       "reports this as an alert rather than showing zeros.")
bullet("Phase 2 of the rebuild covers visualisation: the week Gantt, pump load, water balance "
       "and a calculation explainer that shows an operator exactly how a shift's hours were "
       "derived.")
bullet("Carry-forward is uncapped by design. It drains correctly on the current data, but a "
       "physical ceiling tied to root-zone holding capacity would be more defensible if a farm "
       "ever ran a long structural shortfall.")

# ══════════════════════════════════════════════════════════════
doc.add_heading("Appendix — Source map", level=1)

table(
    ["File", "Responsibility"],
    [
        ["`upande_irrigation/engine/demand.py`", "Pure. Measured window, aggregation, "
         "plannability, deficit, required hours, anthracnose index."],
        ["`upande_irrigation/engine/allocate.py`", "Pure. Pump capacity, proportional "
         "granting, cycle planning, window placement."],
        ["`upande_irrigation/events/irrigation_planner.py`", "Before Save adapter. Fetches "
         "readings, writes the demand block, carry-forward and persistence streak."],
        ["`upande_irrigation/api/scheduler.py`", "Run and backfill. Refusal, pump grouping, "
         "allocation, warnings, audit record, live_sections."],
        ["`upande_irrigation/api/overview.py`", "Dashboard read model: tiles, alerts, schedule "
         "grid, deficit trend."],
        ["`upande_irrigation/tests/`", "52 tests. Each module's docstring names the defect it "
         "locks out."],
        ["`upande_irrigation/patches/v1_0/add_engine_fields.py`", "Adds required_hours and the "
         "measured window; drops the per-block table."],
        ["`upande_irrigation/patches/v1_0/retire_superseded_server_scripts.py`",
         "Disables the five Server Scripts the Python engine replaced."],
    ],
    widths=[2.7, 3.3],
)

para(
    "Run the tests with:", space_after=4,
)
code(["bench --site <site> run-tests --app upande_irrigation"])

OUT.parent.mkdir(parents=True, exist_ok=True)
doc.save(OUT)
print(f"wrote {OUT}  ({OUT.stat().st_size / 1024:.1f} KiB)")
