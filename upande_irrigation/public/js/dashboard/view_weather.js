/* Weather — daily station observations, plus the entry form.
 *
 * Charts ported from meniscus's weather tab. The entry card is new: it POSTs a
 * plain Weather Reading so events.weather_reading.compute_derived runs and fills
 * in et_pan, et_crop, GDD, SWD and the Z-value. Nothing agronomic is
 * recalculated here — the browser only collects the four operator inputs.
 */

import { irrometerMap } from "./irrometer_map.js";
import { pagehead, kpi, statusStrip, icon } from "./shell.js";

const T = {
	rain: "var(--ui-rain)",
	eto: "var(--ui-eto)",
	heat: "var(--ui-hot)",
	cool: "var(--ui-cool)",
	violet: "var(--ui-violet)",
	ok: "var(--ui-ok)",
	warn: "var(--ui-warn)",
	clay: "var(--ui-clay)",
};

/* "23HA_SECTION - KL" → "23 Ha", so a tab bar stays readable. */
function shortSection(name) {
	const base = String(name || "")
		.replace(/ - [A-Z]{2}$/, "")
		.replace("_SECTION", "")
		.trim();
	return base.endsWith("HA") ? `${base.slice(0, -2)} Ha` : base || "—";
}

function tension(cb) {
	if (cb == null) return { cls: "ink", label: "—", color: "var(--ui-mute)" };
	if (cb < 20) return { cls: "ok", label: "Wet", color: "var(--ui-ok)" };
	if (cb < 40) return { cls: "warn", label: "Optimal", color: "var(--ui-warn)" };
	if (cb < 60) return { cls: "clay", label: "Drying", color: "var(--ui-clay)" };
	return { cls: "hot", label: "Stressed", color: "var(--ui-hot)" };
}

function zTone(z) {
	if (z >= 20) return { cls: "hot", color: "var(--ui-hot)" };
	if (z >= 15) return { cls: "clay", color: "var(--ui-clay)" };
	if (z >= 5) return { cls: "warn", color: "var(--ui-warn)" };
	return { cls: "ok", color: "var(--ui-ok)" };
}

export default {
	id: "weather",
	label: "Weather",

	mount(el, ctx) {
		this.ctx = ctx;
		this.el = el;
		el.innerHTML = `
${pagehead("Weather Station", "Daily observations", '<span id="wx-period"></span>')}
<div class="status" id="wx-status"></div>

<div class="card" id="wx-entry-card">
	<div class="card__head">
		<h3>${icon("weather")}Today's reading</h3>
		<span class="meta">ET, SWD and the Z-value are computed on save</span>
	</div>
	<div id="wx-entry"><div class="empty small">Loading…</div></div>
</div>

<div class="kpi-grid stagger" id="wx-kpis"></div>

<div class="card">
	<div class="card__head"><h3>Rainfall</h3><span class="meta" id="wx-wetdry">mm · daily, against reference ETo</span></div>
	<div id="wx-rain"></div>
	<div class="clegend">
		<span><i style="background:${T.rain}"></i>Rainfall</span>
		<span><i class="ln" style="background:${T.eto}"></i>Reference ETo</span>
	</div>
</div>

<div class="card">
	<div class="card__head"><h3>Temperature</h3><span class="meta">°C · min / mean / max</span></div>
	<div id="wx-temp"></div>
	<div class="clegend">
		<span><i class="ln" style="background:${T.cool}"></i>Min</span>
		<span><i class="ln" style="background:${T.clay}"></i>Mean</span>
		<span><i class="ln" style="background:${T.heat}"></i>Max</span>
	</div>
</div>

<div class="row-2-eq">
	<div class="card">
		<div class="card__head"><h3>Pan evaporation</h3><span class="meta">mm/day</span></div>
		<div id="wx-evap"></div>
	</div>
	<div class="card">
		<div class="card__head"><h3>Soil water deficit</h3><span class="meta">mm · running balance</span></div>
		<div id="wx-swd"></div>
	</div>
</div>

<div class="card">
	<div class="card__head">
		<h3>Anthracnose risk (Z)</h3>
		<span class="meta"><span class="sev ink" id="wx-z-badge">—</span> &nbsp; z = −58.99 + 3.22·T̄ + 0.18·rain₇d</span>
	</div>
	<div id="wx-z"></div>
	<div class="clegend">
		<span><i class="ln" style="background:${T.ok}"></i>&lt; 5 low</span>
		<span><i class="ln" style="background:${T.warn}"></i>5–15 spore release</span>
		<span><i class="ln" style="background:${T.clay}"></i>15–20 infection risk</span>
		<span><i class="ln" style="background:${T.heat}"></i>≥ 20 fungicide required</span>
	</div>
</div>

<div class="card">
	<div class="card__head">
		<h3>Soil tension · irrometers</h3>
		<span class="meta">centibars · latest reading per block · click a block for its readings</span>
	</div>
	<div id="wx-irro-map"></div>
	<div class="stabs" id="wx-irro-tabs"></div>
	<div class="scrollbox"><div class="irro-grid stagger" id="wx-irro"></div></div>
	<div class="clegend" style="margin-top:12px">
		<span><i style="background:${T.ok}"></i>0–20 wet</span>
		<span><i style="background:${T.warn}"></i>20–40 optimal</span>
		<span><i style="background:${T.clay}"></i>40–60 drying</span>
		<span><i style="background:${T.heat}"></i>60+ stressed</span>
	</div>
</div>

<div class="card">
	<div class="card__head"><h3>Irrigation sections</h3><span class="meta" id="wx-sections-count"></span></div>
	<div class="tablewrap scroll">
		<table class="table">
			<thead><tr><th>Section</th><th class="num">Blocks</th><th class="num">Shifts</th><th class="num">Valves</th><th class="num">Coverage %</th><th class="num">Rate mm/hr</th><th class="num">Status</th></tr></thead>
			<tbody id="wx-sections"></tbody>
		</table>
	</div>
</div>

<div class="card">
	<div class="card__head"><h3>Blocks</h3><span class="meta" id="wx-blocks-count"></span></div>
	<div class="tablewrap scroll">
		<table class="table">
			<thead><tr><th>Block</th><th>Section</th><th>1 ft</th><th>2 ft</th><th>Read on</th><th class="num">Status</th></tr></thead>
			<tbody id="wx-blocks"></tbody>
		</table>
	</div>
</div>`;

		this.unsubscribe = ctx.onFilterChange(() => {});
	},

	/* ── entry form ───────────────────────────────────────────── */

	renderEntry(data) {
		const { charts, filters, shell } = this.ctx;
		const host = this.el.querySelector("#wx-entry");
		const today = (window.IRRIGATION_BOOT || {}).today || new Date().toISOString().slice(0, 10);

		/* One farm must be chosen to write a reading; compute_derived throws
		 * otherwise. Prefer the active filter, else the only farm available. */
		const farms = shell.farms || [];
		const farm = filters.farm || (farms.length === 1 ? farms[0] : "");

		const todays = (data.weather || []).filter(
			(r) => String(r.date).slice(0, 10) === today && (!farm || r.farm === farm)
		);

		if (todays.length) {
			const r = todays[0];
			host.innerHTML = `
<div class="alert ok" style="margin-bottom:12px">${icon("check")}<span>Logged for ${charts.esc(r.farm || farm)} on ${charts.esc(today)}.</span></div>
<div class="logged">
	<div><small>Rainfall</small><b>${charts.fmtNum(r.rainfall_mm)} mm</b></div>
	<div><small>Pan depth</small><b>${charts.fmtNum(r.pan_depth_mm)} mm</b></div>
	<div><small>ET crop</small><b>${charts.fmtNum(r.et_crop, 2)} mm</b></div>
	<div><small>SWD</small><b>${charts.fmtNum(r.swd, 1)} mm</b></div>
	<div><small>Z value</small><b>${charts.fmtNum(r.z_value, 1)}</b></div>
	<div><small>GDD</small><b>${charts.fmtNum(r.cumulative_temperature, 0)}</b></div>
</div>
<div class="card__tools" style="margin-top:12px">
	<a class="btn ghost small" href="/app/weather-reading/${encodeURIComponent(r.name)}" style="text-decoration:none">Open record</a>
</div>`;
			return;
		}

		/* Never pre-select a farm the operator did not choose. A browser selects
		 * the first <option> by default, which silently aimed the form at
		 * whichever farm sorted first — a reading filed against the wrong farm
		 * corrupts that farm's SWD and GDD chain from that day forward. */
		const farmField = farms.length
			? `<div>
	<label for="wx-f-farm">Farm</label>
	<select class="select" id="wx-f-farm">
		${farm ? "" : '<option value="">Choose a farm…</option>'}
		${farms.map((f) => `<option value="${charts.esc(f)}"${f === farm ? " selected" : ""}>${charts.esc(f)}</option>`).join("")}
	</select>
</div>`
			: "";

		host.innerHTML = `
<div class="entry">
	${farmField}
	<div>
		<label for="wx-f-date">Date</label>
		<input class="input" type="date" id="wx-f-date" value="${today}" max="${today}">
	</div>
	<div>
		<label for="wx-f-rain">Rainfall (mm)</label>
		<input class="input" type="number" id="wx-f-rain" step="0.1" min="0" placeholder="0.0">
	</div>
	<div>
		<label for="wx-f-cups">Pan cups</label>
		<input class="input" type="number" id="wx-f-cups" step="0.1" placeholder="0.0">
	</div>
	<div>
		<label for="wx-f-min">Min temp (°C)</label>
		<input class="input" type="number" id="wx-f-min" step="0.1" placeholder="—">
	</div>
	<div>
		<label for="wx-f-max">Max temp (°C)</label>
		<input class="input" type="number" id="wx-f-max" step="0.1" placeholder="—">
	</div>
	<div class="entry__actions">
		<button class="btn" id="wx-f-save" type="button">Log reading</button>
	</div>
</div>
<div id="wx-entry-status" class="status" style="margin-top:12px"></div>`;

		host.querySelector("#wx-f-save").addEventListener("click", () => this.submitEntry());
	},

	async submitEntry() {
		const { api, charts } = this.ctx;
		const host = this.el.querySelector("#wx-entry");
		const status = host.querySelector("#wx-entry-status");
		const btn = host.querySelector("#wx-f-save");
		const val = (id) => {
			const el = host.querySelector(id);
			if (!el || el.value === "") return null;
			return el.value;
		};

		const farmEl = host.querySelector("#wx-f-farm");
		const farm = farmEl ? farmEl.value : this.ctx.filters.farm;
		if (!farm) {
			statusStrip(status, "Choose a farm — a reading belongs to exactly one.");
			if (farmEl) farmEl.focus();
			return;
		}
		const date = val("#wx-f-date");
		if (!date) {
			statusStrip(status, "Date is required.");
			return;
		}

		const doc = {
			doctype: "Weather Reading",
			farm,
			date,
			rainfall_mm: Number(val("#wx-f-rain") || 0),
			pan_cups: Number(val("#wx-f-cups") || 0),
		};
		const tmin = val("#wx-f-min");
		const tmax = val("#wx-f-max");
		if (tmin !== null) doc.minimum_temperature = Number(tmin);
		if (tmax !== null) doc.maximum_temperature = Number(tmax);

		btn.disabled = true;
		btn.textContent = "Saving…";
		statusStrip(status, "");

		try {
			const { serverMessages } = await api.insertDoc(doc);
			/* msgprint output — the negative pan-evaporation anomaly warning —
			 * matters to the operator, so show it rather than dropping it. */
			if (serverMessages && serverMessages.length) {
				status.innerHTML = `<div class="alert warn">${icon("alert")}<span>${charts.esc(serverMessages.join(" "))}</span></div>`;
			}
			await this.refresh({ keepStatus: serverMessages && serverMessages.length > 0 });
		} catch (err) {
			/* compute_derived's throws are already operator-worded. */
			statusStrip(status, err.message);
			btn.disabled = false;
			btn.textContent = "Log reading";
		}
	},

	/* ── charts ───────────────────────────────────────────────── */

	async refresh({ keepStatus = false } = {}) {
		const { api, charts, filters, shell } = this.ctx;
		const status = this.el.querySelector("#wx-status");
		if (!keepStatus) statusStrip(status, "");

		/* Sections come from the shift mapping, not the weather window, so they
		 * load alongside and fail on their own. */
		const sectionsReq = api
			.get("upande_irrigation.api.weather.sections", { farm: filters.farm })
			.then(({ data: d }) => (d && d.sections) || [])
			.catch((err) => {
				console.warn("[irrigation] sections failed", err);
				return null;
			});

		let data;
		try {
			({ data } = await api.get("upande_irrigation.api.weather.fetch", {
				days: filters.days,
				farm: filters.farm,
				start_date: filters.from,
				end_date: filters.to,
			}));
		} catch (err) {
			statusStrip(status, `Could not load weather data: ${err.message}`);
			return;
		}
		if (!data) return;

		const weather = data.weather || [];
		const blocks = data.blocks || [];
		const meta = data.meta || {};
		if (data.farms) shell.setFarms(data.farms);

		const period = this.el.querySelector("#wx-period");
		if (period) {
			period.textContent = `${meta.start_date} → ${meta.end_date} · ${weather.length} ${weather.length === 1 ? "reading" : "readings"}`;
		}
		if (data.blocks_error) {
			statusStrip(status, `Block data unavailable: ${data.blocks_error}`, "warn");
		}

		this.renderEntry(data);
		this.renderKpis(data.kpis, weather);
		this.renderCharts(weather, meta);
		this.renderIrrometer(blocks);
		this.renderIrrometerMap(blocks);
		this.renderSections(await sectionsReq);
		this.renderBlocks(blocks);
	},

	renderSections(sections) {
		const { charts } = this.ctx;
		const body = this.el.querySelector("#wx-sections");
		const count = this.el.querySelector("#wx-sections-count");
		if (sections == null) {
			count.textContent = "";
			body.innerHTML = '<tr><td colspan="7"><div class="empty small">Sections could not be loaded.</div></td></tr>';
			return;
		}
		count.textContent = `${sections.length} ${sections.length === 1 ? "section" : "sections"} · from the shift mapping`;
		if (!sections.length) {
			body.innerHTML = '<tr><td colspan="7"><div class="empty small">No shifts are mapped to blocks yet — set them on Irrigation Scheduler.</div></td></tr>';
			return;
		}
		/* A blank rate/coverage means every shift inherits the farm default from
		 * Irrigation Settings — say so rather than print a dash that reads as missing data. */
		const inherit = '<span style="color:var(--ui-mute)">farm default</span>';
		body.innerHTML = sections
			.map(
				(r) => `<tr>
	<td><b>${charts.esc(shortSection(r.section) || r.section)}</b>${r.farm ? `<div class="list__meta">${charts.esc(r.farm)}</div>` : ""}</td>
	<td class="num">${r.blocks}</td>
	<td class="num">${r.shifts}</td>
	<td class="num">${r.valves}</td>
	<td class="num">${r.coverage_pct == null ? inherit : charts.fmtNum(r.coverage_pct, 0)}</td>
	<td class="num">${r.application_rate_mm_hr == null ? inherit : charts.fmtNum(r.application_rate_mm_hr, 1)}</td>
	<td class="num"><span class="sev ${r.active ? "lo" : "ink"}">${r.active ? "Active" : "Idle"}</span></td>
</tr>`
			)
			.join("");
	},

	renderKpis(kpis, weather) {
		const { charts } = this.ctx;
		const host = this.el.querySelector("#wx-kpis");
		if (!kpis || !kpis.reading_count) {
			host.innerHTML = `<div class="kpi" style="grid-column:1/-1"><div class="empty">No readings in this period.</div></div>`;
			return;
		}
		const sp = (vals, color) => charts.sparkline(vals, color);
		const range =
			kpis.min_temperature != null ? `${kpis.min_temperature}° – ${kpis.max_temperature}°` : "—";
		const idx = (html, i) => html.replace('<div class="kpi"', `<div class="kpi"`);
		host.innerHTML = [
			kpi(T.rain, "Rainfall", charts.fmtNum(kpis.total_rainfall), "mm", `${kpis.wet_days} wet · ${kpis.dry_days} dry`, sp(weather.map((r) => r.rainfall_mm || 0), T.rain)),
			kpi(T.eto, "Pan evap", charts.fmtNum(kpis.total_evaporation), "mm", `${kpis.reading_count} readings`, sp(weather.map((r) => r.daily_evaporation || 0), T.eto)),
			kpi(T.clay, "Reference ETo", charts.fmtNum(kpis.total_eto), "mm", "K-pan 0.75", sp(weather.map((r) => r.eto || 0), T.clay)),
			kpi(T.violet, "Mean temp", charts.fmtNum(kpis.avg_mean_temperature), "°C", range, sp(weather.map((r) => r.mean_temperature).filter((v) => v != null), T.violet)),
			kpi(kpis.water_deficit > 0 ? T.heat : T.ok, "Deficit", charts.fmtNum(kpis.water_deficit), "mm", kpis.water_deficit > 0 ? "irrigation needed" : "crop demand met", sp(weather.map((r) => Math.max(0, (r.eto || 0) - (r.rainfall_mm || 0))), T.heat)),
		].join("");
	},

	/* Every chart here shares one continuous daily axis spanning the selected
	 * period, with null for days the station never recorded. Plotting readings
	 * by array index — as the old page did — compressed a sparse month into a
	 * handful of adjacent points and invented a trend between them. */
	renderCharts(weather, meta) {
		const { charts } = this.ctx;
		const axis = charts.dailyAxis(weather, meta.start_date, meta.end_date);
		const dates = axis.labels;

		charts.mkChart(
			this.el.querySelector("#wx-rain"),
			[
				{ label: "Rainfall", color: T.rain, type: "bar", values: axis.pick("rainfall_mm"), unit: "mm" },
				{ label: "ETo", color: T.eto, values: axis.pick("eto"), dash: "4 3", width: 1.8, unit: "mm", noPoints: true },
			],
			1200,
			210,
			{ xLabels: dates, tooltip: true }
		);
		const wet = weather.filter((r) => (r.rainfall_mm || 0) > 0.1).length;
		const wd = this.el.querySelector("#wx-wetdry");
		if (wd) wd.textContent = `${wet} wet · ${weather.length - wet} dry of ${weather.length} logged`;

		/* A 0 °C reading on a Kenyan farm means "not recorded", which is exactly
		 * how events/irrigation_planner.py treats it (temp > 0). Charting the
		 * zeros drew the series down to the axis. */
		const realTemp = (v) => (v == null || Number(v) <= 0 ? null : Number(v));
		const lo = axis.pick(null, (r) => realTemp(r.minimum_temperature));
		const hi = axis.pick(null, (r) => realTemp(r.maximum_temperature));
		const mean = axis.pick(null, (r) => {
			const a = realTemp(r.minimum_temperature);
			const b = realTemp(r.maximum_temperature);
			if (r.mean_temperature != null && Number(r.mean_temperature) > 0) return Number(r.mean_temperature);
			return a != null && b != null ? (a + b) / 2 : null;
		});
		const tempHost = this.el.querySelector("#wx-temp");
		const anyTemp = lo.some((v) => v != null) || hi.some((v) => v != null);
		if (!anyTemp) {
			tempHost.innerHTML = '<div class="empty small">No temperature readings in this period.</div>';
		} else {
			const loVals = lo.filter((v) => v != null);
			const hiVals = hi.filter((v) => v != null);
			charts.mkChart(
				tempHost,
				[
					{ label: "Max", color: T.heat, values: hi, width: 2.2, unit: "°C", noPoints: true },
					{ label: "Mean", color: T.clay, values: mean, width: 3, unit: "°C", noPoints: true },
					{ label: "Min", color: T.cool, values: lo, width: 2.2, unit: "°C", noPoints: true },
				],
				1200,
				210,
				{
					xLabels: dates,
					tooltip: true,
					noFill: true,
					/* The shaded min–max envelope Meniscus drew behind the lines. */
					band: { lo, hi, color: T.heat },
					yMin: Math.floor(Math.min(...loVals) - 2),
					yMax: Math.ceil(Math.max(...hiVals) + 2),
				}
			);
		}

		charts.mkChart(
			this.el.querySelector("#wx-evap"),
			[{ label: "Evaporation", color: T.eto, values: axis.pick("daily_evaporation"), unit: "mm" }],
			560,
			190,
			{ xLabels: dates, tooltip: true }
		);

		const swdVals = axis.pick("swd");
		const swdHost = this.el.querySelector("#wx-swd");
		if (swdVals.some((v) => v != null)) {
			const present = swdVals.filter((v) => v != null);
			charts.mkChart(
				swdHost,
				[{ label: "SWD", color: T.cool, values: swdVals, unit: "mm" }],
				560,
				190,
				{
					xLabels: dates,
					tooltip: true,
					noFill: true,
					yMin: Math.floor(Math.min(...present, 0)),
					yMax: Math.ceil(Math.max(...present, 0)),
				}
			);
		} else {
			swdHost.innerHTML = '<div class="empty small">No soil water deficit recorded yet.</div>';
		}

		const zVals = axis.pick("z_value");
		const badge = this.el.querySelector("#wx-z-badge");
		const zHost = this.el.querySelector("#wx-z");
		if (!zVals.some((v) => v != null)) {
			zHost.innerHTML = '<div class="empty small">The Z-value needs a temperature reading to compute.</div>';
			if (badge) {
				badge.textContent = "—";
				badge.className = "sev ink";
			}
			return;
		}
		const withZ = weather.filter((r) => r.z_value != null);
		const latest = withZ[withZ.length - 1];
		const zv = Number(latest.z_value) || 0;
		if (badge) {
			badge.textContent = `${zv.toFixed(1)} · ${latest.z_risk_level || "—"}`;
			badge.className = `sev ${zTone(zv).cls}`;
		}
		const n = dates.length;
		charts.mkChart(
			zHost,
			[
				{ label: "Z value", color: T.violet, values: zVals, width: 3, unit: "" },
				{ label: "Spore release (5)", color: T.warn, values: Array(n).fill(5), dash: "3 3", width: 1.2, noPoints: true },
				{ label: "Infection risk (15)", color: T.clay, values: Array(n).fill(15), dash: "3 3", width: 1.2, noPoints: true },
				{ label: "Fungicide (20)", color: T.heat, values: Array(n).fill(20), dash: "3 3", width: 1.2, noPoints: true },
			],
			1200,
			210,
			{ xLabels: dates, tooltip: true, noFill: true }
		);
	},

	/* One tab per irrigation section, gauges for the selected one in a panel
	 * that scrolls on its own. Stacking every section as open <details> made a
	 * farm with 78 blocks push the rest of the page off screen. */
	renderIrrometerMap(blocks) {
		if (!this.irroMap) {
			this.irroMap = irrometerMap(this.el.querySelector("#wx-irro-map"), this.ctx, { tension, shortSection });
		}
		/* The map loads its own libraries and geometry; never hold the view up. */
		this.irroMap.render(blocks, this.ctx.filters.farm).catch((err) => console.warn("[irrigation] irrometer map", err));
	},

	renderIrrometer(blocks) {
		const { charts } = this.ctx;
		const tabsHost = this.el.querySelector("#wx-irro-tabs");
		const host = this.el.querySelector("#wx-irro");

		if (!blocks.length) {
			tabsHost.innerHTML = "";
			host.innerHTML = '<div class="empty small">No blocks are mapped to a section yet.</div>';
			return;
		}

		const grouped = {};
		blocks.forEach((b) => {
			const key = b.parent_section || "Unassigned";
			(grouped[key] = grouped[key] || []).push(b);
		});
		const sections = Object.keys(grouped).sort();

		/* Keep the operator's tab across refreshes; fall back to the first. */
		if (!this.irroSection || !grouped[this.irroSection]) {
			this.irroSection = sections[0];
		}

		tabsHost.innerHTML = sections
			.map((s) => {
				const list = grouped[s];
				const withData = list.filter((b) => b.irrometer_1ft != null).length;
				return `<button class="stab${s === this.irroSection ? " on" : ""}" type="button" data-section="${charts.esc(s)}" title="${charts.esc(s)}">
	${charts.esc(shortSection(s))}<span class="n">${withData || list.length}</span>
</button>`;
			})
			.join("");

		tabsHost.querySelectorAll("[data-section]").forEach((btn) => {
			btn.addEventListener("click", () => {
				this.irroSection = btn.getAttribute("data-section");
				this.renderIrrometer(blocks);
			});
		});

		const list = grouped[this.irroSection] || [];
		const withData = list.filter((b) => b.irrometer_1ft != null);
		if (!withData.length) {
			host.innerHTML = `<div class="empty">${icon("drop")}No irrometer readings logged for ${charts.esc(shortSection(this.irroSection))} yet. Readings are entered on the Weather Reading form.</div>`;
			return;
		}

		host.innerHTML = list
			.map((b, i) => {
				const t1 = tension(b.irrometer_1ft);
				const t2 = tension(b.irrometer_2ft);
				return `<div class="irro" style="border-left-color:${t1.color}">
	<div class="irro-name">
		<span title="${charts.esc(b.name)}">${charts.esc(b.block_name || b.name)}</span>
		<span class="sev ${t1.cls}">${t1.label}</span>
	</div>
	<div class="irro-gauges">
		<div>
			${charts.arcGauge(b.irrometer_1ft, t1.color)}
			<b style="color:${t1.color}">${b.irrometer_1ft != null ? b.irrometer_1ft : "—"}</b>
			<small>1 ft</small>
		</div>
		<div>
			${charts.arcGauge(b.irrometer_2ft, t2.color)}
			<b style="color:${t2.color}">${b.irrometer_2ft != null ? b.irrometer_2ft : "—"}</b>
			<small>2 ft</small>
		</div>
	</div>
</div>`;
			})
			.join("");
		charts.animateIn(host);
	},

	renderBlocks(blocks) {
		const { charts } = this.ctx;
		const body = this.el.querySelector("#wx-blocks");
		const count = this.el.querySelector("#wx-blocks-count");
		count.textContent = `${blocks.length} ${blocks.length === 1 ? "block" : "blocks"}`;
		if (!blocks.length) {
			body.innerHTML = '<tr><td colspan="6"><div class="empty small">no blocks</div></td></tr>';
			return;
		}
		body.innerHTML = blocks
			.map((b) => {
				const t = tension(b.irrometer_1ft);
				/* No reading means no status — a row of "—" pills reads as data. */
				const status =
					b.irrometer_1ft != null
						? `<span class="sev ${t.cls}">${t.label}</span>`
						: '<span style="color:var(--ui-mute)">not read</span>';
				return `<tr>
	<td><b>${charts.esc(b.block_name || b.name)}</b></td>
	<td>${charts.esc(shortSection(b.parent_section) || "—")}</td>
	<td class="num">${b.irrometer_1ft != null ? b.irrometer_1ft : "—"}</td>
	<td class="num">${b.irrometer_2ft != null ? b.irrometer_2ft : "—"}</td>
	<td>${b.irrometer_date ? charts.fmtDate(b.irrometer_date) : "—"}</td>
	<td class="num">${status}</td>
</tr>`;
			})
			.join("");
	},

	unmount() {
		if (this.unsubscribe) this.unsubscribe();
		if (this.irroMap) this.irroMap.destroy();
		this.irroMap = null;
	},
};
