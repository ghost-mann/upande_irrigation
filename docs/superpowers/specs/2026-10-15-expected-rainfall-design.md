# Expected rainfall on the weather card

Date: 2026-10-15. Status: approved in chat ("finish").

## Goal

Farm managers set how much rain they expect at each farm, month by month, and
the weather card's rainfall bars say at a glance whether a period met it:
green when it did, red when it fell short. One switch turns the whole thing
off, and with it off the card is exactly as before.

## Decisions (from the brainstorm)

| Question | Answer |
|---|---|
| What is the number? | Expected **mm per day**, per farm, per calendar month. The same twelve figures every year. |
| Day view | Bars keep the rain colour; a dashed stepped line shows the daily target. Green/red only on week, month and year bars. |
| The period still running | Compared against the days so far, and drawn faded/outlined so it reads as open. |
| Entry layout | One row per farm, twelve month columns, in Irrigation Settings. |
| Measures | Rainfall only. |

## Configuration (Irrigation Settings → "Expected rainfall")

- `rain_expected_enabled` (Check, default 0): "Colour rainfall against expected".
- `rain_expected` (Table → new child doctype **Irrigation Expected Rainfall**):
  `farm` (Link Farm, required), `jan` … `dec` (Float, mm/day).
- Validation: one row per farm; no negative figures.
- **0 means "no target"**. Frappe stores a blank Float as 0, so blank and 0 cannot
  be told apart; a target of 0 mm/day would also make every bar green. A month
  without a target leaves its bars in the rain colour.

## Server (`api/weather_series.py`)

`series()` adds `expected` to the response when the measure is rainfall and the
switch is on (otherwise `expected` is `None`): a list aligned with `buckets`,
each item `None` or

```
{"mm": 75.0, "days": 15, "of_days": 31, "open": true, "status": "met" | "below" | null}
```

- A day's target is its farm's figure for that day's month. "All farms" uses the
  mean of the farms that have a figure for that month.
- A bucket's target adds up the daily targets of its days, clipped to the chosen
  range and to today (`days` of `of_days`). A week across 30 Apr – 6 May takes one
  April day and six May days.
- If any counted day has no target, the bucket has none (`None`).
- A bucket that holds today is `open`. A bucket wholly after today is `None`.
- `status` compares the current series' bucket total against `mm`;
  it is `null` when the bucket has no readings (a gap stays a gap, never "dry").

## Chart (`weather_explorer.js`, `charts.js`)

- Week/month/year, bars (not running totals): the current series' bars take
  `--ui-ok` (met) or `--ui-hot` (below); an open bucket's bar is faded with a
  dashed outline. A dark tick across each bar marks the target. Comparison and
  long-term-average bars keep their colours.
- Day: bars keep the rain colour; the targets draw as a dashed stepped line.
- Tooltip / title: "… · expected 75 mm (15 of 31 days) · 231%".
- Legend adds "Met expected", "Below expected", "Still open", "Expected"; the
  table and CSV gain an "Expected (mm)" column.
- Both renderers (`groupedBars` for ≤ 36 buckets, `mkChart` beyond) support
  per-bar colours, faded bars and target ticks.

## Testing

`tests/test_weather_series.py`: month-boundary weeks, an open period, a range
clipped mid-month, all-farm averaging, a month with no target, the switch off,
the status, and settings validation. Then a browser check on webstore.localhost.

## Out of scope

Temperature/evaporation targets, per-year targets, alerts.

## Revision (same day): one year against one year

The card's controls confused users, so they were cut to **Show, Farm, Year,
Compare with** (one year, default the year before, or none). Day/week/year
grouping, the date range and presets, the year chips, the long-term average and
running totals are gone; the card always reads Jan – Dec by month on a
graph-paper chart (`charts.graphPaper`: titled axes, labelled major grid, fine
minor grid). The chosen year draws as bars (green/red against expected), the
comparison year as a dashed line. The day-view target line and the per-bar
options added to `groupedBars`/`mkChart` were removed with it. Coverage now
counts only days up to today.
