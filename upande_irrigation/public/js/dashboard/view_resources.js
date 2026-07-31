/* Water & Energy — meters, pumping, per-section usage against pump targets.
 *
 * Ported from meniscus's resources tab. Section names are shortened for display
 * ("23HA_SECTION - KL" → "23 Ha") and coloured from the house accents rather
 * than the old blue-grey series.
 */

import { pagehead, kpi, statusStrip, icon } from "./shell.js";

const SECTION_COLORS = [
	"var(--ui-teal)",
	"var(--ui-ok)",
	"var(--ui-clay)",
	"var(--ui-violet)",
	"var(--ui-warn)",
	"var(--ui-cool)",
];

const T = {
	water: "var(--ui-water)",
	elec: "var(--ui-elec)",
	pump: "var(--ui-pump)",
	violet: "var(--ui-violet)",
	ok: "var(--ui-ok)",
	hot: "var(--ui-hot)",
};

function shortSection(name) {
	const base = String(name || "")
		.replace(/ - [A-Z]{2}$/, "")
		.replace("_SECTION", "")
		.trim();
	return base.endsWith("HA") ? `${base.slice(0, -2)} Ha` : base || "—";
}

/* Stable colour per section name so a section keeps its hue across charts. */
function sectionColor(name, index) {
	if (index != null) return SECTION_COLORS[index % SECTION_COLORS.length];
	let h = 0;
	const s = String(name || "");
	for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
	return SECTION_COLORS[Math.abs(h) % SECTION_COLORS.length];
}

function slug(name) {
	return String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "sec";
}

/* Bucket meter readings into 7-day windows anchored on the period start. */
function weeklyBuckets(readings, startISO, weeks) {
	const start = new Date(`${startISO}T00:00:00`);
	const out = Array(Math.max(1, weeks)).fill(0);
	(readings || []).forEach((r) => {
		if (r.units_used == null) return;
		const d = new Date(`${String(r.date).slice(0, 10)}T00:00:00`);
		if (isNaN(d)) return;
		const wk = Math.floor(Math.floor((d - start) / 86400000) / 7);
		if (wk < 0 || wk >= out.length) return;
		out[wk] = +(out[wk] + (r.units_used || 0)).toFixed(2);
	});
	return out;
}

function weekLabels(startISO, weeks) {
	const start = new Date(`${startISO}T00:00:00`);
	const out = [];
	for (let i = 0; i < weeks; i++) {
		out.push(new Date(start.getTime() + i * 7 * 86400000).toISOString().slice(0, 10));
	}
	return out;
}

export default {
	id: "resources",
	label: "Water & Energy",

	mount(el, ctx) {
		this.ctx = ctx;
		this.el = el;
		el.innerHTML = `
${pagehead(
	"Water & Energy",
	"Meters · pumping · efficiency",
	'<span id="r-meta"></span>',
	`<select class="ui-select" id="r-section"><option value="">All sections</option></select>
	 <button class="ui-btn ghost" id="r-refresh" type="button">${icon("refresh")}Refresh</button>`
)}
<div class="ui-status" id="r-status"></div>
<div class="ui-kpis" id="r-kpis"></div>

<div class="ui-card">
	<div class="ui-cardhead"><h3>${icon("resources")}River → dam → sections</h3><span class="meta">period totals</span></div>
	<div class="ui-flow" id="r-flow"></div>
</div>

<div class="ui-row3">
	<div class="ui-card"><div class="ui-cardhead"><h3>Monthly pump volume</h3><span class="meta">m³</span></div><div id="r-mon-pump"></div></div>
	<div class="ui-card"><div class="ui-cardhead"><h3>Monthly electricity</h3><span class="meta">kWh</span></div><div id="r-mon-elec"></div></div>
	<div class="ui-card"><div class="ui-cardhead"><h3>Pump efficiency</h3><span class="meta">m³ per kWh</span></div><div id="r-mon-eff"></div></div>
</div>

<div class="ui-row2eq">
	<div class="ui-card">
		<div class="ui-cardhead"><h3>Water by section</h3><span class="meta">tick = period target</span></div>
		<div class="ui-bars" id="r-bars-water"></div>
	</div>
	<div class="ui-card">
		<div class="ui-cardhead"><h3>Electricity by section</h3><span class="meta">tick = period target</span></div>
		<div class="ui-bars" id="r-bars-elec"></div>
	</div>
</div>

<div class="ui-row2eq">
	<div class="ui-card">
		<div class="ui-cardhead"><h3>Recent pump sessions</h3><span class="meta" id="r-tl-count"></span></div>
		<div class="ui-timeline" id="r-timeline"></div>
	</div>
	<div class="ui-card">
		<div class="ui-cardhead"><h3>Weekly targets</h3><span class="meta">Irrigation Pump Profile</span></div>
		<div class="ui-tablewrap"><table class="ui-table">
			<thead><tr><th>Section</th><th>Pump</th><th class="num">m³/wk</th><th class="num">kWh/wk</th></tr></thead>
			<tbody id="r-targets"></tbody>
		</table></div>
	</div>
</div>

<div class="ui-card">
	<div class="ui-cardhead"><h3>Section performance vs target</h3><span class="meta">weekly · dashed line = weekly budget</span></div>
	<div class="ui-sensor-grid" id="r-perf"></div>
</div>

<div class="ui-row2eq">
	<div class="ui-card">
		<div class="ui-cardhead"><h3>Daily water applied</h3><span class="meta">m³ · all sections</span></div>
		<div id="r-daily-water"></div>
	</div>
	<div class="ui-card">
		<div class="ui-cardhead"><h3>Daily pump transfer</h3><span class="meta">m³ and hours</span></div>
		<div id="r-daily-pump"></div>
		<div class="ui-legend row">
			<span><i class="ln" style="background:${T.pump}"></i>m³/day</span>
			<span><i class="ln" style="background:${T.violet}"></i>pump hours</span>
		</div>
	</div>
</div>

<div class="ui-row3">
	<div class="ui-card">
		<div class="ui-cardhead"><h3>Pumping sessions</h3><span class="meta" id="r-pump-count"></span></div>
		<div class="ui-tablewrap scroll"><table class="ui-table">
			<thead><tr><th>Date</th><th class="num">hrs</th><th class="num">kWh</th><th class="num">m³</th></tr></thead>
			<tbody id="r-pump-rows"></tbody>
		</table></div>
	</div>
	<div class="ui-card">
		<div class="ui-cardhead"><h3>Water meter</h3><span class="meta" id="r-water-count"></span></div>
		<div class="ui-tablewrap scroll"><table class="ui-table">
			<thead><tr><th>Date</th><th>Section</th><th class="num">Used m³</th></tr></thead>
			<tbody id="r-water-rows"></tbody>
		</table></div>
	</div>
	<div class="ui-card">
		<div class="ui-cardhead"><h3>Electricity meter</h3><span class="meta" id="r-elec-count"></span></div>
		<div class="ui-tablewrap scroll"><table class="ui-table">
			<thead><tr><th>Date</th><th>Section</th><th class="num">kWh</th></tr></thead>
			<tbody id="r-elec-rows"></tbody>
		</table></div>
	</div>
</div>`;

		el.querySelector("#r-refresh").addEventListener("click", () => this.refresh());
		el.querySelector("#r-section").addEventListener("change", () => this.refresh());
		this.unsubscribe = ctx.onFilterChange(() => {});
	},

	async refresh() {
		const { api, filters } = this.ctx;
		const status = this.el.querySelector("#r-status");
		statusStrip(status, "");
		const section = this.el.querySelector("#r-section").value;

		let data;
		try {
			({ data } = await api.get("upande_irrigation.api.resources.fetch", {
				days: filters.days,
				section,
				farm: filters.farm,
			}));
		} catch (err) {
			statusStrip(status, `Could not load resource data: ${err.message}`);
			return;
		}
		if (!data) return;

		this.populateSections(data.sections || []);
		const meta = data.meta || {};
		const metaEl = this.el.querySelector("#r-meta");
		if (metaEl) metaEl.textContent = `${meta.start_date} → ${meta.end_date}`;

		const weeks = Math.max(1, Math.ceil((meta.days || filters.days) / 7));
		this.renderKpis(data);
		this.renderFlow(data);
		this.renderMonthly(data);
		this.renderBars(data, weeks);
		this.renderTargets(data.profiles || {});
		this.renderPerformance(data, weeks);
		this.renderDaily(data);
		this.renderTables(data);
	},

	populateSections(sections) {
		const { charts } = this.ctx;
		const sel = this.el.querySelector("#r-section");
		const key = sections.map((s) => s.id).join("|");
		if (sel.dataset.built === key) return;
		sel.dataset.built = key;
		const cur = sel.value;
		sel.innerHTML =
			'<option value="">All sections</option>' +
			sections
				.map((s) => `<option value="${charts.esc(s.id)}">${charts.esc(s.name)}</option>`)
				.join("");
		if (cur) sel.value = cur;
	},

	renderKpis(data) {
		const { charts } = this.ctx;
		const k = data.kpis || {};
		const wDaily = (data.water.daily_series || []).map((r) => r.units || 0);
		const eDaily = (data.electricity.daily_series || []).map((r) => r.units || 0);
		const pDaily = (data.pumping.daily_series || []).map((r) => r.volume_m3 || 0);
		this.el.querySelector("#r-kpis").innerHTML =
			kpi(T.water, "Water used", charts.fmtNum(k.water_total_m3), "m³", "all sections", charts.sparkline(wDaily, T.water)) +
			kpi(T.elec, "Electricity", charts.fmtNum(k.elec_total_units), "kWh", "pumping meter", charts.sparkline(eDaily, T.elec)) +
			kpi(T.pump, "Pumped to dam", charts.fmtNum(k.pump_total_m3), "m³", "river → dam", charts.sparkline(pDaily, T.pump)) +
			kpi(T.violet, "Pump hours", (k.pump_total_hours || 0).toFixed(1), "hrs", "total run time") +
			kpi(T.ok, "Efficiency", k.pump_efficiency ? k.pump_efficiency.toFixed(2) : "—", "m³/kWh", "water per kWh");
	},

	renderFlow(data) {
		const { charts, icon: ic } = this.ctx;
		const k = data.kpis || {};
		const totalSection = Object.values(data.water.by_section || {}).reduce(
			(s, v) => s + (v.total || 0),
			0
		);
		const nodes = [
			{ glyph: "resources", label: "River", v: "Source", s: "intake", c: T.water },
			{ glyph: "control", label: "Pump", v: `${(k.pump_total_hours || 0).toFixed(0)} hrs`, s: `${charts.fmtNum(k.elec_total_units)} kWh`, c: T.elec },
			{ glyph: "drop", label: "Dam", v: `${charts.fmtNum(k.pump_total_m3)} m³`, s: "stored", c: T.pump },
			{ glyph: "map", label: "Sections", v: `${charts.fmtNum(totalSection)} m³`, s: "applied", c: T.violet },
		];
		const arrows = [
			`${charts.fmtNum(k.pump_total_m3)} m³`,
			`${(k.pump_efficiency || 0).toFixed(2)} m³/kWh`,
			`${charts.fmtNum(totalSection)} m³`,
		];
		let html = "";
		nodes.forEach((n, i) => {
			html += `<div class="ui-flow-node" style="--kc:${n.c}">
	<div class="ic">${ic(n.glyph)}</div>
	<div class="l">${charts.esc(n.label)}</div>
	<div class="v">${charts.esc(n.v)}</div>
	<div class="s">${charts.esc(n.s)}</div>
</div>`;
			if (i < arrows.length) {
				html += `<div class="ui-flow-arrow"><div class="line"></div><div class="lbl">${charts.esc(arrows[i])}</div></div>`;
			}
		});
		this.el.querySelector("#r-flow").innerHTML = html;
	},

	renderMonthly(data) {
		const { charts } = this.ctx;
		const pm = {};
		(data.pumping.all_readings || []).forEach((r) => {
			const m = String(r.date).slice(0, 7);
			pm[m] = pm[m] || { vol: 0, kwh: 0 };
			pm[m].vol += r.volume_m3 || 0;
			pm[m].kwh += r.elec_used || 0;
		});
		const em = {};
		(data.electricity.all_readings || []).forEach((r) => {
			const m = String(r.date).slice(0, 7);
			em[m] = (em[m] || 0) + (r.units_used || 0);
		});
		/* Guard against the 1900-ish rows that exist in the meter history. */
		const months = [...new Set([...Object.keys(pm), ...Object.keys(em)])]
			.filter((m) => /^\d{4}-\d{2}$/.test(m) && m > "2020-01")
			.sort();
		const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
		const labels = months.map((m) => {
			const [y, mo] = m.split("-");
			return `${MON[+mo - 1]} ${y.slice(2)}`;
		});
		const opts = { xLabels: labels, xRaw: true, tooltip: true, margin: { t: 12, r: 14, b: 28, l: 46 } };
		charts.mkChart(this.el.querySelector("#r-mon-pump"), [{ label: "m³", color: T.pump, type: "bar", values: months.map((m) => +(pm[m]?.vol || 0).toFixed(0)), unit: "m³" }], 420, 170, opts);
		charts.mkChart(this.el.querySelector("#r-mon-elec"), [{ label: "kWh", color: T.elec, type: "bar", values: months.map((m) => +(em[m] || 0).toFixed(0)), unit: "kWh" }], 420, 170, opts);
		charts.mkChart(
			this.el.querySelector("#r-mon-eff"),
			[{ label: "m³/kWh", color: T.water, values: months.map((m) => (pm[m] && pm[m].kwh > 0 ? +(pm[m].vol / pm[m].kwh).toFixed(2) : null)), unit: "m³/kWh", dec: 2 }],
			420,
			170,
			{ ...opts, noFill: true }
		);
	},

	renderBars(data, weeks) {
		const profiles = data.profiles || {};
		this.bars(this.el.querySelector("#r-bars-water"), data.water.by_section, profiles, weeks, "water_target_m3_per_week", "m³");
		this.bars(this.el.querySelector("#r-bars-elec"), data.electricity.by_section, profiles, weeks, "kwh_target_per_week", "kWh");
	},

	bars(host, bySection, profiles, weeks, targetField, unit) {
		const { charts } = this.ctx;
		const entries = Object.entries(bySection || {});
		if (!entries.length) {
			host.innerHTML = `<div class="ui-empty small">No ${unit === "kWh" ? "electricity" : "water meter"} data in this period.</div>`;
			return;
		}
		entries.sort(([a], [b]) => a.localeCompare(b));
		const targetFor = (name) => (Number((profiles[name] || {})[targetField]) || 0) * weeks;
		/* Scale must fit both usage and target so the target tick stays on the track. */
		const max = Math.max(...entries.map(([, v]) => v.total || 0), ...entries.map(([n]) => targetFor(n)), 1);

		host.innerHTML = entries
			.map(([name, v], i) => {
				const color = sectionColor(name, i);
				const pct = Math.max(0, Math.min(100, ((v.total || 0) / max) * 100));
				const tgt = targetFor(name);
				const tgtPct = tgt > 0 ? Math.max(0, Math.min(100, (tgt / max) * 100)) : null;
				const over = tgt > 0 && v.total > tgt;
				const share = tgt > 0 ? ` · ${Math.round((v.total / tgt) * 100)}%` : "";
				return `<div class="ui-bar">
	<div class="n">${charts.esc(shortSection(name))}</div>
	<div class="track">
		<i style="width:${pct}%;background:${over ? "var(--ui-hot)" : color}"></i>
		${tgtPct != null ? `<div class="tick" style="left:${tgtPct}%" title="target ${charts.fmtNum(tgt)} ${unit}"></div>` : ""}
	</div>
	<div class="v" style="${over ? "color:var(--ui-hot)" : ""}">${charts.fmtNum(v.total)} <small>${unit}${share}</small></div>
</div>`;
			})
			.join("");
	},

	renderTargets(profiles) {
		const { charts } = this.ctx;
		const body = this.el.querySelector("#r-targets");
		const entries = Object.entries(profiles);
		if (!entries.length) {
			body.innerHTML = '<tr><td colspan="4"><div class="ui-empty small">No Irrigation Pump Profile rows. Without one, the planner falls back to 168 hr/wk.</div></td></tr>';
			return;
		}
		entries.sort(([a], [b]) => a.localeCompare(b));
		body.innerHTML = entries
			.map(([sec, p], i) => `<tr>
	<td><span class="ui-dot" style="background:${sectionColor(sec, i)}"></span><b>${charts.esc(shortSection(sec))}</b></td>
	<td>${charts.esc(p.pump_name || "—")}</td>
	<td class="num" style="color:${T.water}">${charts.fmtNum(p.water_target_m3_per_week || 0)}</td>
	<td class="num" style="color:${T.elec}">${charts.fmtNum(p.kwh_target_per_week || 0)}</td>
</tr>`)
			.join("");
	},

	renderPerformance(data, weeks) {
		const { charts } = this.ctx;
		const host = this.el.querySelector("#r-perf");
		const profiles = data.profiles || {};
		const water = (data.water && data.water.by_section) || {};
		const elec = (data.electricity && data.electricity.by_section) || {};
		const startISO = (data.meta && data.meta.start_date) || "";
		const labels = weekLabels(startISO, weeks);

		const keys = [...new Set([...Object.keys(profiles), ...Object.keys(water), ...Object.keys(elec)])]
			.filter((s) => s && s !== "Unknown")
			.sort();
		if (!keys.length) {
			host.innerHTML = '<div class="ui-empty small">No section data.</div>';
			return;
		}

		host.innerHTML = keys
			.map((sec, i) => {
				const p = profiles[sec] || {};
				const color = sectionColor(sec, i);
				const wWeekly = weeklyBuckets((water[sec] || {}).readings, startISO, weeks);
				const eWeekly = weeklyBuckets((elec[sec] || {}).readings, startISO, weeks);
				const wTarget = Number(p.water_target_m3_per_week) || 0;
				const eTarget = Number(p.kwh_target_per_week) || 0;
				const wAvg = wWeekly.reduce((a, b) => a + b, 0) / Math.max(1, weeks);
				const eAvg = eWeekly.reduce((a, b) => a + b, 0) / Math.max(1, weeks);
				const wPct = wTarget > 0 ? Math.round((wAvg / wTarget) * 100) : null;
				const ePct = eTarget > 0 ? Math.round((eAvg / eTarget) * 100) : null;
				const tone = (pct) => (pct == null ? "ink" : pct > 100 ? "hot" : "ok");
				return `<div class="ui-sensor" style="--kc:${color}">
	<div class="ui-sensor-head">
		<div><div class="t">${charts.esc(shortSection(sec))}</div><div class="id">${weeks} wk · ${charts.esc(p.pump_name || "no profile")}</div></div>
	</div>
	<div class="ui-sensor-meta">
		<b style="color:${T.water}">${charts.fmtNum(wAvg)}</b> m³/wk avg · target ${charts.fmtNum(wTarget)}
		${wPct != null ? ` · <span class="ui-sev ${tone(wPct)}">${wPct}%</span>` : ""}
	</div>
	<div id="r-perf-w-${slug(sec)}"></div>
	<div class="ui-sensor-meta" style="margin-top:8px">
		<b style="color:${T.elec}">${charts.fmtNum(eAvg)}</b> kWh/wk avg · target ${charts.fmtNum(eTarget)}
		${ePct != null ? ` · <span class="ui-sev ${tone(ePct)}">${ePct}%</span>` : ""}
	</div>
	<div id="r-perf-e-${slug(sec)}"></div>
</div>`;
			})
			.join("");

		/* Paint once the hosts exist in the DOM. */
		keys.forEach((sec) => {
			const p = profiles[sec] || {};
			const wWeekly = weeklyBuckets((water[sec] || {}).readings, startISO, weeks);
			const eWeekly = weeklyBuckets((elec[sec] || {}).readings, startISO, weeks);
			const wTarget = Number(p.water_target_m3_per_week) || 0;
			const eTarget = Number(p.kwh_target_per_week) || 0;
			const opts = { xLabels: labels, tooltip: true, margin: { t: 8, r: 12, b: 24, l: 42 } };

			const wSeries = [{ label: "m³/wk", color: T.water, type: "bar", values: wWeekly, unit: "m³" }];
			if (wTarget > 0) wSeries.push({ label: "target", color: "var(--ui-teal)", values: Array(weeks).fill(wTarget), dash: "5 3", width: 1.5, unit: "m³", noPoints: true });
			charts.mkChart(this.el.querySelector(`#r-perf-w-${slug(sec)}`), wSeries, 420, 130, opts);

			const eSeries = [{ label: "kWh/wk", color: T.elec, type: "bar", values: eWeekly, unit: "kWh" }];
			if (eTarget > 0) eSeries.push({ label: "target", color: "var(--ui-warn-deep)", values: Array(weeks).fill(eTarget), dash: "5 3", width: 1.5, unit: "kWh", noPoints: true });
			charts.mkChart(this.el.querySelector(`#r-perf-e-${slug(sec)}`), eSeries, 420, 130, opts);
		});
	},

	renderDaily(data) {
		const { charts } = this.ctx;
		const w = data.water.daily_series || [];
		const p = data.pumping.daily_series || [];
		if (w.length) {
			charts.mkChart(this.el.querySelector("#r-daily-water"), [{ label: "m³", color: T.water, values: w.map((r) => r.units || 0), unit: "m³" }], 560, 170, { xLabels: w.map((r) => r.date), tooltip: true });
		} else {
			this.el.querySelector("#r-daily-water").innerHTML = '<div class="ui-empty small">No water readings</div>';
		}
		if (p.length) {
			charts.mkChart(
				this.el.querySelector("#r-daily-pump"),
				[
					{ label: "m³", color: T.pump, values: p.map((r) => r.volume_m3 || 0), unit: "m³" },
					{ label: "hrs", color: T.violet, values: p.map((r) => r.hours || 0), dash: "3 3", width: 1.5, unit: "hrs", noPoints: true },
				],
				560,
				170,
				{ xLabels: p.map((r) => r.date), tooltip: true }
			);
		} else {
			this.el.querySelector("#r-daily-pump").innerHTML = '<div class="ui-empty small">No pump data</div>';
		}
	},

	renderTables(data) {
		const { charts } = this.ctx;

		/* Pump sessions carry a few malformed historical dates; filter as the
		 * old page did rather than rendering "1900" rows. */
		const sessions = (data.pumping.all_readings || []).filter(
			(r) => r.volume_m3 > 0 && /^(19[5-9]\d|2\d{3})/.test(String(r.date))
		);
		this.el.querySelector("#r-pump-count").textContent = `${sessions.length} session(s)`;
		this.el.querySelector("#r-pump-rows").innerHTML = sessions.length
			? sessions
					.slice()
					.reverse()
					.map((r) => `<tr><td>${charts.fmtDate(r.date)}</td><td class="num">${(r.total_hours || 0).toFixed(1)}</td><td class="num">${(r.elec_used || 0).toFixed(0)}</td><td class="num"><b style="color:${T.pump}">${(r.volume_m3 || 0).toFixed(0)}</b></td></tr>`)
					.join("")
			: '<tr><td colspan="4"><div class="ui-empty small">No sessions</div></td></tr>';

		const timeline = sessions.slice(-30).reverse();
		this.el.querySelector("#r-tl-count").textContent = `${timeline.length} shown`;
		this.el.querySelector("#r-timeline").innerHTML = timeline.length
			? timeline
					.map((r) => `<div class="ui-tl"><div class="d">${charts.fmtDate(r.date)}</div><div class="m">${(r.total_hours || 0).toFixed(1)} hrs · ${(r.elec_used || 0).toFixed(0)} kWh</div><div class="v">${(r.volume_m3 || 0).toFixed(0)} m³</div></div>`)
					.join("")
			: '<div class="ui-empty small">No pump sessions in period.</div>';

		const water = data.water.all_readings || [];
		this.el.querySelector("#r-water-count").textContent = `${water.length} reading(s)`;
		this.el.querySelector("#r-water-rows").innerHTML = water.length
			? water
					.slice()
					.reverse()
					.map((r) => `<tr><td>${charts.fmtDate(r.date)}</td><td>${charts.esc(shortSection(r.section))}</td><td class="num"><b style="color:${T.water}">${(r.units_used || 0).toFixed(1)}</b></td></tr>`)
					.join("")
			: '<tr><td colspan="3"><div class="ui-empty small">No readings</div></td></tr>';

		const elec = data.electricity.all_readings || [];
		this.el.querySelector("#r-elec-count").textContent = `${elec.length} reading(s)`;
		this.el.querySelector("#r-elec-rows").innerHTML = elec.length
			? elec
					.slice()
					.reverse()
					.map((r) => `<tr><td>${charts.fmtDate(r.date)}</td><td>${charts.esc(shortSection(r.section))}</td><td class="num"><b style="color:${T.elec}">${(r.units_used || 0).toFixed(1)}</b></td></tr>`)
					.join("")
			: '<tr><td colspan="3"><div class="ui-empty small">No readings</div></td></tr>';
	},

	unmount() {
		if (this.unsubscribe) this.unsubscribe();
	},
};
