/* Compare — several seasons of one weather measure, month by month.
 *
 * Kept lean, like the Weather card: a measure, a farm, the years to lay over
 * each other, and month-by-month or a running total. Each year is a line on
 * the same graph-paper chart (charts.graphPaper); the newest selected year is
 * drawn heavy in the measure's colour, the others thinner.
 *
 * The numbers come from api.weather_series.series -- the Weather card's own
 * endpoint -- so both agree: "All farms" is the mean of the farms that read
 * that day (never their sum), and a month without readings is a gap.
 */

import { pagehead, statusStrip, icon } from "./shell.js";

const MEASURES = [
	["rainfall", "Rainfall", "var(--ui-rain)"],
	["temperature", "Temperature", "var(--ui-hot)"],
	["evaporation", "Pan evaporation", "var(--ui-eto)"],
	["deficit_blocks", "Soil deficit · block balance", "var(--ui-cool)"],
	["deficit_swd", "Soil deficit · old SWD", "var(--ui-cool)"],
];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/* earlier years: muted and told apart by hue, newest first */
const OLDER = ["#8a8780", "#6f8fa6", "#b08d6e", "#7d8f74", "#9a7f9a", "#a39a8c", "#c4a35a", "#5f6b73"];
/* the column naming each measure's peak month */
const PEAK = {
	rainfall: "Wettest month", evaporation: "Highest month", temperature: "Warmest month",
	deficit_blocks: "Driest month", deficit_swd: "Driest month",
};

/* one number per month for a series: levels carry {mean, …} objects */
const monthValue = (measure, v) => (v == null ? null : measure === "temperature" || measure === "deficit_blocks" ? v.mean : v);

/* a running total carried across gaps, stopped after the last reading so a
 * year still under way does not run flat into months nobody has logged */
function running(values) {
	let last = -1;
	values.forEach((v, i) => { if (v != null) last = i; });
	let acc = null;
	return values.map((v, i) => {
		if (i > last) return null;
		if (v == null) return acc;
		acc = (acc || 0) + v;
		return Math.round(acc * 100) / 100;
	});
}

export default {
	id: "compare",
	label: "Compare",

	mount(el, ctx) {
		this.ctx = ctx;
		this.el = el;
		this.s = {
			measure: "rainfall", farm: ctx.filters.farm || "", farmTouched: false,
			years: null, available: [], running: false, data: null, seq: 0,
		};

		el.innerHTML = `
${pagehead("Year-on-Year", "Compare seasons", '<span id="cmp-meta"></span>')}
<div class="status" id="cmp-status"></div>
<div class="card wxx">
	<div class="card__head"><h3>${icon("compare")}<span id="cmp-title">Rainfall</span></h3></div>
	<div class="wxx-controls">
		<label class="wxx-f"><span>Show</span><select class="select" id="cmp-measure">${MEASURES.map(
			(m) => `<option value="${m[0]}">${m[1]}</option>`
		).join("")}</select></label>
		<label class="wxx-f"><span>Farm</span><select class="select" id="cmp-farm"></select></label>
		<div class="wxx-f cmp-years"><span>Years</span><div class="side__chips" id="cmp-years"></div></div>
		<div class="wxx-right">
			<div class="pillgroup" id="cmp-mode" role="group" aria-label="Show as">
				<button type="button" data-m="month">Month by month</button>
				<button type="button" data-m="running">Running total</button>
			</div>
		</div>
	</div>
	<div id="cmp-chart"></div>
	<div class="clegend" id="cmp-legend"></div>
</div>
<div class="card tight">
	<div class="card__head"><h3>Seasons</h3><span class="meta" id="cmp-table-meta"></span></div>
	<div class="tablewrap">
		<table class="table">
			<thead id="cmp-head"></thead>
			<tbody id="cmp-table"></tbody>
		</table>
	</div>
</div>`;

		const $ = (id) => el.querySelector(id);
		$("#cmp-measure").onchange = (e) => {
			this.s.measure = e.target.value;
			this.s.years = null; // years with data differ by measure
			this.load();
		};
		$("#cmp-farm").onchange = (e) => {
			this.s.farm = e.target.value;
			this.s.farmTouched = true;
			this.s.years = null;
			this.load();
		};
		$("#cmp-years").onclick = (e) => {
			const b = e.target.closest("[data-year]");
			if (!b) return;
			const y = Number(b.dataset.year);
			const on = new Set(this.s.years || []);
			// never let the chart go empty
			if (on.has(y) && on.size === 1) return;
			on.has(y) ? on.delete(y) : on.add(y);
			this.s.years = [...on].sort((a, b2) => b2 - a);
			this.load();
		};
		$("#cmp-mode").onclick = (e) => {
			const b = e.target.closest("[data-m]");
			if (!b) return;
			this.s.running = b.dataset.m === "running";
			this.paint();
		};
		this.unsubscribe = ctx.onFilterChange(() => {});
	},

	async refresh() {
		if (!this.s.farmTouched) this.s.farm = this.ctx.filters.farm || "";
		this.fillFarms();
		await this.load();
	},

	fillFarms() {
		const { esc } = this.ctx.charts;
		const farms = this.ctx.shell.farms || [];
		this.el.querySelector("#cmp-farm").innerHTML = `<option value="">All farms</option>${farms
			.map((f) => `<option value="${esc(f)}"${f === this.s.farm ? " selected" : ""}>${esc(f)}</option>`)
			.join("")}`;
	},

	/* One call: the newest selected year, with every other selected year and
	 * each one's year before (for "vs previous year") lined up on its calendar. */
	async load() {
		const s = this.s;
		const seq = ++s.seq;
		const status = this.el.querySelector("#cmp-status");
		statusStrip(status, "");
		const chart = this.el.querySelector("#cmp-chart");
		chart.style.opacity = ".5";
		const ask = async (main, others) => {
			const { data } = await this.ctx.api.get("upande_irrigation.api.weather_series.series", {
				measure: s.measure, farm: s.farm, from_date: `${main}-01-01`, to_date: `${main}-12-31`,
				granularity: "month", compare_years: others.join(","), long_term: 0,
			});
			return data;
		};
		let data;
		try {
			const thisYear = new Date().getFullYear();
			if (!s.years) {
				// first look: which years hold readings, then the latest three
				const probe = await ask(thisYear, []);
				if (seq !== s.seq) return;
				s.available = probe.years_available || [];
				s.years = s.available.slice(0, 3);
				if (!s.years.length) s.years = [thisYear];
			}
			const main = Math.max(...s.years);
			const extra = new Set();
			s.years.forEach((y) => { extra.add(y); extra.add(y - 1); });
			extra.delete(main);
			data = await ask(main, [...extra].filter((y) => y < main).sort((a, b) => b - a));
		} catch (err) {
			if (seq !== s.seq) return;
			chart.style.opacity = "";
			statusStrip(status, `Could not load history: ${err.message}`);
			return;
		}
		if (seq !== s.seq) return;
		chart.style.opacity = "";
		s.available = data.years_available || s.available;
		s.data = data;
		this.paint();
	},

	/* every loaded year as {year, values (one per month), headline, …} */
	years() {
		const d = this.s.data;
		const main = Math.max(...this.s.years);
		return d.series.map((ser) => ({
			...ser,
			year: ser.key === "current" ? main : Number(ser.key),
			monthly: ser.values.map((v) => monthValue(d.measure, v)),
		}));
	},

	paint() {
		const s = this.s;
		const d = s.data;
		if (!d) return;
		const { charts } = this.ctx;
		const $ = (id) => this.el.querySelector(id);
		const def = MEASURES.find((m) => m[0] === d.measure) || MEASURES[0];
		const total = d.kind === "total";
		const asRunning = s.running && total;

		$("#cmp-title").textContent = def[1];
		$("#cmp-measure").value = d.measure;
		this.fillFarms();
		$("#cmp-mode").style.display = total ? "" : "none";
		$("#cmp-mode").querySelectorAll("button").forEach((b) => b.classList.toggle("on", (b.dataset.m === "running") === asRunning));
		const avail = [...new Set([...s.available, ...s.years])].sort((a, b) => a - b);
		$("#cmp-meta").textContent = `${d.farm || "All farms"} · ${avail.length} ${avail.length === 1 ? "year" : "years"} on record`;

		const all = this.years();
		const byYear = new Map(all.map((y) => [y.year, y]));
		const shown = s.years.map((y) => byYear.get(y)).filter(Boolean); // newest first
		const newest = shown[0];
		let oi = 0;
		const colour = (y) => (y === newest ? def[2] : OLDER[oi++ % OLDER.length]);
		const plotted = shown.map((y) => ({ ...y, color: colour(y), values: asRunning ? running(y.monthly) : y.monthly }));
		// a selected year's chip wears its line's colour, so chip and line match
		const chipColour = new Map(plotted.map((y) => [y.year, y.color]));
		$("#cmp-years").innerHTML = avail
			.map((y) => {
				const c = chipColour.get(y);
				return c
					? `<button class="side__chip cmp-chip on" style="--chip:${c}" data-year="${y}" type="button"><i></i>${y}</button>`
					: `<button class="side__chip cmp-chip" data-year="${y}" type="button">${y}</button>`;
			})
			.join("");

		const lines = plotted.slice().reverse().map((y) => ({
			label: String(y.year), color: y.color, values: y.values, width: y === plotted[0] ? 3 : 1.75,
		}));
		const band = d.measure === "temperature" && newest
			? {
				lo: newest.values.map((v) => (v ? v.min : null)), hi: newest.values.map((v) => (v ? v.max : null)),
				color: def[2], label: `${newest.year} min–max`,
			}
			: undefined;
		charts.graphPaper($("#cmp-chart"), {
			labels: MONTHS, lines, band, unit: ` ${d.unit}`, dec: 1,
			yTitle: `${def[1]}${asRunning ? ", running total" : ""} (${d.unit})`, xTitle: "Month",
		});
		$("#cmp-legend").innerHTML = plotted
			.map((y) => `<span><i class="ln" style="background:${y.color}${y === plotted[0] ? "" : ";height:2px"}"></i>${y.year}${y.headline == null ? ' <em class="wxx-muted">(no readings)</em>' : ""}</span>`)
			.join("") + (band ? `<span><i style="background:${def[2]};opacity:.18"></i>${newest.year} min–max</span>` : "");

		this.paintTable(plotted, byYear, def);
	},

	/* one row per year: its figure, its peak month, the change on the year
	 * before, and how many of its days were read */
	paintTable(plotted, byYear, def) {
		const { charts } = this.ctx;
		const d = this.s.data;
		const $ = (id) => this.el.querySelector(id);
		const total = d.kind === "total";
		const thisYear = new Date().getFullYear();
		// whole numbers with separators for big totals (1,523 mm, not 1.5k)
		const fmtv = (v) => {
			if (v == null) return "—";
			const dec = Math.abs(v) >= 100 ? 0 : 1;
			return `${Number(v).toLocaleString("en-GB", { maximumFractionDigits: dec, minimumFractionDigits: dec })} ${charts.esc(d.unit)}`;
		};
		$("#cmp-head").innerHTML = `<tr><th>Year</th><th class="num">${total ? "Total" : "Average"}</th><th>${PEAK[d.measure]}</th><th class="num">vs previous year</th><th class="num">Days read</th></tr>`;
		$("#cmp-table-meta").textContent = thisYear === Math.max(...this.s.years)
			? `${thisYear} so far, compared with ${thisYear - 1} to the same date`
			: "selected years";
		$("#cmp-table").innerHTML = plotted
			.map((y) => {
				const vals = y.monthly;
				let peak = -1;
				vals.forEach((v, i) => { if (v != null && (peak < 0 || v > vals[peak])) peak = i; });
				const prev = byYear.get(y.year - 1);
				// the year still under way is compared with last year to the same date
				const ref = prev && (y.year === thisYear && prev.headline_to_date != null ? prev.headline_to_date : prev.headline);
				let ch = '<span class="wxx-muted">—</span>';
				if (ref && y.headline != null) {
					const pc = Math.round(((y.headline - ref) / Math.abs(ref)) * 100);
					ch = pc === 0 ? "0%" : `<span class="${pc > 0 ? "wxx-up" : "wxx-down"}">${pc > 0 ? "+" : "−"}${Math.abs(pc)}%</span>`;
				}
				return `<tr>
	<td><i class="swatch" style="background:${y.color}"></i><b>${y.year}</b></td>
	<td class="num">${fmtv(y.headline)}</td>
	<td>${peak < 0 ? "—" : `${MONTHS[peak]} · ${fmtv(vals[peak])}`}</td>
	<td class="num">${ch}</td>
	<td class="num">${y.coverage == null ? "—" : `${Math.round(y.coverage * 100)}%`}</td>
</tr>`;
			})
			.join("");
	},

	unmount() {
		if (this.unsubscribe) this.unsubscribe();
	},
};
