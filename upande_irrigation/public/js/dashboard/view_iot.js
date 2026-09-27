/* IoT Sensors — pick a site, a type and a sensor; read its measurements.
 *
 * That is the order an operator asks the question in, so it is the order the
 * controls sit in. Each dropdown narrows the next: choosing a site limits the
 * types available, choosing a type limits the sensors.
 *
 * The chart shows one sensor: its per-bucket average as the line, with that
 * bucket's min–max behind it so a sampled average does not hide how much the
 * reading actually moved. Everything else on the page describes that same
 * sensor, so there is only ever one thing being looked at.
 *
 * Above the drill-down sits the fleet overview Meniscus opened with
 * (api.sensors.fleet): fleet KPIs, one card per device with its latest value,
 * Live/Stale, battery/RSSI/SNR and a sparkline, and — below — the newest raw
 * readings. Clicking a card drills into that sensor.
 */

import { pagehead, kpi, statusStrip, icon } from "./shell.js";

const STALE_HOURS = 6;

function batteryTone(v) {
	if (v == null) return "var(--ink-mute)";
	if (v >= 3.4) return "var(--sev-low)";
	if (v >= 3.0) return "var(--sev-mod)";
	return "var(--sev-high)";
}

function signalTone(v) {
	if (v == null) return { txt: "—", tone: "ink" };
	if (v >= -90) return { txt: "Excellent", tone: "lo" };
	if (v >= -105) return { txt: "Good", tone: "lo" };
	if (v >= -115) return { txt: "Fair", tone: "mo" };
	return { txt: "Weak", tone: "hi" };
}

function typeAccent(type) {
	const t = String(type || "").toLowerCase();
	if (t.includes("moisture")) return "var(--bio)";
	if (t.includes("humid")) return "var(--ui-cool)";
	if (t.includes("temp")) return "var(--sev-high)";
	if (t.includes("ec") || t.includes("conduct")) return "var(--sev-mod)";
	return "var(--trap-700)";
}

export default {
	id: "iot",
	label: "IoT Sensors",
	pollMs: 180000,

	mount(el, ctx) {
		this.ctx = ctx;
		this.el = el;
		this.sel = { site_name: "", sensor_type: "", deveui: "" };

		el.innerHTML = `
${pagehead(
	"IoT Sensors",
	"Live telemetry",
	'<span id="iot-meta">Loading…</span>',
	`<button class="btn ghost" id="iot-refresh" type="button">${icon("refresh")}Refresh</button>`
)}
<div class="status" id="iot-status"></div>

<div class="kpi-grid stagger" id="iot-fleet-kpis">${'<div class="skel skel-kpi"></div>'.repeat(5)}</div>
<div class="card card--padded">
	<div class="card__head">
		<h3>${icon("iot")}All sensors</h3>
		<span class="meta" id="iot-fleet-meta"></span>
	</div>
	<div class="sensor-grid stagger" id="iot-fleet"><div class="skel skel-card" style="height:180px"></div></div>
</div>

<div class="card">
	<div class="entry" style="grid-template-columns:repeat(auto-fit,minmax(190px,1fr))">
		<div>
			<label for="iot-site">Sensor site</label>
			<select class="select" id="iot-site"></select>
		</div>
		<div>
			<label for="iot-type">Measurement</label>
			<select class="select" id="iot-type"></select>
		</div>
		<div style="grid-column:span 2">
			<label for="iot-device">Sensor</label>
			<select class="select" id="iot-device"></select>
		</div>
	</div>
</div>

<div class="kpi-grid stagger" id="iot-kpis">${'<div class="skel skel-kpi"></div>'.repeat(4)}</div>

<div class="card card--padded">
	<div class="card__head">
		<h3>${icon("iot")}<span id="iot-chart-title">Readings</span></h3>
		<span class="meta" id="iot-bucket"></span>
	</div>
	<div id="iot-chart"><div class="skel skel-card" style="height:320px"></div></div>
	<div class="clegend" id="iot-legend"></div>
</div>

<div class="card card--padded">
	<div class="card__head">
		<h3>${icon("target")}Other sensors here</h3>
		<span class="meta" id="iot-roster-meta"></span>
	</div>
	<div class="tablewrap scroll">
		<table class="table" id="iot-roster">
			<thead><tr><th>Sensor</th><th>Site</th><th class="num">Readings</th><th class="num">Last seen</th></tr></thead>
			<tbody></tbody>
		</table>
	</div>
</div>

<div class="card card--padded">
	<div class="card__head">
		<h3>${icon("trend")}Recent readings</h3>
		<span class="meta" id="iot-readings-meta"></span>
	</div>
	<div class="tablewrap scroll" style="max-height:420px">
		<table class="table" id="iot-readings">
			<thead><tr><th>Time</th><th>Sensor</th><th>Type</th><th class="num">Value</th><th class="num">Battery</th><th class="num">RSSI</th><th class="num">SNR</th></tr></thead>
			<tbody></tbody>
		</table>
	</div>
</div>`;

		el.querySelector("#iot-refresh").addEventListener("click", () => this.refresh());

		/* Changing a broader filter clears the narrower ones so the server can
		 * pick a sensible default rather than keeping an impossible pairing. */
		el.querySelector("#iot-site").addEventListener("change", (e) => {
			this.sel = { site_name: e.target.value, sensor_type: "", deveui: "" };
			this.refresh();
		});
		el.querySelector("#iot-type").addEventListener("change", (e) => {
			this.sel = { ...this.sel, sensor_type: e.target.value, deveui: "" };
			this.refresh();
		});
		el.querySelector("#iot-device").addEventListener("change", (e) => {
			this.sel = { ...this.sel, deveui: e.target.value };
			this.refresh();
		});

		this.unsubscribe = ctx.onFilterChange(() => {});
	},

	async refresh() {
		const { api, filters, shell } = this.ctx;
		const status = this.el.querySelector("#iot-status");
		statusStrip(status, "");

		/* The fleet follows the site filter only: it is the "is everything
		 * reporting?" view, so narrowing it to one measurement would hide the
		 * silent sensors it exists to show. */
		const fleetReq = api
			.get("upande_irrigation.api.sensors.fleet", { days: filters.days, site_name: this.sel.site_name })
			.then(({ data: f }) => f)
			.catch((err) => {
				console.warn("[irrigation] sensor fleet failed", err);
				return null;
			});

		let data;
		try {
			({ data } = await api.get("upande_irrigation.api.sensors.fetch", {
				days: filters.days,
				site_name: this.sel.site_name,
				sensor_type: this.sel.sensor_type,
				deveui: this.sel.deveui,
			}));
		} catch (err) {
			statusStrip(status, `Could not reach the sensor API: ${err.message}`);
			return;
		}
		if (!data) return;
		this.fleet = await fleetReq;

		this.data = data;
		this.sel = { ...data.selection };
		shell.setCount("iot", (data.devices || []).length || "");

		this.renderFilters();
		this.renderMeta();
		this.renderStats();
		this.renderChart();
		this.renderRoster();
		this.renderFleet();
		this.renderReadings();
	},

	renderFleet() {
		const { charts } = this.ctx;
		const f = this.fleet;
		const kpis = this.el.querySelector("#iot-fleet-kpis");
		const grid = this.el.querySelector("#iot-fleet");
		const meta = this.el.querySelector("#iot-fleet-meta");
		if (!f || (f.meta && f.meta.unavailable)) {
			kpis.innerHTML = "";
			grid.innerHTML = '<div class="empty">Sensor telemetry is not available on this site (upande_sensors is not installed or has no readings).</div>';
			meta.textContent = "";
			return;
		}
		const k = f.kpis || {};
		const sig = signalTone(k.avg_rssi);
		kpis.innerHTML = [
			kpi("var(--trap-700)", "Sensors", k.sensors, "", "devices in this period"),
			kpi("var(--trap-500)", "Readings", charts.fmtNum(k.readings, 0), "", `last ${f.meta.days} days`),
			kpi(batteryTone(k.avg_battery), "Avg battery", k.avg_battery == null ? null : Number(k.avg_battery).toFixed(2), "V", "latest per device"),
			kpi("var(--ui-cool)", "Avg RSSI", k.avg_rssi, "dBm", sig.txt),
			kpi(k.online === k.sensors ? "var(--sev-low)" : "var(--sev-mod)", "Online", k.online, `/ ${k.sensors}`, `reported in the last ${STALE_HOURS} h`),
		].join("");

		const devices = f.devices || [];
		meta.textContent = devices.length ? `${devices.length} measurement${devices.length === 1 ? "" : "s"} · click one to chart it` : "";
		if (!devices.length) {
			grid.innerHTML = `<div class="empty">No sensor reported in the last ${f.meta.days} days. Widen the period in the sidebar.</div>`;
			return;
		}
		grid.innerHTML = devices
			.map((d) => {
				const accent = typeAccent(d.sensor_type);
				const s = signalTone(d.rssi);
				const on = d.deveui === this.sel.deveui && d.sensor_type === this.sel.sensor_type;
				return `<div class="sensor${on ? " on" : ""}" style="--kc:${accent};cursor:pointer" data-dev="${charts.esc(d.deveui)}" data-type="${charts.esc(d.sensor_type)}" data-site="${charts.esc(d.site_name || "")}">
	<div class="sensor__head">
		<div><div class="t">${charts.esc(d.sensor_name)}</div><div class="id">${charts.esc(d.sensor_type)} · ${charts.esc(String(d.deveui).slice(-8))}</div></div>
		<span class="sev ${d.stale ? "mo" : "lo"}">${d.stale ? "Stale" : "Live"}</span>
	</div>
	<div class="sensor__value"><b>${d.latest_value == null ? "—" : charts.fmtNum(d.latest_value)}</b><span>${charts.esc(d.units || "")}</span></div>
	<div class="sensor__meta">${charts.timeAgo(d.latest_at)} · range ${d.min_value == null ? "—" : charts.fmtNum(d.min_value)}–${d.max_value == null ? "—" : charts.fmtNum(d.max_value)}${d.site_name ? ` · ${charts.esc(d.site_name)}` : ""}</div>
	${charts.sparkline(d.spark, accent, 26)}
	<div class="sensor__foot">
		<div><small>Battery</small><b style="color:${batteryTone(d.battery)}">${d.battery == null ? "—" : `${Number(d.battery).toFixed(2)} V`}</b></div>
		<div><small>RSSI</small><b>${d.rssi == null ? "—" : `${d.rssi}`}</b><small>${s.txt}</small></div>
		<div><small>SNR</small><b>${d.snr == null ? "—" : `${d.snr} dB`}</b></div>
	</div>
</div>`;
			})
			.join("");

		grid.querySelectorAll("[data-dev]").forEach((card) => {
			card.addEventListener("click", () => {
				this.sel = {
					site_name: this.sel.site_name,
					sensor_type: card.getAttribute("data-type"),
					deveui: card.getAttribute("data-dev"),
				};
				this.el.querySelector("#iot-chart").innerHTML = '<div class="skel skel-card" style="height:320px"></div>';
				this.refresh();
				this.el.querySelector("#iot-chart").scrollIntoView({ behavior: "smooth", block: "center" });
			});
		});
	},

	renderReadings() {
		const { charts } = this.ctx;
		const body = this.el.querySelector("#iot-readings tbody");
		const meta = this.el.querySelector("#iot-readings-meta");
		const rows = (this.fleet && this.fleet.readings) || [];
		meta.textContent = rows.length ? `newest ${rows.length}` : "";
		if (!rows.length) {
			body.innerHTML = '<tr><td colspan="7"><div class="empty small">No readings in this period.</div></td></tr>';
			return;
		}
		body.innerHTML = rows
			.map(
				(r) => `<tr>
	<td>${charts.esc(charts.fmtDayClock(r.timestamp))}</td>
	<td><b>${charts.esc(r.sensor_name)}</b></td>
	<td>${charts.esc(r.sensor_type || "—")}</td>
	<td class="num">${r.value == null ? "—" : charts.fmtNum(r.value)}${r.units ? ` <small>${charts.esc(r.units)}</small>` : ""}</td>
	<td class="num">${r.battery == null ? "—" : Number(r.battery).toFixed(2)}</td>
	<td class="num">${r.rssi == null ? "—" : r.rssi}</td>
	<td class="num">${r.snr == null ? "—" : r.snr}</td>
</tr>`
			)
			.join("");
	},

	renderFilters() {
		const { charts } = this.ctx;
		const d = this.data;

		const site = this.el.querySelector("#iot-site");
		site.innerHTML =
			'<option value="">All sensor sites</option>' +
			(d.sites || [])
				.map(
					(s) =>
						`<option value="${charts.esc(s.site_name)}"${s.site_name === this.sel.site_name ? " selected" : ""}>${charts.esc(s.site_name)} · ${s.devices}</option>`
				)
				.join("");

		const type = this.el.querySelector("#iot-type");
		type.innerHTML = (d.types || [])
			.map(
				(t) =>
					`<option value="${charts.esc(t.sensor_type)}"${t.sensor_type === this.sel.sensor_type ? " selected" : ""}>${charts.esc(t.sensor_type)} · ${t.devices}</option>`
			)
			.join("");
		if (!(d.types || []).length) type.innerHTML = '<option value="">No measurements</option>';

		const dev = this.el.querySelector("#iot-device");
		dev.innerHTML = (d.devices || [])
			.map(
				(x) =>
					`<option value="${charts.esc(x.deveui)}"${x.deveui === this.sel.deveui ? " selected" : ""}>${charts.esc(x.sensor_name)}${x.stale ? " · stale" : ""}</option>`
			)
			.join("");
		if (!(d.devices || []).length) dev.innerHTML = '<option value="">No sensors match</option>';
	},

	renderMeta() {
		const { charts } = this.ctx;
		const m = this.data.meta || {};
		const meta = this.el.querySelector("#iot-meta");
		const count = (this.data.devices || []).length;
		if (!count) {
			meta.textContent = m.last_reading_at
				? `Nothing in the last ${m.days} days — newest reading ${charts.timeAgo(m.last_reading_at)}`
				: "No sensor readings on this site yet";
			return;
		}
		meta.textContent = `${count} sensor${count === 1 ? "" : "s"} match · newest reading ${charts.timeAgo(m.last_reading_at)}`;
	},

	renderStats() {
		const { charts } = this.ctx;
		const s = this.data.stats;
		const host = this.el.querySelector("#iot-kpis");
		if (!s) {
			host.innerHTML = "";
			return;
		}
		const u = s.units || "";
		const sig = signalTone(s.rssi);
		host.innerHTML = [
			kpi(
				typeAccent(s.sensor_type),
				"Latest reading",
				s.latest_value == null ? null : charts.fmtNum(s.latest_value),
				u,
				charts.timeAgo(s.latest_at),
				"",
				s.stale
					? { text: `Silent ${s.hours_silent} h`, tone: "warn" }
					: { text: "Reporting", tone: "up" }
			),
			kpi(
				"var(--trap-500)",
				"Average",
				s.avg_value == null ? null : charts.fmtNum(s.avg_value),
				u,
				`over ${charts.fmtNum(s.readings, 0)} readings`
			),
			kpi(
				"var(--trap-700)",
				"Range",
				s.min_value == null || s.max_value == null
					? null
					: `${charts.fmtNum(s.min_value)}<span style="color:var(--ink-mute)"> – </span>${charts.fmtNum(s.max_value)}`,
				u,
				`low to high over ${this.data.meta.days} days`
			),
			kpi(
				batteryTone(s.battery),
				"Battery",
				s.battery == null ? null : Number(s.battery).toFixed(2),
				"V",
				`signal ${s.rssi == null ? "—" : `${s.rssi} dBm`} · ${sig.txt}${s.snr == null ? "" : ` · SNR ${s.snr} dB`}`
			),
		].join("");
	},

	renderChart() {
		const { charts } = this.ctx;
		const host = this.el.querySelector("#iot-chart");
		const legend = this.el.querySelector("#iot-legend");
		const s = this.data.stats;
		const series = this.data.series || {};
		const title = this.el.querySelector("#iot-chart-title");
		const bucket = this.el.querySelector("#iot-bucket");

		if (!s || !(series.labels || []).length) {
			const last = (this.data.meta || {}).last_reading_at;
			host.innerHTML = `<div class="empty lg">
	<div class="ic">${icon("iot")}</div>
	<h4>No readings for this sensor</h4>
	<p>${last ? `The newest reading on this site is ${charts.timeAgo(last)}. Widen the period in the sidebar to bring it into view.` : "No sensor has reported yet. Check that the gateway is forwarding into Sensor Readings."}</p>
</div>`;
			legend.innerHTML = "";
			if (title) title.textContent = "Readings";
			if (bucket) bucket.textContent = "";
			return;
		}

		const accent = typeAccent(s.sensor_type);
		if (title) {
			title.textContent = `${s.sensor_name} · ${s.sensor_type}${s.units ? ` (${s.units})` : ""}`;
		}
		if (bucket) {
			const mins = Math.round((s.bucket_seconds || 0) / 60);
			const label = mins >= 1440 ? `${Math.round(mins / 1440)} d` : mins >= 60 ? `${Math.round(mins / 60)} h` : `${mins} min`;
			bucket.textContent = `${charts.fmtNum(s.readings, 0)} readings · ${s.points} points · ${label} average`;
		}

		charts.mkRangeChart(host, {
			labels: series.labels,
			stats: { lo: series.min || [], mid: series.avg || [], hi: series.max || [] },
			picks: [],
			unit: s.units,
			seriesLabel: "Average",
			bandColor: accent,
			medianColor: accent,
			H: 360,
		});

		legend.innerHTML =
			`<span><i class="ln" style="background:${accent}"></i>Average per interval</span>` +
			`<span><i style="background:${accent};opacity:.4"></i>Min–max within each interval</span>` +
			(s.gateway ? `<span style="color:var(--ink-mute)">Gateway ${charts.esc(s.gateway)}</span>` : "");
	},

	renderRoster() {
		const { charts } = this.ctx;
		const body = this.el.querySelector("#iot-roster tbody");
		const meta = this.el.querySelector("#iot-roster-meta");
		const rows = (this.data.devices || []).filter((d) => d.deveui !== this.sel.deveui);

		if (meta) {
			meta.textContent = this.sel.sensor_type
				? `${this.sel.sensor_type}${this.sel.site_name ? ` · ${this.sel.site_name}` : ""}`
				: "";
		}
		if (!rows.length) {
			body.innerHTML =
				'<tr><td colspan="4"><div class="empty small">No other sensors match these filters.</div></td></tr>';
			return;
		}

		body.innerHTML = rows
			.map(
				(d) => `<tr data-dev="${charts.esc(d.deveui)}" style="cursor:pointer">
	<td><b>${charts.esc(d.sensor_name)}</b><div class="list__meta">${charts.esc(String(d.deveui).slice(-8))}</div></td>
	<td>${charts.esc(d.site_name || "—")}</td>
	<td class="num">${charts.fmtNum(d.reading_count, 0)}</td>
	<td class="num">${d.stale ? `<span class="sev mo">${charts.timeAgo(d.last_seen)}</span>` : charts.timeAgo(d.last_seen)}</td>
</tr>`
			)
			.join("");

		body.querySelectorAll("[data-dev]").forEach((tr) => {
			tr.addEventListener("click", () => {
				this.sel = { ...this.sel, deveui: tr.getAttribute("data-dev") };
				this.el.querySelector("#iot-chart").innerHTML =
					'<div class="skel skel-card" style="height:320px"></div>';
				this.refresh();
			});
		});
	},

	unmount() {
		if (this.unsubscribe) this.unsubscribe();
	},
};
