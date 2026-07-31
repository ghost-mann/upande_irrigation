/* IoT Sensors — one tab per sensor type, a fleet chart, a compact device table.
 *
 * The previous layout was a wall of one large card per device — 64 cards on
 * Lokitela, no way to compare them, and a 200-row dump of raw readings beneath.
 * This shows a tab per sensor type (Temperature, Humidity, Soil Moisture), the
 * fleet's p10–p90 envelope with its median as the hero chart, and one compact
 * row per device. Clicking a row overlays that device on the chart, which is how
 * you answer the question the view exists for: is this sensor drifting from the
 * others?
 *
 * The API buckets each type's series server-side, so switching tabs is one
 * scoped query rather than half a million rows in the browser.
 */

import { pagehead, kpi, statusStrip, icon } from "./shell.js";

const STALE_HOURS = 6;

/* Accents for picked devices, chosen to stay legible over the gold envelope. */
const PICK_COLORS = ["#c4302b", "#228883", "#7d4a72", "#4a70a8"];
const MAX_PICKS = 4;

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

/* The band is always gold; each type keeps its own median hue so the tabs are
 * distinguishable at a glance. */
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
		this.picks = new Set();
		this.sort = "name";
		this.mode = "fleet";

		el.innerHTML = `
${pagehead(
	"IoT Sensors",
	"Live telemetry",
	'<span id="iot-meta">Loading…</span>',
	`<button class="btn ghost" id="iot-refresh" type="button">${icon("refresh")}Refresh</button>`
)}
<div class="status" id="iot-status"></div>
<div class="kpi-grid stagger" id="iot-kpis">${'<div class="skel skel-kpi"></div>'.repeat(4)}</div>

<div class="card card--padded">
	<div class="card__head">
		<h3>${icon("iot")}<span id="iot-chart-title">Fleet</span></h3>
		<div class="card__tools">
			<span class="meta" id="iot-bucket"></span>
			<div class="pillgroup" id="iot-mode">
				<button class="on" data-mode="fleet" type="button">Fleet</button>
				<button data-mode="picked" type="button">Selected only</button>
			</div>
		</div>
	</div>
	<div class="stabs" id="iot-tabs"></div>
	<div id="iot-chart"><div class="skel skel-card" style="height:320px"></div></div>
	<div class="clegend" id="iot-chart-legend"></div>
</div>

<div class="card card--padded">
	<div class="card__head">
		<h3>${icon("target")}Devices</h3>
		<div class="card__tools">
			<span class="meta" id="iot-dev-meta"></span>
			<div class="pillgroup" id="iot-sort">
				<button class="on" data-sort="name" type="button">Name</button>
				<button data-sort="value" type="button">Value</button>
				<button data-sort="stale" type="button">Last seen</button>
				<button data-sort="battery" type="button">Battery</button>
			</div>
		</div>
	</div>
	<div class="tablewrap">
		<table class="table" id="iot-devices">
			<thead><tr>
				<th style="width:30px"></th><th>Device</th><th style="width:140px">Trend</th>
				<th class="num">Latest</th><th class="num">Range</th>
				<th class="num">Battery</th><th class="num">Signal</th><th class="num">Last seen</th>
			</tr></thead>
			<tbody></tbody>
		</table>
	</div>
	<div class="clegend">
		<span><i style="background:var(--trap-500)"></i>Click a row to overlay that device on the chart · up to ${MAX_PICKS}</span>
	</div>
</div>`;

		el.querySelector("#iot-refresh").addEventListener("click", () => this.refresh());
		el.querySelectorAll("#iot-sort button").forEach((b) => {
			b.addEventListener("click", () => {
				this.sort = b.getAttribute("data-sort");
				el.querySelectorAll("#iot-sort button").forEach((x) => x.classList.remove("on"));
				b.classList.add("on");
				this.renderDevices();
			});
		});
		el.querySelectorAll("#iot-mode button").forEach((b) => {
			b.addEventListener("click", () => {
				this.mode = b.getAttribute("data-mode");
				el.querySelectorAll("#iot-mode button").forEach((x) => x.classList.remove("on"));
				b.classList.add("on");
				this.renderChart();
			});
		});
		this.unsubscribe = ctx.onFilterChange(() => {});
	},

	async refresh() {
		const { api, filters, shell } = this.ctx;
		const status = this.el.querySelector("#iot-status");
		statusStrip(status, "");

		let data;
		try {
			({ data } = await api.get("upande_irrigation.api.sensors.fetch", {
				days: filters.days,
				sensor_type: this.activeType || "",
			}));
		} catch (err) {
			statusStrip(status, `Could not reach the sensor API: ${err.message}`);
			return;
		}
		if (!data) return;

		this.data = data;
		this.activeType = (data.meta && data.meta.sensor_type) || this.activeType || "";
		shell.setCount("iot", (data.kpis && data.kpis.device_count) || "");

		this.renderMeta();
		this.renderKpis();
		this.renderTabs();
		this.renderChart();
		this.renderDevices();
		this.renderSidebarStats();
	},

	renderMeta() {
		const { charts } = this.ctx;
		const d = this.data;
		const m = d.meta || {};
		const k = d.kpis || {};
		const meta = this.el.querySelector("#iot-meta");
		const total = (d.types || []).reduce((s, t) => s + t.devices, 0);

		if (!k.device_count) {
			/* Explain an empty window rather than showing a blank screen: the
			 * newest reading is usually just older than the selected period. */
			meta.textContent = m.last_reading_at
				? `Nothing in the last ${m.days} days — newest reading ${charts.timeAgo(m.last_reading_at)}`
				: "No sensor readings on this site yet";
			return;
		}
		const types = (d.types || []).length;
		meta.textContent =
			`${total} device${total === 1 ? "" : "s"} across ${types} type${types === 1 ? "" : "s"} · ` +
			`newest reading ${charts.timeAgo(m.last_reading_at)}`;

		const bucket = this.el.querySelector("#iot-bucket");
		if (bucket) {
			const mins = Math.round((m.bucket_seconds || 0) / 60);
			const label =
				mins >= 1440 ? `${Math.round(mins / 1440)} d` : mins >= 60 ? `${Math.round(mins / 60)} h` : `${mins} min`;
			bucket.textContent = `${m.points} points · ${label} average`;
		}
	},

	renderKpis() {
		const { charts } = this.ctx;
		const k = this.data.kpis || {};
		const host = this.el.querySelector("#iot-kpis");
		if (!k.device_count) {
			host.innerHTML = "";
			return;
		}
		const sig = signalTone(k.avg_rssi);
		host.innerHTML = [
			kpi("var(--trap-500)", "Devices", k.device_count, "", `reporting ${this.activeType || ""}`.trim()),
			kpi("var(--bio)", "Readings", charts.fmtNum(k.reading_count, 0), "", `last ${k.window_days} days`),
			kpi(
				k.stale_count ? "var(--sev-mod)" : "var(--sev-low)",
				"Current",
				k.device_count - k.stale_count,
				`of ${k.device_count}`,
				`silent over ${STALE_HOURS} h counts as stale`,
				"",
				k.stale_count
					? { text: `${k.stale_count} stale`, tone: "warn" }
					: { text: "all current", tone: "up" }
			),
			kpi(
				batteryTone(k.avg_battery),
				"Avg battery",
				k.avg_battery == null ? null : k.avg_battery.toFixed(2),
				"V",
				`signal ${k.avg_rssi == null ? "—" : `${k.avg_rssi} dBm`} · ${sig.txt}`
			),
		].join("");
	},

	renderTabs() {
		const { charts } = this.ctx;
		const host = this.el.querySelector("#iot-tabs");
		const types = this.data.types || [];
		if (!types.length) {
			host.innerHTML = "";
			return;
		}
		host.innerHTML = types
			.map(
				(t) =>
					`<button class="stab${t.sensor_type === this.activeType ? " on" : ""}" type="button" data-type="${charts.esc(t.sensor_type)}" title="${t.readings} readings">
	${charts.esc(t.sensor_type)}<span class="n">${t.devices}</span>
</button>`
			)
			.join("");
		host.querySelectorAll("[data-type]").forEach((btn) => {
			btn.addEventListener("click", () => {
				const next = btn.getAttribute("data-type");
				if (next === this.activeType) return;
				this.activeType = next;
				/* Picks are per type — a Temperature device means nothing on a
				 * Humidity axis. */
				this.picks.clear();
				this.el.querySelector("#iot-chart").innerHTML =
					'<div class="skel skel-card" style="height:320px"></div>';
				this.refresh();
			});
		});
	},

	pickedDevices() {
		const order = [...this.picks];
		return (this.data.devices || [])
			.filter((d) => this.picks.has(d.deveui))
			.sort((a, b) => order.indexOf(a.deveui) - order.indexOf(b.deveui));
	},

	renderChart() {
		const { charts } = this.ctx;
		const host = this.el.querySelector("#iot-chart");
		const legend = this.el.querySelector("#iot-chart-legend");
		const d = this.data;
		const devices = d.devices || [];
		const title = this.el.querySelector("#iot-chart-title");
		const unit = (devices.find((x) => x.units) || {}).units || "";

		if (title) title.textContent = `${this.activeType || "Fleet"}${unit ? ` · ${unit}` : ""}`;

		if (!devices.length) {
			const last = (d.meta || {}).last_reading_at;
			host.innerHTML = `<div class="empty lg">
	<div class="ic">${icon("iot")}</div>
	<h4>Nothing reported in this window</h4>
	<p>${
		last
			? `The newest reading on this site is ${charts.timeAgo(last)}. Widen the period in the sidebar to bring it into view.`
			: "No sensor has reported yet. Check that the gateway is forwarding into Sensor Readings."
	}</p>
</div>`;
			legend.innerHTML = "";
			return;
		}

		const picks = this.pickedDevices().map((dev, i) => ({
			label: dev.sensor_name,
			color: PICK_COLORS[i % PICK_COLORS.length],
			values: dev.series || [],
		}));

		/* "Selected only" drops the envelope so two devices can be read closely
		 * against each other. */
		const showFleet = !(this.mode === "picked" && picks.length);
		const stats = showFleet ? charts.fleetStats(devices) : { lo: [], mid: [], hi: [], count: [] };
		const accent = typeAccent(this.activeType);

		charts.mkFleetChart(host, {
			labels: d.labels || [],
			stats,
			picks,
			unit,
			bandColor: "var(--trap-500)",
			medianColor: accent,
			H: 340,
		});

		const fleetLegend = showFleet
			? `<span><i class="ln" style="background:${accent}"></i>Fleet median · ${devices.length} device${devices.length === 1 ? "" : "s"}</span>` +
				`<span><i style="background:var(--trap-500);opacity:.45"></i>Middle 80% · p10–p90</span>`
			: "";
		const pickLegend = picks
			.map((p) => `<span><i class="ln" style="background:${p.color}"></i>${charts.esc(p.label)}</span>`)
			.join("");
		legend.innerHTML =
			fleetLegend +
			pickLegend +
			(picks.length ? "" : '<span style="color:var(--ink-mute)">Pick a device below to overlay it</span>');
	},

	renderDevices() {
		const { charts } = this.ctx;
		const body = this.el.querySelector("#iot-devices tbody");
		const devices = (this.data.devices || []).slice();
		const meta = this.el.querySelector("#iot-dev-meta");

		if (!devices.length) {
			body.innerHTML =
				'<tr><td colspan="8"><div class="empty small">No devices reported in this window.</div></td></tr>';
			if (meta) meta.textContent = "";
			return;
		}

		const cmp = {
			name: (a, b) => String(a.sensor_name).localeCompare(String(b.sensor_name)),
			value: (a, b) => (b.latest_value == null ? -Infinity : b.latest_value) - (a.latest_value == null ? -Infinity : a.latest_value),
			stale: (a, b) => (a.hours_silent || 0) - (b.hours_silent || 0),
			battery: (a, b) => (a.battery == null ? Infinity : a.battery) - (b.battery == null ? Infinity : b.battery),
		};
		devices.sort(cmp[this.sort] || cmp.name);

		if (meta) {
			const stale = devices.filter((x) => x.stale).length;
			meta.textContent = `${devices.length} in ${this.activeType}${stale ? ` · ${stale} stale` : ""}`;
		}

		const accent = typeAccent(this.activeType);
		const order = [...this.picks];

		body.innerHTML = devices
			.map((dev) => {
				const on = this.picks.has(dev.deveui);
				const color = on ? PICK_COLORS[order.indexOf(dev.deveui) % PICK_COLORS.length] : accent;
				const sig = signalTone(dev.rssi);
				const range =
					dev.min_value != null && dev.max_value != null
						? `${charts.fmtNum(dev.min_value)} – ${charts.fmtNum(dev.max_value)}`
						: "—";
				const spark = charts.sparkline(dev.series || [], color, 26);
				return `<tr data-dev="${charts.esc(dev.deveui)}" style="cursor:pointer${on ? ";background:rgba(217,165,20,0.08)" : ""}">
	<td><span class="dot" style="margin:0;background:${on ? color : "rgba(10,10,10,0.14)"}"></span></td>
	<td>
		<b>${charts.esc(dev.sensor_name)}</b>
		<div class="list__meta">${charts.esc(String(dev.deveui).slice(-8))} · ${charts.fmtNum(dev.reading_count, 0)} readings</div>
	</td>
	<td>${spark || '<span style="color:var(--ink-mute)">—</span>'}</td>
	<td class="num"><b>${dev.latest_value != null ? charts.fmtNum(dev.latest_value) : "—"}</b> ${charts.esc(dev.units || "")}</td>
	<td class="num">${range}</td>
	<td class="num" style="color:${batteryTone(dev.battery)}">${dev.battery != null ? `${Number(dev.battery).toFixed(2)}V` : "—"}</td>
	<td class="num"><span class="sev ${sig.tone}">${sig.txt}</span></td>
	<td class="num">${dev.stale ? `<span class="sev mo">${charts.timeAgo(dev.last_seen)}</span>` : charts.timeAgo(dev.last_seen)}</td>
</tr>`;
			})
			.join("");

		body.querySelectorAll("[data-dev]").forEach((tr) => {
			tr.addEventListener("click", () => {
				const id = tr.getAttribute("data-dev");
				if (this.picks.has(id)) this.picks.delete(id);
				else {
					/* At the cap the oldest pick drops out, so a click always
					 * does something visible. */
					if (this.picks.size >= MAX_PICKS) this.picks.delete([...this.picks][0]);
					this.picks.add(id);
				}
				this.renderChart();
				this.renderDevices();
			});
		});
	},

	/* Feed the sidebar's numbers block, as the reference page does. */
	renderSidebarStats() {
		const { charts, shell } = this.ctx;
		const k = this.data.kpis || {};
		if (!k.device_count) {
			shell.setStats(null);
			return;
		}
		shell.setStats(
			[
				{ label: "Devices", value: k.device_count },
				{ label: "Current", value: k.device_count - k.stale_count },
				{ label: "Readings", value: charts.fmtNum(k.reading_count, 0) },
				{ label: "Types", value: (this.data.types || []).length },
			],
			this.activeType || "Sensors"
		);
	},

	unmount() {
		if (this.unsubscribe) this.unsubscribe();
	},
};
