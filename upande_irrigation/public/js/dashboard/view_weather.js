/* Weather — daily station observations, plus the entry form.
 *
 * Charts ported from meniscus's weather tab. The entry card is new: it POSTs a
 * plain Weather Reading so events.weather_reading.compute_derived runs and fills
 * in et_pan, et_crop, GDD, SWD and the Z-value. Nothing agronomic is
 * recalculated here — the browser only collects the four operator inputs.
 */

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
<div class="ui-status" id="wx-status"></div>

<div class="ui-card" id="wx-entry-card">
	<div class="ui-cardhead">
		<h3>${icon("weather")}Today's reading</h3>
		<span class="meta">ET, SWD and the Z-value are computed on save</span>
	</div>
	<div id="wx-entry"><div class="ui-empty small">Loading…</div></div>
</div>

<div class="ui-kpis" id="wx-kpis"></div>

<div class="ui-card">
	<div class="ui-cardhead"><h3>Rainfall</h3><span class="meta" id="wx-wetdry">mm · daily, against reference ETo</span></div>
	<div id="wx-rain"></div>
	<div class="ui-legend row">
		<span><i style="background:${T.rain}"></i>Rainfall</span>
		<span><i class="ln" style="background:${T.eto}"></i>Reference ETo</span>
	</div>
</div>

<div class="ui-card">
	<div class="ui-cardhead"><h3>Temperature</h3><span class="meta">°C · min / mean / max</span></div>
	<div id="wx-temp"></div>
	<div class="ui-legend row">
		<span><i class="ln" style="background:${T.cool}"></i>Min</span>
		<span><i class="ln" style="background:${T.clay}"></i>Mean</span>
		<span><i class="ln" style="background:${T.heat}"></i>Max</span>
	</div>
</div>

<div class="ui-row2eq">
	<div class="ui-card">
		<div class="ui-cardhead"><h3>Pan evaporation</h3><span class="meta">mm/day</span></div>
		<div id="wx-evap"></div>
	</div>
	<div class="ui-card">
		<div class="ui-cardhead"><h3>Soil water deficit</h3><span class="meta">mm · running balance</span></div>
		<div id="wx-swd"></div>
	</div>
</div>

<div class="ui-card">
	<div class="ui-cardhead">
		<h3>Anthracnose risk (Z)</h3>
		<span class="meta"><span class="ui-sev ink" id="wx-z-badge">—</span> &nbsp; z = −58.99 + 3.22·T̄ + 0.18·rain₇d</span>
	</div>
	<div id="wx-z"></div>
	<div class="ui-legend row">
		<span><i class="ln" style="background:${T.ok}"></i>&lt; 5 low</span>
		<span><i class="ln" style="background:${T.warn}"></i>5–15 spore release</span>
		<span><i class="ln" style="background:${T.clay}"></i>15–20 infection risk</span>
		<span><i class="ln" style="background:${T.heat}"></i>≥ 20 fungicide required</span>
	</div>
</div>

<div class="ui-card">
	<div class="ui-cardhead"><h3>Soil tension · irrometers</h3><span class="meta">centibars · latest reading per block</span></div>
	<div id="wx-irro"></div>
	<div class="ui-legend row">
		<span><i style="background:${T.ok}"></i>0–20 wet</span>
		<span><i style="background:${T.warn}"></i>20–40 optimal</span>
		<span><i style="background:${T.clay}"></i>40–60 drying</span>
		<span><i style="background:${T.heat}"></i>60+ stressed</span>
	</div>
</div>

<div class="ui-card">
	<div class="ui-cardhead"><h3>Blocks</h3><span class="meta" id="wx-blocks-count"></span></div>
	<div class="ui-tablewrap scroll">
		<table class="ui-table">
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
<div class="ui-alert ok" style="margin-bottom:12px">${icon("check")}<span>Logged for ${charts.esc(r.farm || farm)} on ${charts.esc(today)}.</span></div>
<div class="ui-logged">
	<div><small>Rainfall</small><b>${charts.fmtNum(r.rainfall_mm)} mm</b></div>
	<div><small>Pan depth</small><b>${charts.fmtNum(r.pan_depth_mm)} mm</b></div>
	<div><small>ET crop</small><b>${charts.fmtNum(r.et_crop, 2)} mm</b></div>
	<div><small>SWD</small><b>${charts.fmtNum(r.swd, 1)} mm</b></div>
	<div><small>Z value</small><b>${charts.fmtNum(r.z_value, 1)}</b></div>
	<div><small>GDD</small><b>${charts.fmtNum(r.cumulative_temperature, 0)}</b></div>
</div>
<div class="ui-toolbar" style="margin-top:12px">
	<a class="ui-btn ghost small" href="/app/weather-reading/${encodeURIComponent(r.name)}" style="text-decoration:none">Open record</a>
</div>`;
			return;
		}

		const farmField = farms.length
			? `<div>
	<label for="wx-f-farm">Farm</label>
	<select class="ui-select" id="wx-f-farm">
		${farms.map((f) => `<option value="${charts.esc(f)}"${f === farm ? " selected" : ""}>${charts.esc(f)}</option>`).join("")}
	</select>
</div>`
			: "";

		host.innerHTML = `
<div class="ui-entry">
	${farmField}
	<div>
		<label for="wx-f-date">Date</label>
		<input class="ui-input" type="date" id="wx-f-date" value="${today}" max="${today}">
	</div>
	<div>
		<label for="wx-f-rain">Rainfall (mm)</label>
		<input class="ui-input" type="number" id="wx-f-rain" step="0.1" min="0" placeholder="0.0">
	</div>
	<div>
		<label for="wx-f-cups">Pan cups</label>
		<input class="ui-input" type="number" id="wx-f-cups" step="0.1" placeholder="0.0">
	</div>
	<div>
		<label for="wx-f-min">Min temp (°C)</label>
		<input class="ui-input" type="number" id="wx-f-min" step="0.1" placeholder="—">
	</div>
	<div>
		<label for="wx-f-max">Max temp (°C)</label>
		<input class="ui-input" type="number" id="wx-f-max" step="0.1" placeholder="—">
	</div>
	<div class="ui-entry-actions">
		<button class="ui-btn" id="wx-f-save" type="button">Log reading</button>
	</div>
</div>
<div id="wx-entry-status" class="ui-status" style="margin-top:12px"></div>`;

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
			statusStrip(status, "Pick a farm in the sidebar first — a reading belongs to one farm.");
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
				status.innerHTML = `<div class="ui-alert warn">${icon("alert")}<span>${charts.esc(serverMessages.join(" "))}</span></div>`;
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
			period.textContent = `${meta.start_date} → ${meta.end_date} · ${weather.length} reading(s)`;
		}
		if (data.blocks_error) {
			statusStrip(status, `Block data unavailable: ${data.blocks_error}`, "warn");
		}

		this.renderEntry(data);
		this.renderKpis(data.kpis, weather);
		this.renderCharts(weather);
		this.renderIrrometer(blocks);
		this.renderBlocks(blocks);
	},

	renderKpis(kpis, weather) {
		const { charts } = this.ctx;
		const host = this.el.querySelector("#wx-kpis");
		if (!kpis || !kpis.reading_count) {
			host.innerHTML = `<div class="ui-kpi" style="grid-column:1/-1"><div class="ui-empty">No readings in this period.</div></div>`;
			return;
		}
		const sp = (vals, color) => charts.sparkline(vals, color);
		const range =
			kpis.min_temperature != null ? `${kpis.min_temperature}° – ${kpis.max_temperature}°` : "—";
		host.innerHTML =
			kpi(T.rain, "Rainfall", charts.fmtNum(kpis.total_rainfall), "mm", `${kpis.wet_days} wet · ${kpis.dry_days} dry`, sp(weather.map((r) => r.rainfall_mm || 0), T.rain)) +
			kpi(T.eto, "Pan evap", charts.fmtNum(kpis.total_evaporation), "mm", `${kpis.reading_count} readings`, sp(weather.map((r) => r.daily_evaporation || 0), T.eto)) +
			kpi(T.clay, "Reference ETo", charts.fmtNum(kpis.total_eto), "mm", `K-pan ${(this.ctx.filters.kpan || 0.75)}`, sp(weather.map((r) => r.eto || 0), T.clay)) +
			kpi(T.violet, "Mean temp", charts.fmtNum(kpis.avg_mean_temperature), "°C", range, sp(weather.map((r) => r.mean_temperature).filter((v) => v != null), T.violet)) +
			kpi(kpis.water_deficit > 0 ? T.heat : T.ok, "Deficit", charts.fmtNum(kpis.water_deficit), "mm", kpis.water_deficit > 0 ? "irrigation needed" : "crop demand met", sp(weather.map((r) => Math.max(0, (r.eto || 0) - (r.rainfall_mm || 0))), T.heat));
	},

	renderCharts(weather) {
		const { charts } = this.ctx;
		const dates = weather.map((r) => r.date);

		charts.mkChart(
			this.el.querySelector("#wx-rain"),
			[
				{ label: "Rainfall", color: T.rain, type: "bar", values: weather.map((r) => r.rainfall_mm || 0), unit: "mm" },
				{ label: "ETo", color: T.eto, values: weather.map((r) => r.eto || 0), dash: "4 3", width: 1.5, unit: "mm", noPoints: true },
			],
			1200,
			200,
			{ xLabels: dates, tooltip: true }
		);
		const wet = weather.filter((r) => (r.rainfall_mm || 0) > 0.1).length;
		const wd = this.el.querySelector("#wx-wetdry");
		if (wd) wd.textContent = `${wet} wet · ${weather.length - wet} dry days`;

		const temps = weather.filter((r) => r.minimum_temperature != null && r.maximum_temperature != null);
		charts.mkChart(
			this.el.querySelector("#wx-temp"),
			[
				{ label: "Max", color: T.heat, values: temps.map((r) => r.maximum_temperature), width: 1.6, unit: "°C", noPoints: true },
				{ label: "Mean", color: T.clay, values: temps.map((r) => r.mean_temperature != null ? r.mean_temperature : (r.minimum_temperature + r.maximum_temperature) / 2), width: 2, unit: "°C", noPoints: true },
				{ label: "Min", color: T.cool, values: temps.map((r) => r.minimum_temperature), width: 1.6, unit: "°C", noPoints: true },
			],
			1200,
			200,
			{
				xLabels: temps.map((r) => r.date),
				tooltip: true,
				noFill: true,
				yMin: temps.length ? Math.floor(Math.min(...temps.map((r) => r.minimum_temperature)) - 2) : 0,
				yMax: temps.length ? Math.ceil(Math.max(...temps.map((r) => r.maximum_temperature)) + 2) : 40,
			}
		);

		charts.mkChart(
			this.el.querySelector("#wx-evap"),
			[{ label: "Evaporation", color: T.eto, values: weather.map((r) => r.daily_evaporation || 0), unit: "mm" }],
			560,
			180,
			{ xLabels: dates, tooltip: true }
		);

		const swd = weather.filter((r) => r.swd != null);
		if (swd.length) {
			const vals = swd.map((r) => Number(r.swd));
			charts.mkChart(
				this.el.querySelector("#wx-swd"),
				[{ label: "SWD", color: T.cool, values: vals, unit: "mm", noPoints: swd.length > 60 }],
				560,
				180,
				{
					xLabels: swd.map((r) => r.date),
					tooltip: true,
					noFill: true,
					yMin: Math.floor(Math.min(...vals, 0)),
					yMax: Math.ceil(Math.max(...vals, 0)),
				}
			);
		} else {
			this.el.querySelector("#wx-swd").innerHTML = '<div class="ui-empty small">no SWD data</div>';
		}

		const zs = weather.filter((r) => r.z_value != null);
		const badge = this.el.querySelector("#wx-z-badge");
		if (!zs.length) {
			this.el.querySelector("#wx-z").innerHTML = '<div class="ui-empty small">no z-value data yet</div>';
			if (badge) {
				badge.textContent = "—";
				badge.className = "ui-sev ink";
			}
			return;
		}
		const latest = zs[zs.length - 1];
		const zv = Number(latest.z_value) || 0;
		if (badge) {
			badge.textContent = `${zv.toFixed(1)} · ${latest.z_risk_level || "—"}`;
			badge.className = `ui-sev ${zTone(zv).cls}`;
		}
		const n = zs.length;
		charts.mkChart(
			this.el.querySelector("#wx-z"),
			[
				{ label: "Spore release (5)", color: T.warn, values: Array(n).fill(5), dash: "3 3", width: 1, noPoints: true },
				{ label: "Infection risk (15)", color: T.clay, values: Array(n).fill(15), dash: "3 3", width: 1, noPoints: true },
				{ label: "High risk (20)", color: T.heat, values: Array(n).fill(20), dash: "3 3", width: 1, noPoints: true },
				{ label: "Z value", color: T.violet, values: zs.map((r) => Number(r.z_value) || 0), width: 2 },
			],
			1200,
			200,
			{ xLabels: zs.map((r) => r.date), tooltip: true, noFill: true }
		);
	},

	renderIrrometer(blocks) {
		const { charts } = this.ctx;
		const host = this.el.querySelector("#wx-irro");
		if (!blocks.length) {
			host.innerHTML = '<div class="ui-empty small">No block data.</div>';
			return;
		}
		const grouped = {};
		blocks.forEach((b) => {
			const key = b.parent_section || "Other";
			(grouped[key] = grouped[key] || []).push(b);
		});

		host.innerHTML = Object.keys(grouped)
			.sort()
			.map((section) => {
				const list = grouped[section];
				const hasData = list.some((b) => b.irrometer_1ft != null);
				const cards = list
					.map((b) => {
						const t1 = tension(b.irrometer_1ft);
						const t2 = tension(b.irrometer_2ft);
						return `<div class="ui-irro" style="border-left-color:${t1.color}">
	<div class="ui-irro-name">
		<span>${charts.esc(b.block_name || b.name)}</span>
		<span class="ui-sev ${t1.cls}">${t1.label}</span>
	</div>
	<div class="ui-irro-gauges">
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
				return `<details class="ui-section-group" open>
	<summary>
		<span class="ui-dot" style="background:var(--ui-clay)"></span>
		${charts.esc(section)}
		<span class="meta">${list.length} block(s)</span>
		${hasData ? "" : '<span class="meta" style="margin-left:auto">no readings yet</span>'}
	</summary>
	<div class="ui-irro-grid">${cards}</div>
</details>`;
			})
			.join("");
	},

	renderBlocks(blocks) {
		const { charts } = this.ctx;
		const body = this.el.querySelector("#wx-blocks");
		const count = this.el.querySelector("#wx-blocks-count");
		count.textContent = `${blocks.length} block(s)`;
		if (!blocks.length) {
			body.innerHTML = '<tr><td colspan="6"><div class="ui-empty small">no blocks</div></td></tr>';
			return;
		}
		body.innerHTML = blocks
			.map((b) => {
				const t = tension(b.irrometer_1ft);
				return `<tr>
	<td><b>${charts.esc(b.block_name || b.name)}</b></td>
	<td>${charts.esc(b.parent_section || "—")}</td>
	<td class="num">${b.irrometer_1ft != null ? b.irrometer_1ft : "—"}</td>
	<td class="num">${b.irrometer_2ft != null ? b.irrometer_2ft : "—"}</td>
	<td>${b.irrometer_date ? charts.fmtDate(b.irrometer_date) : "—"}</td>
	<td class="num"><span class="ui-sev ${t.cls}">${t.label}</span></td>
</tr>`;
			})
			.join("");
	},

	unmount() {
		if (this.unsubscribe) this.unsubscribe();
	},
};
