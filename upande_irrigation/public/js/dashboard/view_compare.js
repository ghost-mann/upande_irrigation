/* Compare — year-on-year weather overlay.
 *
 * This was the real chart buried at the bottom of meniscus's Weather tab, while
 * its "Compare" tab held an "In Development" placeholder. Promoted to its own
 * view here.
 *
 * Also drops the Chart.js CDN load meniscus pulled in for this one chart — the
 * in-house multi-line renderer in charts.js covers it, so the page has no
 * charting dependency at all.
 *
 * Pulls the full history once (days=3650) and re-buckets client-side, so
 * switching metric / granularity / mode is instant and costs no round trips.
 */

import { pagehead, statusStrip, icon } from "./shell.js";

const METRICS = [
	["rainfall_mm", "Rainfall (mm)", "mm"],
	["eto", "Reference ETo (mm)", "mm"],
	["daily_evaporation", "Pan evaporation (mm)", "mm"],
	["et_crop", "ET crop (mm)", "mm"],
	["mean_temperature", "Mean temp (°C)", "°C"],
	["minimum_temperature", "Min temp (°C)", "°C"],
	["maximum_temperature", "Max temp (°C)", "°C"],
	["swd", "Soil water deficit (mm)", "mm"],
];

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/* ISO week number, so weekly buckets line up across years. */
function isoWeek(dt) {
	const d = new Date(Date.UTC(dt.getFullYear(), dt.getMonth(), dt.getDate()));
	const dayNum = d.getUTCDay() || 7;
	d.setUTCDate(d.getUTCDate() + 4 - dayNum);
	const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
	return Math.ceil(((d - yearStart) / 86400000 + 1) / 7);
}

function aggregate(rows, metric, mode, granularity) {
	const byYear = {};
	rows.forEach((r) => {
		if (!r.date) return;
		const v = r[metric];
		if (v == null || isNaN(v)) return;
		const year = String(r.date).slice(0, 4);
		(byYear[year] = byYear[year] || []).push({ date: r.date, val: parseFloat(v) || 0 });
	});

	const monthly = granularity === "monthly";
	const maxKey = monthly ? 11 : 52;
	const out = {};

	Object.entries(byYear).forEach(([year, pts]) => {
		const buckets = {};
		pts.forEach((p) => {
			const dt = new Date(`${String(p.date).slice(0, 10)}T00:00:00`);
			if (isNaN(dt)) return;
			const key = monthly ? dt.getMonth() : isoWeek(dt) - 1;
			(buckets[key] = buckets[key] || []).push(p.val);
		});
		const series = [];
		let running = 0;
		let seen = false;
		for (let k = 0; k <= maxKey; k++) {
			const vals = buckets[k] || [];
			let v = null;
			if (vals.length) {
				seen = true;
				v = mode === "avg" ? vals.reduce((a, b) => a + b, 0) / vals.length : vals.reduce((a, b) => a + b, 0);
			}
			if (mode === "cumulative") {
				if (v != null) running += v;
				series.push(seen ? +running.toFixed(2) : null);
			} else {
				series.push(v != null ? +v.toFixed(2) : null);
			}
		}
		out[year] = series;
	});

	const labels = [];
	for (let k = 0; k <= maxKey; k++) labels.push(monthly ? MONTHS[k] : `W${k + 1}`);
	return { byYear: out, labels };
}

export default {
	id: "compare",
	label: "Compare",

	mount(el, ctx) {
		this.ctx = ctx;
		this.el = el;
		this.rows = [];
		this.years = [];
		this.activeYears = new Set();
		this.loadedFarm = null;

		el.innerHTML = `
${pagehead("Year-on-Year", "Compare seasons", '<span id="cmp-meta"></span>')}
<div class="status" id="cmp-status"></div>
<div class="card">
	<div class="card__head">
		<h3>${icon("compare")}<span id="cmp-title">Rainfall</span></h3>
		<div class="card__tools">
			<select class="select" id="cmp-metric">
				${METRICS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}
			</select>
			<select class="select" id="cmp-gran">
				<option value="monthly" selected>Monthly</option>
				<option value="weekly">Weekly</option>
			</select>
			<select class="select" id="cmp-mode">
				<option value="period" selected>Period total</option>
				<option value="cumulative">Cumulative</option>
				<option value="avg">Average</option>
			</select>
			<button class="btn ghost" id="cmp-reload" type="button">${icon("refresh")}Reload</button>
		</div>
	</div>
	<div class="side__chips" id="cmp-years"></div>
	<div id="cmp-chart"></div>
	<div class="clegend" id="cmp-legend"></div>
</div>
<div class="card tight">
	<div class="card__head"><h3>Season totals</h3><span class="meta">selected years · current metric</span></div>
	<div class="tablewrap">
		<table class="table">
			<thead><tr><th>Year</th><th class="num">Total</th><th class="num">Mean</th><th class="num">Peak</th><th class="num">Buckets with data</th></tr></thead>
			<tbody id="cmp-table"></tbody>
		</table>
	</div>
</div>`;

		["#cmp-metric", "#cmp-gran", "#cmp-mode"].forEach((sel) => {
			el.querySelector(sel).addEventListener("change", () => this.paint());
		});
		el.querySelector("#cmp-reload").addEventListener("click", () => this.load(true));
		this.unsubscribe = ctx.onFilterChange(() => {});
	},

	/* The farm filter is the only thing that changes what we fetch — the date
	 * range deliberately does not, since the point is the whole history. */
	async refresh() {
		const farm = this.ctx.filters.farm || "";
		if (this.rows.length && farm === this.loadedFarm) {
			this.paint();
			return;
		}
		await this.load();
	},

	async load(force = false) {
		const { api, filters } = this.ctx;
		const status = this.el.querySelector("#cmp-status");
		const farm = filters.farm || "";
		if (!force && this.rows.length && farm === this.loadedFarm) return;

		statusStrip(status, "");
		this.el.querySelector("#cmp-chart").innerHTML = '<div class="loading">Loading full history…</div>';

		let data;
		try {
			({ data } = await api.get("upande_irrigation.api.weather.fetch", {
				days: 3650,
				farm,
			}));
		} catch (err) {
			statusStrip(status, `Could not load history: ${err.message}`);
			this.el.querySelector("#cmp-chart").innerHTML = "";
			return;
		}

		this.rows = (data && data.weather) || [];
		this.loadedFarm = farm;
		if (data && data.farms) this.ctx.shell.setFarms(data.farms);

		this.years = [...new Set(this.rows.map((r) => String(r.date || "").slice(0, 4)).filter(Boolean))].sort();
		/* Default to the three most recent seasons — enough to see a trend
		 * without turning the chart into spaghetti. */
		this.activeYears = new Set(this.years.slice(-3));
		this.paint();
	},

	paint() {
		const { charts } = this.ctx;
		const metric = this.el.querySelector("#cmp-metric").value;
		const gran = this.el.querySelector("#cmp-gran").value;
		const mode = this.el.querySelector("#cmp-mode").value;
		const metricDef = METRICS.find((m) => m[0] === metric) || METRICS[0];

		this.el.querySelector("#cmp-title").textContent = metricDef[1];
		this.el.querySelector("#cmp-meta").textContent = this.rows.length
			? `${this.rows.length} readings across ${this.years.length} ${this.years.length === 1 ? "year" : "years"}`
			: "no history";

		if (!this.rows.length) {
			this.el.querySelector("#cmp-chart").innerHTML = '<div class="empty">No weather history for this farm.</div>';
			this.el.querySelector("#cmp-years").innerHTML = "";
			this.el.querySelector("#cmp-legend").innerHTML = "";
			this.el.querySelector("#cmp-table").innerHTML = "";
			return;
		}

		this.el.querySelector("#cmp-years").innerHTML = this.years
			.map(
				(y) =>
					`<button class="side__chip${this.activeYears.has(y) ? " on" : ""}" data-year="${y}" type="button">${y}</button>`
			)
			.join("");
		this.el.querySelectorAll("[data-year]").forEach((b) => {
			b.addEventListener("click", () => {
				const y = b.getAttribute("data-year");
				/* Never let the operator empty the chart entirely. */
				if (this.activeYears.has(y) && this.activeYears.size === 1) return;
				if (this.activeYears.has(y)) this.activeYears.delete(y);
				else this.activeYears.add(y);
				this.paint();
			});
		});

		const { byYear, labels } = aggregate(this.rows, metric, mode, gran);
		const active = [...this.activeYears].sort();
		const datasets = active.map((y) => ({ label: y, values: byYear[y] || [] }));

		charts.mkMultiLine(this.el.querySelector("#cmp-chart"), labels, datasets, 1200, 320, {
			unit: metricDef[2],
		});

		this.el.querySelector("#cmp-legend").innerHTML = datasets
			.map(
				(d, i) =>
					`<span><i class="ln" style="background:${charts.SERIES_COLORS[i % charts.SERIES_COLORS.length]}"></i>${charts.esc(d.label)}</span>`
			)
			.join("");

		this.el.querySelector("#cmp-table").innerHTML = datasets
			.map((d) => {
				const vals = (d.values || []).filter((v) => v != null);
				if (!vals.length) {
					return `<tr><td><b>${charts.esc(d.label)}</b></td><td class="num">—</td><td class="num">—</td><td class="num">—</td><td class="num">0</td></tr>`;
				}
				/* In cumulative mode the running total is the last point, not the
				 * sum of the points. */
				const total = mode === "cumulative" ? vals[vals.length - 1] : vals.reduce((a, b) => a + b, 0);
				const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
				return `<tr>
	<td><b>${charts.esc(d.label)}</b></td>
	<td class="num">${charts.fmtNum(total)} ${charts.esc(metricDef[2])}</td>
	<td class="num">${charts.fmtNum(mean)}</td>
	<td class="num">${charts.fmtNum(Math.max(...vals))}</td>
	<td class="num">${vals.length}</td>
</tr>`;
			})
			.join("");
	},

	unmount() {
		if (this.unsubscribe) this.unsubscribe();
	},
};
