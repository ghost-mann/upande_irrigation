/* Weather — one card for every daily weather measure.
 *
 * Replaced four fixed charts (rainfall, temperature, pan evaporation, soil
 * deficit). A dropdown picks the measure; farm, period, day/week/month/year and
 * the years to compare against are the same for all of them. The numbers come
 * from api.weather_series.series, which buckets the days and lines comparison
 * years up on this year's calendar dates.
 *
 * Totals (rain, evaporation) draw as bars, or as running
 * totals; levels (temperature, deficit) draw as lines. A bucket with no reading
 * is a gap, never a zero, and every series says how many of its days were read.
 *
 * Expected rainfall (Irrigation Settings, switchable): week, month and year
 * bars turn green where the period met its farm's expected mm and red where it
 * fell short, with a tick at the target; a period still running is faded and
 * judged on its days so far. Day bars keep the rain colour under a dashed
 * line at the daily target.
 */

const MEASURES = [
	["rainfall", "Rainfall", "var(--ui-rain)"],
	["temperature", "Temperature", "var(--ui-hot)"],
	["evaporation", "Pan evaporation", "var(--ui-eto)"],
	["deficit_blocks", "Soil deficit · block balance", "var(--ui-cool)"],
	["deficit_swd", "Soil deficit · old SWD", "var(--ui-cool)"],
];
const PRESETS = [["30d", "Last 30 days"], ["month", "This month"], ["ytd", "Year to date"], ["12m", "Last 12 months"]];
const GRANS = [["day", "Day"], ["week", "Week"], ["month", "Month"], ["year", "Year"]];
/* Comparison years in warm greys, darkest for the nearest year; the long-term
 * average dark and dotted. Colour never carries identity alone: every series
 * is in the legend, the tooltip and the table. */
const COMPARE_COLORS = ["#8a8780", "#b3b0a8", "#cfccc4", "#a39a8c", "#c9bfae", "#9aa0a8"];
const AVG_COLOR = "#45433f";
const MET_COLOR = "var(--ui-ok)";
const BELOW_COLOR = "var(--ui-hot)";

const iso = (d) => {
	const z = new Date(d.getTime() - d.getTimezoneOffset() * 60000);
	return z.toISOString().slice(0, 10);
};

function presetRange(key) {
	const today = new Date();
	const end = iso(today);
	const d = new Date(today);
	if (key === "30d") d.setDate(d.getDate() - 29);
	else if (key === "month") d.setDate(1);
	else if (key === "12m") { d.setFullYear(d.getFullYear() - 1); d.setDate(d.getDate() + 1); }
	else { d.setMonth(0); d.setDate(1); }
	return { from: iso(d), to: end };
}

/* the granularity that reads best for a range, until the user picks one */
function autoGran(from, to) {
	const days = (new Date(to) - new Date(from)) / 864e5 + 1;
	if (days <= 45) return "day";
	if (days <= 120) return "week";
	if (days <= 800) return "month";
	return "year";
}

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
		<label class="wxx-f"><span>From</span><input class="select" type="date" id="wxx-from"></label>
		<label class="wxx-f"><span>To</span><input class="select" type="date" id="wxx-to"></label>
		<div class="pillgroup" id="wxx-preset" role="group" aria-label="Period">${PRESETS.map(
			(p) => `<button type="button" data-p="${p[0]}">${p[1]}</button>`
		).join("")}</div>
	</div>
	<div class="wxx-controls">
		<div class="pillgroup" id="wxx-gran" role="group" aria-label="Group by">${GRANS.map(
			(g) => `<button type="button" data-g="${g[0]}">${g[1]}</button>`
		).join("")}</div>
		<div class="wxx-compare" id="wxx-compare"></div>
		<div class="wxx-right">
			<button type="button" class="btn ghost small" id="wxx-run" title="Add each bucket to the ones before it">Running total</button>
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
		const r = presetRange("ytd");
		this.s = {
			measure: "rainfall", farm: ctx.filters.farm || "", farmTouched: false,
			preset: "ytd", from: r.from, to: r.to, gran: "month", granTouched: false,
			compare: null, avg: true, running: false, table: false, seq: 0, data: null,
		};
		const $ = (id) => host.querySelector(id);
		$("#wxx-measure").onchange = (e) => { this.s.measure = e.target.value; this.s.running = false; this.load(); };
		$("#wxx-farm").onchange = (e) => { this.s.farm = e.target.value; this.s.farmTouched = true; this.load(); };
		const setDates = () => {
			this.s.preset = null;
			if (!this.s.granTouched) this.s.gran = autoGran(this.s.from, this.s.to);
			this.load();
		};
		$("#wxx-from").onchange = (e) => { if (e.target.value) { this.s.from = e.target.value; setDates(); } };
		$("#wxx-to").onchange = (e) => { if (e.target.value) { this.s.to = e.target.value; setDates(); } };
		$("#wxx-preset").onclick = (e) => {
			const b = e.target.closest("button[data-p]");
			if (!b) return;
			const r = presetRange(b.dataset.p);
			Object.assign(this.s, { preset: b.dataset.p, from: r.from, to: r.to, compare: null });
			if (!this.s.granTouched) this.s.gran = autoGran(r.from, r.to);
			this.load();
		};
		$("#wxx-gran").onclick = (e) => {
			const b = e.target.closest("button[data-g]");
			if (!b) return;
			this.s.gran = b.dataset.g;
			this.s.granTouched = true;
			this.load();
		};
		$("#wxx-compare").onclick = (e) => {
			const b = e.target.closest("button[data-y]");
			if (!b) return;
			if (b.dataset.y === "avg") this.s.avg = !this.s.avg;
			else {
				const y = Number(b.dataset.y);
				const c = new Set(this.s.compare || []);
				c.has(y) ? c.delete(y) : c.add(y);
				this.s.compare = [...c].sort((a, b2) => b2 - a);
			}
			this.load();
		};
		$("#wxx-run").onclick = () => { this.s.running = !this.s.running; this.draw(); };
		$("#wxx-table-btn").onclick = () => { this.s.table = !this.s.table; this.draw(); };
		$("#wxx-csv").onclick = () => this.csv();
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

	async load() {
		const s = this.s;
		const seq = ++s.seq;
		const startYear = Number(s.from.slice(0, 4));
		// last year is compared by default, until the user picks their own years
		const compare = s.compare == null ? [startYear - 1] : s.compare;
		this.syncControls();
		const chart = this.host.querySelector("#wxx-chart");
		chart.style.opacity = ".5";
		let data;
		try {
			({ data } = await this.ctx.api.get("upande_irrigation.api.weather_series.series", {
				measure: s.measure, farm: s.farm, from_date: s.from, to_date: s.to,
				granularity: s.gran, compare_years: compare.join(","), long_term: s.avg ? 1 : 0,
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
		s.compareShown = compare;
		this.draw();
	},

	syncControls() {
		const s = this.s;
		const $ = (id) => this.host.querySelector(id);
		$("#wxx-measure").value = s.measure;
		$("#wxx-from").value = s.from;
		$("#wxx-to").value = s.to;
		$("#wxx-preset").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.p === s.preset));
		$("#wxx-gran").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.g === s.gran));
		$("#wxx-table-btn").classList.toggle("on", s.table);
	},

	/* comparison chips: the years before the period starts that hold readings */
	drawCompare() {
		const s = this.s;
		const d = s.data;
		const startYear = Number(s.from.slice(0, 4));
		const years = (d.years_available || []).filter((y) => y < startYear).slice(0, 8);
		const on = new Set(s.compareShown || []);
		const host = this.host.querySelector("#wxx-compare");
		if (!years.length) {
			const since = d.data_from ? new Date(`${d.data_from}T00:00:00`).toLocaleDateString("en-GB", { month: "short", year: "numeric" }) : "";
			host.innerHTML = `<span class="wxx-muted">No earlier years to compare${since ? ` — recorded since ${since}` : ""}</span>`;
			return;
		}
		const cross = s.from.slice(0, 4) !== s.to.slice(0, 4);
		const yl = (y) => (cross ? `${y}–${String(y + 1).slice(2)}` : String(y));
		host.innerHTML =
			`<span class="wxx-muted">Compare</span>` +
			years.map((y) => `<button type="button" class="wxx-chip${on.has(y) ? " on" : ""}" data-y="${y}">${yl(y)}</button>`).join("") +
			(d.measure !== "deficit_blocks"
				? `<button type="button" class="wxx-chip${s.avg ? " on" : ""}" data-y="avg" title="Every earlier year that recorded at least 60% of these days; shown when there are three or more">Long-term avg</button>`
				: "");
	},

	/* series ready to plot: running totals applied, colours assigned */
	plotted() {
		const s = this.s;
		const d = s.data;
		const color = (MEASURES.find((m) => m[0] === d.measure) || [])[2] || "var(--ui-rain)";
		let ci = 0;
		return d.series.map((ser) => {
			const isCur = ser.key === "current";
			const isAvg = ser.key === "avg";
			let values = ser.values;
			if (s.running && d.kind === "total") {
				// carried across gaps between readings, but stopped after the last
				// one: a flat line on into weeks nobody has logged yet reads as dry
				let last = -1;
				values.forEach((v, i) => { if (v != null) last = i; });
				let acc = null;
				values = values.map((v, i) => {
					if (i > last) return null;
					if (v == null) return acc;
					acc = (acc || 0) + v;
					return Math.round(acc * 100) / 100;
				});
			}
			return {
				...ser, values, isCur, isAvg,
				color: isCur ? color : isAvg ? AVG_COLOR : COMPARE_COLORS[ci++ % COMPARE_COLORS.length],
				dash: isCur ? "" : isAvg ? "2 3" : "6 4",
			};
		});
	},

	/* expected rainfall, per position, ready for the renderers; null when off,
	 * not rainfall, or shown as running totals */
	expected(labels) {
		const s = this.s;
		const d = s.data;
		const ex = d.expected;
		if (!ex || s.running || d.kind !== "total" || !ex.some(Boolean)) return null;
		const daily = d.granularity === "day";
		const fmt = (v) => this.ctx.charts.fmtNum(v, 1);
		const cur = d.series.find((x) => x.key === "current");
		return {
			daily,
			targets: ex.map((e) => (e ? e.mm : null)),
			colors: ex.map((e) => (daily || !e || !e.status ? null : e.status === "met" ? MET_COLOR : BELOW_COLOR)),
			faded: ex.map((e) => !daily && !!(e && e.open)),
			tips: ex.map((e, i) => {
				if (!e) return "";
				const v = cur && cur.values[i];
				const part = e.days < e.of_days ? ` (${e.days} of ${e.of_days} days)` : "";
				const pc = v != null && e.mm > 0 ? ` · ${Math.round((v / e.mm) * 100)}%` : "";
				return `expected ${fmt(e.mm)} mm${part}${pc}${e.open && !daily ? " · still running" : ""}`;
			}),
			any: (st) => ex.some((e) => e && e.status === st),
			anyOpen: ex.some((e) => e && e.open),
			label: daily ? "Expected (mm/day)" : "Expected",
			count: labels.length,
		};
	},

	draw() {
		const s = this.s;
		const d = s.data;
		if (!d) return;
		this.syncControls();
		this.drawCompare();
		const { charts } = this.ctx;
		const $ = (id) => this.host.querySelector(id);
		const unit = d.unit;
		const series = this.plotted();
		const labels = d.labels;
		const chart = $("#wxx-chart");
		const runBtn = $("#wxx-run");
		// the dashboard's .btn sets display, which beats the hidden attribute
		runBtn.style.display = d.kind === "total" ? "" : "none";
		if (d.kind !== "total") s.running = false;
		runBtn.classList.toggle("on", s.running);

		const W = 1200;
		const H = 300;
		const pick = (vals, f) => vals.map((v) => (v == null ? null : f(v)));
		const any = series.some((x) => x.values.some((v) => v != null));
		const ex = this.expected(labels);
		// the current period's bars carry the verdict; comparisons keep their colours
		const judged = (x) => (ex && x.isCur && !ex.daily
			? { colors: ex.colors, faded: ex.faded, targets: ex.targets, tips: ex.tips }
			: ex && x.isCur ? { tips: ex.tips } : {});
		if (!any) {
			chart.innerHTML = `<div class="empty small">Nothing recorded for ${charts.esc(d.label.toLowerCase())}${d.farm ? ` on ${charts.esc(d.farm)}` : ""} in these dates.</div>`;
		} else if (d.kind === "total" && !s.running && labels.length <= 36) {
			charts.groupedBars(chart, {
				labels, W, H, unit: ` ${unit}`, dec: unit === "mm" ? 1 : 0,
				// with expected colouring on, only this period is bars, so green/red
				// never sits beside another year's bar; comparisons become lines
				groups: series.filter((x) => !ex || x.isCur).map((x) => ({ label: x.label, color: x.color, values: x.values, ...judged(x) })),
				lines: ex ? series.filter((x) => !x.isCur).map((x) => ({ label: x.label, color: x.color, values: x.values, dash: x.dash })) : [],
				steps: ex && ex.daily ? { values: ex.targets, label: ex.label } : undefined,
			});
		} else if (d.measure === "temperature") {
			const cur = series.find((x) => x.isCur);
			const lo = pick(cur.values, (v) => v.min);
			const hi = pick(cur.values, (v) => v.max);
			const all = series.flatMap((x) => x.values.filter(Boolean).flatMap((v) => [v.min, v.max])).filter((v) => v != null);
			charts.mkChart(
				chart,
				series.map((x) => ({
					label: `${x.label} mean`, color: x.color, values: pick(x.values, (v) => v.mean),
					dash: x.dash, width: x.isCur ? 3 : 2, unit: "°C", noPoints: !x.isCur || labels.length > 40,
				})),
				W, H,
				{
					xLabels: labels, tooltip: true, noFill: true,
					band: { lo, hi, color: cur.color },
					yMin: Math.floor(Math.min(...all) - 2), yMax: Math.ceil(Math.max(...all) + 2),
				}
			);
		} else if (d.measure === "deficit_blocks") {
			const cur = series.find((x) => x.isCur);
			charts.mkChart(
				chart,
				[
					{ label: "Farm average", color: cur.color, values: pick(cur.values, (v) => v.mean), width: 3, unit: " mm", noPoints: labels.length > 40 },
					{ label: "Driest block", color: "var(--ui-hot)", values: pick(cur.values, (v) => v.driest), dash: "5 3", width: 2, unit: " mm", noPoints: true },
				],
				W, H, { xLabels: labels, tooltip: true, noFill: true }
			);
		} else {
			const all = series.flatMap((x) => x.values).filter((v) => v != null);
			const lines = series.map((x) => ({
				label: x.label, color: x.color, values: x.values,
				type: x.isCur && d.kind === "total" && !s.running ? "bar" : undefined,
				dash: x.dash, width: x.isCur ? 3 : 2, unit: ` ${unit}`,
				noPoints: !x.isCur || labels.length > 40,
				...judged(x),
			}));
			if (ex && ex.daily) {
				lines.push({ label: ex.label, color: "var(--ink-3)", values: ex.targets, dash: "5 4", width: 1.5, unit: " mm", noPoints: true });
			}
			charts.mkChart(
				chart,
				lines,
				W, H,
				{
					xLabels: labels, tooltip: true, noFill: series.length > 1 || d.kind === "level",
					...(d.kind === "level" ? { yMin: Math.floor(Math.min(0, ...all)), yMax: Math.ceil(Math.max(0, ...all)) } : {}),
				}
			);
		}

		// legend: every series, so identity never rests on colour alone
		const legend = [];
		series.forEach((x) => {
			const line = d.kind === "level" || s.running || (!x.isCur && (labels.length > 36 || !!ex));
			const empty = !x.isCur && !x.values.some((v) => v != null);
			legend.push(`<span><i class="${line ? "ln" : ""}" style="background:${x.color}${x.dash && line ? ";opacity:.8" : ""}"></i>${charts.esc(x.label)}${empty ? ' <em class="wxx-muted">(no readings)</em>' : x.coverage != null && x.coverage < 0.9 ? ` <em class="wxx-muted">(${Math.round(x.coverage * 100)}% of days read)</em>` : ""}</span>`);
		});
		if (d.measure === "temperature") legend.push(`<span><i style="background:var(--ui-hot);opacity:.18"></i>${charts.esc(series[0].label)} min–max</span>`);
		if (d.measure === "deficit_blocks") legend.splice(0, legend.length,
			`<span><i class="ln" style="background:var(--ui-cool)"></i>Farm average</span>`,
			`<span><i class="ln" style="background:var(--ui-hot)"></i>Driest block</span>`);
		if (ex && ex.daily) legend.push(`<span><i class="ln tick"></i>${charts.esc(ex.label)}</span>`);
		if (ex && !ex.daily) {
			if (ex.any("met")) legend.push(`<span><i style="background:${MET_COLOR}"></i>Met expected</span>`);
			if (ex.any("below")) legend.push(`<span><i style="background:${BELOW_COLOR}"></i>Below expected</span>`);
			if (ex.anyOpen) legend.push(`<span><i class="open" style="background:var(--ink-4)"></i>Still running</span>`);
			legend.push(`<span><i class="ln tick"></i>Expected</span>`);
		}
		$("#wxx-legend").innerHTML = legend.join("");

		this.drawSummary(series);
		$("#wxx-notes").innerHTML = (d.notes || [])
			.map((n) => `<div class="alert ${/Ignored/.test(n) ? "warn" : "muted"}">${charts.esc(n)}</div>`)
			.join("");
		const fd = (x) => new Date(`${x}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
		$("#wxx-meta").textContent = `${fd(d.from_date)} – ${fd(d.to_date)} · ${d.farm || "all farms"} · ${d.unit}`;
		this.drawTable(series);
	},

	/* one sentence: the period's figure, each comparison against it, coverage */
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
			if (x.headline == null && !x.isAvg) {
				parts.push(`${charts.esc(x.label)}: <span class="wxx-muted">no readings</span>`);
				return;
			}
			// compare like for like: where this period has gaps, the other year is
			// measured over the same days that were read here
			const ref = partial && x.headline_same_days != null ? x.headline_same_days : x.headline;
			let ch = "";
			if (ref && cur.headline != null) {
				const pc = ((cur.headline - ref) / Math.abs(ref)) * 100;
				ch = ` <span class="${pc >= 0 ? "wxx-up" : "wxx-down"}">(${pc >= 0 ? "+" : "−"}${Math.abs(pc).toFixed(0)}%${partial ? " on the same days" : ""})</span>`;
			}
			parts.push(`${charts.esc(x.label)}: ${fmtv(partial ? ref : x.headline)}${partial ? ' <span class="wxx-muted">same days</span>' : ""}${ch}`);
		});
		if (d.measure === "rainfall" && d.granularity === "day") {
			const wet = cur.values.filter((v) => v != null && v > 0.1).length;
			parts.push(`${wet} wet day${wet === 1 ? "" : "s"}`);
		}
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
		const head = ["Period"];
		series.forEach((x) => {
			if (d.measure === "temperature") head.push(`${x.label} min`, `${x.label} mean`, `${x.label} max`);
			else if (d.measure === "deficit_blocks") head.push(`${x.label} average`, `${x.label} driest`, "Driest block");
			else head.push(`${x.label} (${d.unit})`);
			if (x.isCur && d.expected) head.push(`Expected (${d.unit})`);
		});
		const body = d.labels.map((lab, i) => {
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
		a.download = `${d.measure}-${d.farm || "all-farms"}-${d.from_date}-to-${d.to_date}-${d.granularity}.csv`;
		document.body.appendChild(a);
		a.click();
		setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 0);
	},
};
