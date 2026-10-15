/* Weather — one card for every daily weather measure.
 *
 * Kept deliberately simple: a measure, a farm, a year and one year to compare
 * it with. The year always reads Jan – Dec, one slot per month, on a
 * graph-paper chart (titled axes, labelled major grid, fine minor grid) so a
 * value can be read straight off it. The numbers come from
 * api.weather_series.series, which lines the comparison year up on the chosen
 * year's calendar.
 *
 * Totals (rain, evaporation) draw the year as bars and the comparison as a
 * dashed line; levels (temperature, deficit) draw as lines. A month with no
 * reading is a gap, never a zero, and the summary says how many days were read.
 *
 * Expected rainfall (Irrigation Settings, switchable): a month's bar turns
 * green where it met its farm's expected mm and red where it fell short, with
 * a tick at the target; the month still running is faded and judged on its
 * days so far.
 */

const MEASURES = [
	["rainfall", "Rainfall", "var(--ui-rain)"],
	["temperature", "Temperature", "var(--ui-hot)"],
	["evaporation", "Pan evaporation", "var(--ui-eto)"],
	["deficit_blocks", "Soil deficit · block balance", "var(--ui-cool)"],
	["deficit_swd", "Soil deficit · old SWD", "var(--ui-cool)"],
];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/* the comparison year in warm grey and dashed: identity never rests on colour
 * alone -- every series is in the legend, the tooltip and the table */
const COMPARE_COLOR = "#8a8780";
const MET_COLOR = "var(--ui-ok)";
const BELOW_COLOR = "var(--ui-hot)";

export const weatherExplorer = {
	markup() {
		return `
<div class="card wxx" id="wxx-card">
	<div class="card__head"><h3>Weather</h3><span class="meta" id="wxx-meta"></span></div>
	<div class="wxx-controls">
		<label class="wxx-f"><span>Show</span><select class="select" id="wxx-measure">${MEASURES.map(
			(m) => `<option value="${m[0]}">${m[1]}</option>`
		).join("")}</select></label>
		<label class="wxx-f"><span>Farm</span><select class="select" id="wxx-farm"></select></label>
		<label class="wxx-f wxx-f--year"><span>Year</span><select class="select" id="wxx-year"></select></label>
		<label class="wxx-f wxx-f--year"><span>Compare with</span><select class="select" id="wxx-compare"></select></label>
		<div class="wxx-right">
			<button type="button" class="btn ghost small" id="wxx-table-btn">Table</button>
			<button type="button" class="btn ghost small" id="wxx-csv">CSV</button>
		</div>
	</div>
	<div id="wxx-chart"></div>
	<div class="clegend" id="wxx-legend"></div>
	<div class="wxx-sum" id="wxx-sum"></div>
	<div id="wxx-notes"></div>
	<div class="tablewrap scroll" id="wxx-table" hidden></div>
</div>`;
	},

	init(host, ctx) {
		this.host = host;
		this.ctx = ctx;
		const thisYear = new Date().getFullYear();
		this.s = {
			measure: "rainfall", farm: ctx.filters.farm || "", farmTouched: false,
			// compare: null = the year before, "" = none, else a year
			year: thisYear, compare: null, years: [thisYear],
			table: false, seq: 0, data: null,
		};
		const $ = (id) => host.querySelector(id);
		$("#wxx-measure").onchange = (e) => { this.s.measure = e.target.value; this.load(); };
		$("#wxx-farm").onchange = (e) => { this.s.farm = e.target.value; this.s.farmTouched = true; this.load(); };
		$("#wxx-year").onchange = (e) => {
			this.s.year = Number(e.target.value);
			if (this.s.compare === this.s.year) this.s.compare = null;
			this.load();
		};
		$("#wxx-compare").onchange = (e) => {
			const v = e.target.value;
			this.s.compare = v === "" ? "" : Number(v);
			this.load();
		};
		$("#wxx-table-btn").onclick = () => { this.s.table = !this.s.table; this.draw(); };
		$("#wxx-csv").onclick = () => this.csv();
		this.syncControls();
	},

	/* the dashboard's farm list and its farm filter, when they change */
	setFarms(farms, globalFarm) {
		this.farms = (farms || []).map((f) => (typeof f === "string" ? f : f.name)).filter(Boolean);
		if (!this.s.farmTouched) this.s.farm = globalFarm || "";
		const sel = this.host.querySelector("#wxx-farm");
		const { esc } = this.ctx.charts;
		sel.innerHTML = `<option value="">All farms</option>${this.farms
			.map((f) => `<option value="${esc(f)}"${f === this.s.farm ? " selected" : ""}>${esc(f)}</option>`)
			.join("")}`;
	},

	compareYear() {
		const s = this.s;
		return s.compare == null ? s.year - 1 : s.compare;
	},

	async load() {
		const s = this.s;
		const seq = ++s.seq;
		const compare = this.compareYear();
		this.syncControls();
		const chart = this.host.querySelector("#wxx-chart");
		chart.style.opacity = ".5";
		let data;
		try {
			({ data } = await this.ctx.api.get("upande_irrigation.api.weather_series.series", {
				measure: s.measure, farm: s.farm, from_date: `${s.year}-01-01`, to_date: `${s.year}-12-31`,
				granularity: "month", compare_years: compare === "" ? "" : String(compare), long_term: 0,
			}));
		} catch (err) {
			if (seq !== s.seq) return;
			chart.style.opacity = "";
			chart.innerHTML = `<div class="empty small">Could not load ${this.ctx.charts.esc(s.measure)}: ${this.ctx.charts.esc(err.message)}</div>`;
			return;
		}
		if (seq !== s.seq) return;
		chart.style.opacity = "";
		s.data = data;
		s.years = [...new Set([new Date().getFullYear(), s.year, ...(data.years_available || [])])].sort((a, b) => b - a);
		this.draw();
	},

	syncControls() {
		const s = this.s;
		const $ = (id) => this.host.querySelector(id);
		$("#wxx-measure").value = s.measure;
		$("#wxx-year").innerHTML = s.years.map((y) => `<option value="${y}"${y === s.year ? " selected" : ""}>${y}</option>`).join("");
		const cmp = this.compareYear();
		const others = s.years.filter((y) => y !== s.year);
		// the year before stays offered even with no readings, so the default reads honestly
		if (cmp !== "" && !others.includes(cmp)) others.push(cmp);
		others.sort((a, b) => b - a);
		$("#wxx-compare").innerHTML = `<option value=""${cmp === "" ? " selected" : ""}>No comparison</option>`
			+ others.map((y) => `<option value="${y}"${y === cmp ? " selected" : ""}>${y}</option>`).join("");
		$("#wxx-table-btn").classList.toggle("on", s.table);
	},

	/* series ready to plot, colours assigned */
	plotted() {
		const d = this.s.data;
		const color = (MEASURES.find((m) => m[0] === d.measure) || [])[2] || "var(--ui-rain)";
		return d.series.map((ser) => {
			const isCur = ser.key === "current";
			return { ...ser, isCur, color: isCur ? color : COMPARE_COLOR, dash: isCur ? "" : "6 4" };
		});
	},

	/* expected rainfall per month, ready for the chart; null when off */
	expected() {
		const d = this.s.data;
		const ex = d.expected;
		if (!ex || d.kind !== "total" || !ex.some(Boolean)) return null;
		const fmt = (v) => this.ctx.charts.fmtNum(v, 1);
		const cur = d.series.find((x) => x.key === "current");
		return {
			targets: ex.map((e) => (e ? e.mm : null)),
			colors: ex.map((e) => (!e || !e.status ? null : e.status === "met" ? MET_COLOR : BELOW_COLOR)),
			faded: ex.map((e) => !!(e && e.open)),
			tips: ex.map((e, i) => {
				if (!e) return "";
				const v = cur && cur.values[i];
				const part = e.days < e.of_days ? ` (${e.days} of ${e.of_days} days)` : "";
				const pc = v != null && e.mm > 0 ? ` · ${Math.round((v / e.mm) * 100)}%` : "";
				return `Expected ${fmt(e.mm)} mm${part}${pc}${e.open ? " · still running" : ""}`;
			}),
			any: (st) => ex.some((e) => e && e.status === st),
			anyOpen: ex.some((e) => e && e.open),
		};
	},

	draw() {
		const s = this.s;
		const d = s.data;
		if (!d) return;
		this.syncControls();
		const { charts } = this.ctx;
		const $ = (id) => this.host.querySelector(id);
		const unit = d.unit;
		const series = this.plotted();
		const cur = series.find((x) => x.isCur);
		const cmp = series.filter((x) => !x.isCur);
		const chart = $("#wxx-chart");
		const pick = (vals, f) => vals.map((v) => (v == null ? null : f(v)));
		const ex = this.expected();
		const base = {
			labels: MONTHS, unit: ` ${unit}`, dec: unit === "mm" ? 1 : 1,
			yTitle: `${d.label} (${unit})`, xTitle: `Month, ${s.year}`,
		};
		const legend = [];
		const sw = (x, line) => `<i class="${line ? "ln" : ""}" style="background:${x.color}${x.dash ? ";opacity:.8" : ""}"></i>`;
		const note = (x) => (!x.values.some((v) => v != null) ? ' <em class="wxx-muted">(no readings)</em>' : "");

		if (d.kind === "total") {
			charts.graphPaper(chart, {
				...base,
				bars: { label: cur.label, color: cur.color, values: cur.values, ...(ex || {}) },
				lines: cmp.map((x) => ({ label: x.label, color: x.color, values: x.values, dash: x.dash })),
			});
			legend.push(`<span>${sw(cur, false)}${charts.esc(cur.label)}${note(cur)}</span>`);
		} else if (d.measure === "temperature") {
			charts.graphPaper(chart, {
				...base,
				band: { lo: pick(cur.values, (v) => v.min), hi: pick(cur.values, (v) => v.max), color: cur.color, label: `${cur.label} min–max` },
				lines: [
					...cmp.map((x) => ({ label: `${x.label} mean`, color: x.color, values: pick(x.values, (v) => v.mean), dash: x.dash })),
					{ label: `${cur.label} mean`, color: cur.color, values: pick(cur.values, (v) => v.mean), width: 3 },
				],
			});
			legend.push(`<span>${sw(cur, true)}${charts.esc(cur.label)} mean${note(cur)}</span>`,
				`<span><i style="background:${cur.color};opacity:.18"></i>${charts.esc(cur.label)} min–max</span>`);
		} else if (d.measure === "deficit_blocks") {
			charts.graphPaper(chart, {
				...base,
				lines: [
					...cmp.map((x) => ({ label: `${x.label} farm average`, color: x.color, values: pick(x.values, (v) => v.mean), dash: x.dash })),
					{ label: `${cur.label} farm average`, color: cur.color, values: pick(cur.values, (v) => v.mean), width: 3 },
					{ label: `${cur.label} driest block`, color: "var(--ui-hot)", values: pick(cur.values, (v) => v.driest), dash: "5 3" },
				],
			});
			legend.push(`<span>${sw(cur, true)}${charts.esc(cur.label)} farm average${note(cur)}</span>`,
				`<span><i class="ln" style="background:var(--ui-hot)"></i>Driest block</span>`);
		} else {
			charts.graphPaper(chart, {
				...base,
				lines: [
					...cmp.map((x) => ({ label: x.label, color: x.color, values: x.values, dash: x.dash })),
					{ label: cur.label, color: cur.color, values: cur.values, width: 3 },
				],
			});
			legend.push(`<span>${sw(cur, true)}${charts.esc(cur.label)}${note(cur)}</span>`);
		}
		cmp.forEach((x) => legend.push(`<span>${sw(x, true)}${charts.esc(x.label)}${note(x)}</span>`));
		if (ex) {
			if (ex.any("met")) legend.push(`<span><i style="background:${MET_COLOR}"></i>Met expected</span>`);
			if (ex.any("below")) legend.push(`<span><i style="background:${BELOW_COLOR}"></i>Below expected</span>`);
			if (ex.anyOpen) legend.push(`<span><i class="open" style="background:var(--ink-4)"></i>Month still running</span>`);
			legend.push(`<span><i class="ln tick"></i>Expected</span>`);
		}
		$("#wxx-legend").innerHTML = legend.join("");

		this.drawSummary(series);
		$("#wxx-notes").innerHTML = (d.notes || [])
			.map((n) => `<div class="alert ${/Ignored/.test(n) ? "warn" : "muted"}">${charts.esc(n)}</div>`)
			.join("");
		$("#wxx-meta").textContent = `${s.year} · ${d.farm || "all farms"} · ${d.unit}`;
		this.drawTable(series);
	},

	/* one sentence: the year's figure, the comparison against it, coverage */
	drawSummary(series) {
		const { charts } = this.ctx;
		const d = this.s.data;
		const cur = series.find((x) => x.isCur);
		const what = d.kind === "total" ? "total" : "average";
		// whole numbers with separators for big totals (1,082 mm, not 1.1k)
		const dec = Math.abs(cur.headline || 0) >= 100 || !(d.unit === "mm" || d.unit === "°C") ? 0 : 1;
		const fmtv = (v) => (v == null ? "—" : `${Number(v).toLocaleString("en-GB", { maximumFractionDigits: dec, minimumFractionDigits: dec })} ${d.unit}`);
		const parts = [`<b>${charts.esc(d.label)}</b>, ${what} ${charts.esc(cur.label)}: <b>${fmtv(cur.headline)}</b>`];
		const partial = cur.coverage != null && cur.coverage < 0.98;
		series.filter((x) => !x.isCur).forEach((x) => {
			if (x.headline == null) {
				parts.push(`${charts.esc(x.label)}: <span class="wxx-muted">no readings</span>`);
				return;
			}
			// compare like for like: where this year has gaps, the other year is
			// measured over the same days that were read here
			const ref = partial && x.headline_same_days != null ? x.headline_same_days : x.headline;
			let ch = "";
			if (ref && cur.headline != null) {
				const pc = ((cur.headline - ref) / Math.abs(ref)) * 100;
				ch = ` <span class="${pc >= 0 ? "wxx-up" : "wxx-down"}">(${pc >= 0 ? "+" : "−"}${Math.abs(pc).toFixed(0)}%${partial ? " on the same days" : ""})</span>`;
			}
			parts.push(`${charts.esc(x.label)}: ${fmtv(partial ? ref : x.headline)}${partial ? ' <span class="wxx-muted">same days</span>' : ""}${ch}`);
		});
		if (cur.coverage != null) {
			parts.push(cur.coverage < 0.9
				? `<span class="wxx-down">readings on ${Math.round(cur.coverage * 100)}% of days${d.kind === "total" ? " — the total understates" : ""}</span>`
				: `readings on ${Math.round(cur.coverage * 100)}% of days`);
		}
		this.host.querySelector("#wxx-sum").innerHTML = parts.join(" · ");
	},

	/* the same numbers as a table, which is also what the CSV holds */
	rows(series) {
		const d = this.s.data;
		const head = ["Month"];
		series.forEach((x) => {
			if (d.measure === "temperature") head.push(`${x.label} min`, `${x.label} mean`, `${x.label} max`);
			else if (d.measure === "deficit_blocks") head.push(`${x.label} average`, `${x.label} driest`, "Driest block");
			else head.push(`${x.label} (${d.unit})`);
			if (x.isCur && d.expected) head.push(`Expected (${d.unit})`);
		});
		const body = MONTHS.map((lab, i) => {
			const row = [lab];
			series.forEach((x) => {
				const v = x.values[i];
				if (d.measure === "temperature") row.push(v ? v.min : null, v ? v.mean : null, v ? v.max : null);
				else if (d.measure === "deficit_blocks") row.push(v ? v.mean : null, v ? v.driest : null, v ? v.driest_block : null);
				else row.push(v);
				if (x.isCur && d.expected) row.push(d.expected[i] ? d.expected[i].mm : null);
			});
			return row;
		});
		return { head, body };
	},

	drawTable(series) {
		const host = this.host.querySelector("#wxx-table");
		host.hidden = !this.s.table;
		if (!this.s.table) return;
		const { charts } = this.ctx;
		const { head, body } = this.rows(series);
		const cell = (v) => (v == null ? '<td class="num wxx-muted">—</td>' : typeof v === "number" ? `<td class="num">${charts.fmtNum(v, 1)}</td>` : `<td>${charts.esc(v)}</td>`);
		host.innerHTML = `<table class="table"><thead><tr>${head.map((h, i) => `<th${i ? ' class="num"' : ""}>${charts.esc(h)}</th>`).join("")}</tr></thead>
<tbody>${body.map((r) => `<tr><td><b>${charts.esc(r[0])}</b></td>${r.slice(1).map(cell).join("")}</tr>`).join("")}</tbody></table>`;
	},

	csv() {
		const d = this.s.data;
		if (!d) return;
		const { head, body } = this.rows(this.plotted());
		const q = (v) => (v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
		const text = [head, ...body].map((r) => r.map(q).join(",")).join("\r\n");
		const a = document.createElement("a");
		a.href = URL.createObjectURL(new Blob(["﻿" + text], { type: "text/csv;charset=utf-8" }));
		a.download = `${d.measure}-${d.farm || "all-farms"}-${this.s.year}.csv`;
		document.body.appendChild(a);
		a.click();
		setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 0);
	},
};
