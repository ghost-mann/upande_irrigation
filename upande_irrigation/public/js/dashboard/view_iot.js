/* IoT Sensors — live telemetry from the Sensor Readings doctype.
 *
 * Ported from meniscus's IoT tab. One card per device (deveui) with the latest
 * value, a sparkline, battery, RSSI and SNR. Devices silent longer than
 * STALE_HOURS are badged Stale — the same threshold api/overview.py alerts on.
 */

import { pagehead, kpi, statusStrip, icon } from "./shell.js";

const STALE_HOURS = 6;

function batteryTone(v) {
	if (v == null) return "var(--ui-mute)";
	if (v >= 3.4) return "var(--ui-ok)";
	if (v >= 3.0) return "var(--ui-warn)";
	return "var(--ui-hot)";
}

function rssiTone(v) {
	if (v == null) return { txt: "—", color: "var(--ui-mute)" };
	if (v >= -90) return { txt: "Excellent", color: "var(--ui-ok)" };
	if (v >= -105) return { txt: "Good", color: "var(--ui-ok)" };
	if (v >= -115) return { txt: "Fair", color: "var(--ui-warn)" };
	return { txt: "Weak", color: "var(--ui-hot)" };
}

/* Sensor type drives the accent so a wall of cards stays scannable. */
function palette(type) {
	const t = String(type || "").toLowerCase();
	if (t.includes("moisture")) return "var(--ui-teal)";
	if (t.includes("temp")) return "var(--ui-hot)";
	if (t.includes("reservoir") || t.includes("level")) return "var(--ui-ok)";
	if (t.includes("ec") || t.includes("conduct")) return "var(--ui-warn)";
	return "var(--ui-clay)";
}

function isStale(ts) {
	const t = new Date(String(ts || "").replace(" ", "T"));
	if (isNaN(t)) return true;
	return Date.now() - t.getTime() > STALE_HOURS * 3600 * 1000;
}

export default {
	id: "iot",
	label: "IoT Sensors",
	pollMs: 120000,

	mount(el, ctx) {
		this.ctx = ctx;
		this.el = el;
		el.innerHTML = `
${pagehead(
	"IoT Sensors",
	"Live telemetry",
	'<span id="iot-meta"></span>',
	`<select class="ui-select" id="iot-type"><option value="">All sensor types</option></select>
	 <button class="ui-btn ghost" id="iot-refresh" type="button">${icon("refresh")}Refresh</button>`
)}
<div class="ui-status" id="iot-status"></div>
<div class="ui-kpis ui-stagger" id="iot-kpis"></div>
<div class="ui-sensor-grid ui-stagger" id="iot-cards"></div>
<div class="ui-card" style="margin-top:14px">
	<div class="ui-cardhead"><h3>Recent readings</h3><span class="meta" id="iot-count"></span></div>
	<div class="ui-tablewrap scroll">
		<table class="ui-table">
			<thead><tr><th>Timestamp</th><th>Sensor</th><th>Type</th><th class="num">Value</th><th class="num">Battery</th><th class="num">RSSI</th></tr></thead>
			<tbody id="iot-rows"></tbody>
		</table>
	</div>
</div>`;

		el.querySelector("#iot-refresh").addEventListener("click", () => this.refresh());
		el.querySelector("#iot-type").addEventListener("change", () => this.refresh());
		this.unsubscribe = ctx.onFilterChange(() => {});
	},

	async refresh() {
		const { api, charts, filters, shell } = this.ctx;
		const status = this.el.querySelector("#iot-status");
		statusStrip(status, "");
		const type = this.el.querySelector("#iot-type").value;

		let data;
		try {
			({ data } = await api.get("upande_irrigation.api.sensors.fetch", {
				days: filters.days,
				sensor_type: type,
			}));
		} catch (err) {
			statusStrip(status, `Could not reach the sensor API: ${err.message}`);
			return;
		}
		if (!data) return;

		const sensors = data.sensors || [];
		const kpis = data.kpis || {};

		this.populateTypes(sensors);
		shell.setCount("iot", sensors.length || "");
		const meta = this.el.querySelector("#iot-meta");
		if (meta) {
			meta.textContent = sensors.length
				? `${sensors.length} device(s) reporting · last ${kpis.window_days || filters.days} days`
				: "no devices reporting in this window";
		}

		/* KPIs */
		const kpiHost = this.el.querySelector("#iot-kpis");
		if (!sensors.length) {
			kpiHost.innerHTML = "";
		} else {
			kpiHost.innerHTML =
				kpi("var(--ui-clay)", "Devices", sensors.length, "", "reporting in window") +
				kpi("var(--ui-teal)", "Readings", charts.fmtNum(kpis.reading_count, 0), "", `last ${kpis.window_days || filters.days}d`) +
				kpi(batteryTone(kpis.avg_battery), "Avg battery", (kpis.avg_battery == null ? 0 : kpis.avg_battery).toFixed(2), "V", kpis.avg_battery >= 3.4 ? "healthy" : kpis.avg_battery >= 3.0 ? "monitor" : "low") +
				kpi("var(--ui-violet)", "Avg RSSI", (kpis.avg_rssi == null ? 0 : kpis.avg_rssi).toFixed(0), "dBm", rssiTone(kpis.avg_rssi).txt) +
				kpi("var(--ui-warn)", "Stale", sensors.filter((s) => isStale(((data.latest || {})[s.deveui] || {}).timestamp || s.last_seen)).length, "", `silent > ${STALE_HOURS} h`);
		}

		this.renderCards(data);
		this.renderRows(data.readings || []);
	},

	populateTypes(sensors) {
		const { charts } = this.ctx;
		const sel = this.el.querySelector("#iot-type");
		const types = [...new Set(sensors.map((s) => s.sensor_type).filter(Boolean))].sort();
		const cur = sel.value;
		/* Don't rebuild while the operator has it open, or the choice jumps. */
		if (sel.dataset.built === types.join("|")) return;
		sel.dataset.built = types.join("|");
		sel.innerHTML =
			'<option value="">All sensor types</option>' +
			types.map((t) => `<option value="${charts.esc(t)}">${charts.esc(t)}</option>`).join("");
		if (cur && types.includes(cur)) sel.value = cur;
	},

	renderCards(data) {
		const { charts } = this.ctx;
		const host = this.el.querySelector("#iot-cards");
		const sensors = data.sensors || [];
		if (!sensors.length) {
			host.innerHTML = `<div class="ui-card" style="grid-column:1/-1"><div class="ui-empty">No readings in the selected window. Check that devices are transmitting and that the period covers a sync.</div></div>`;
			return;
		}

		host.innerHTML = sensors
			.map((s, i) => {
				const duid = s.deveui;
				const latest = (data.latest || {})[duid] || {};
				const series = (data.series || {})[duid] || [];
				const values = series.map((p) => p.value).filter((v) => v != null);
				const color = palette(s.sensor_type);
				const rssi = rssiTone(latest.rssi);
				const stale = isStale(latest.timestamp || s.last_seen);
				const mn = values.length ? Math.min(...values).toFixed(1) : "—";
				const mx = values.length ? Math.max(...values).toFixed(1) : "—";

				return `<div class="ui-sensor" style="--kc:${color};--i:${Math.min(i, 12)}">
	<div class="ui-sensor-head">
		<div>
			<div class="t">${charts.esc(s.sensor_name || duid)}</div>
			<div class="id">${charts.esc(s.sensor_type || "sensor")} · ${charts.esc(String(duid).slice(-8))}</div>
		</div>
		<span class="ui-sev ${stale ? "warn" : "ok"}">${stale ? "Stale" : "Live"}</span>
	</div>
	<div class="ui-sensor-value">
		<b>${latest.value != null ? Number(latest.value).toFixed(1) : "—"}</b>
		<span>${charts.esc(s.units || latest.units || "")}</span>
	</div>
	<div class="ui-sensor-meta">${charts.timeAgo(latest.timestamp || s.last_seen)} · range ${mn}–${mx}</div>
	${charts.sparkline(values, color, 34)}
	<div class="ui-sensor-foot">
		<div><small>Battery</small><b style="color:${batteryTone(latest.battery)}">${latest.battery != null ? `${Number(latest.battery).toFixed(2)}V` : "—"}</b></div>
		<div><small>RSSI</small><b style="color:${rssi.color}">${latest.rssi != null ? `${latest.rssi}` : "—"}</b></div>
		<div><small>SNR</small><b>${latest.snr != null ? Number(latest.snr).toFixed(1) : "—"}</b></div>
	</div>
	<div class="ui-sensor-meta" style="margin:10px 0 0;text-align:center">${s.reading_count || 0} readings in window</div>
</div>`;
			})
			.join("");
	},

	renderRows(readings) {
		const { charts } = this.ctx;
		const body = this.el.querySelector("#iot-rows");
		/* Newest first, capped — the full set can be thousands of rows. */
		const list = readings.slice().reverse().slice(0, 200);
		this.el.querySelector("#iot-count").textContent = `${readings.length} total · showing ${list.length}`;
		if (!list.length) {
			body.innerHTML = '<tr><td colspan="6"><div class="ui-empty small">No readings</div></td></tr>';
			return;
		}
		body.innerHTML = list
			.map((r) => {
				const color = palette(r.sensor_type);
				const rssi = rssiTone(r.rssi);
				const ts = r.timestamp ? String(r.timestamp).replace("T", " ").slice(0, 16) : "—";
				return `<tr>
	<td>${charts.esc(ts)}</td>
	<td><span class="ui-dot" style="background:${color}"></span>${charts.esc(r.sensor_name || r.deveui || "—")}</td>
	<td>${charts.esc(r.sensor_type || "—")}</td>
	<td class="num"><b style="color:${color}">${r.value != null ? Number(r.value).toFixed(1) : "—"}</b> ${charts.esc(r.units || "")}</td>
	<td class="num" style="color:${batteryTone(r.battery)}">${r.battery != null ? `${Number(r.battery).toFixed(2)}V` : "—"}</td>
	<td class="num" style="color:${rssi.color}">${r.rssi != null ? `${r.rssi} dBm` : "—"}</td>
</tr>`;
			})
			.join("");
	},

	unmount() {
		if (this.unsubscribe) this.unsubscribe();
	},
};
