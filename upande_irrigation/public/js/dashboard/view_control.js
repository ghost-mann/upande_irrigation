/* Valve Control — real valve state, grouped by tank, with operator override.
 *
 * Ported from the standalone /irrigation-control page. This is the only valve
 * surface now: meniscus's "Irrigation Control" tab was a simulation over
 * hardcoded valve counts with a fake emergency stop, and has been deleted rather
 * than carried across.
 *
 * effective_state comes from api.valves.list_states — manual override wins, else
 * the schedule window from the active planner.
 *
 * Bulk actions (Close all / Open all / Reset all to Auto, and the same per
 * group) call api.valves.set_override_bulk with an explicit valve list, so
 * what gets overridden is exactly what the operator is looking at — the farm
 * filter included. Each asks for confirmation naming the count.
 */

import { pagehead, kpi, statusStrip, icon } from "./shell.js";

const STATES = [
	["Auto", "Auto", "auto"],
	["Forced Open", "On", "on"],
	["Forced Closed", "Off", "off"],
];

export default {
	id: "control",
	label: "Valve Control",
	pollMs: 30000,

	mount(el, ctx) {
		this.ctx = ctx;
		this.el = el;
		el.innerHTML = `
${pagehead(
	"Valve Control",
	"Valve state",
	"Auto-refresh every 30 s · operator override takes precedence over the schedule",
	`<span class="sev ink" id="vc-clock">—</span>
	 <button class="btn ghost" id="vc-refresh" type="button">${icon("refresh")}Refresh</button>`
)}
<div class="status" id="vc-status"></div>
<div class="kpi-grid stagger" id="vc-kpis"></div>
<div class="card bulkbar" id="vc-bulk" hidden>
	<div class="bulkbar__label"><b>All valves in view</b><span id="vc-bulk-meta"></span></div>
	<div class="bulkbar__actions">
		<button class="btn ghost" type="button" data-bulk="Auto">${icon("refresh")}Reset all to Auto</button>
		<button class="btn ghost" type="button" data-bulk="Forced Open">Open all</button>
		<button class="btn danger" type="button" data-bulk="Forced Closed">Close all</button>
	</div>
</div>
<div id="vc-body"><div class="loading">Loading…</div></div>`;

		el.querySelector("#vc-refresh").addEventListener("click", () => this.refresh());
		el.querySelectorAll("#vc-bulk [data-bulk]").forEach((btn) => {
			btn.addEventListener("click", () => {
				const names = (this.valves || []).map((v) => v.name);
				this.bulk(btn.getAttribute("data-bulk"), names, "every valve in this view");
			});
		});
		this.unsubscribe = ctx.onFilterChange(() => {});
	},

	async refresh() {
		const { api, charts, filters, shell } = this.ctx;
		const status = this.el.querySelector("#vc-status");
		statusStrip(status, "");

		let data;
		try {
			({ data } = await api.get("upande_irrigation.api.valves.list_states", {
				farm: filters.farm,
			}));
		} catch (err) {
			statusStrip(status, `Could not load valves: ${err.message}`);
			return;
		}
		if (!data) return;

		const valves = data.valves || [];
		const tanks = data.tanks || [];
		this.valves = valves;
		const bulkbar = this.el.querySelector("#vc-bulk");
		bulkbar.hidden = !valves.length;
		this.el.querySelector("#vc-bulk-meta").textContent = `${valves.length} valve${valves.length === 1 ? "" : "s"}${filters.farm ? ` · ${filters.farm}` : " · all farms"}`;
		shell.setFarms(valves.map((v) => v.farm).filter(Boolean));

		const on = valves.filter((v) => v.effective_state === "ON").length;
		const overrides = valves.filter((v) => v.override_active).length;
		shell.setCount("control", overrides || "");

		const clock = this.el.querySelector("#vc-clock");
		if (clock) clock.textContent = charts.fmtClock(data.now);

		this.el.querySelector("#vc-kpis").innerHTML =
			kpi("var(--ui-ink4)", "Valves", valves.length, "", "in this view") +
			kpi("var(--ui-ok)", "Open now", on, "", "effective state ON") +
			kpi("var(--ui-mute)", "Closed", valves.length - on, "", "effective state OFF") +
			kpi(overrides ? "var(--ui-warn)" : "var(--ui-ok)", "Overrides", overrides, "", overrides ? "not following schedule" : "all on schedule");

		const body = this.el.querySelector("#vc-body");
		if (!valves.length) {
			body.innerHTML = '<div class="card"><div class="empty">No valves match the current filter. Valves are Tank And Valve records with asset_type = Valve.</div></div>';
			return;
		}

		const tankLabel = {};
		tanks.forEach((t) => {
			tankLabel[t.name] = t.asset_label || t.name;
		});

		/* Group by tank where one is assigned. Where none is — which is every
		 * valve on Lokitela today — fall back to the block-name prefix
		 * ("Airstrip 3 · #3,#4" → "Airstrip"). Without that, 58 identical cards
		 * render as one undifferentiated wall. */
		const groups = {};
		const labels = {};
		valves.forEach((v) => {
			let key;
			let label;
			if (v.tank) {
				key = `tank:${v.tank}`;
				label = tankLabel[v.tank] || v.tank;
			} else {
				const prefix = String(v.asset_label || v.name).trim().split(/[\s·]+/)[0] || "Other";
				key = `prefix:${prefix.toLowerCase()}`;
				label = prefix;
			}
			labels[key] = label;
			(groups[key] = groups[key] || []).push(v);
		});

		/* Tanks first, then prefixes, each alphabetically. */
		this.groups = groups;
		const keys = Object.keys(groups).sort((a, b) => {
			const aTank = a.startsWith("tank:");
			const bTank = b.startsWith("tank:");
			if (aTank !== bTank) return aTank ? -1 : 1;
			return labels[a].localeCompare(labels[b]);
		});

		const noTanks = !tanks.length;
		const eyebrow = this.el.querySelector("[data-eyebrow]");
		if (eyebrow) eyebrow.textContent = noTanks ? "Grouped by block" : "Grouped by tank";
		body.innerHTML =
			(noTanks
				? `<div class="alert muted" style="margin-bottom:14px">${icon("drop")}<span>No valve has a tank assigned, so these are grouped by block name. Set the Tank field on Tank And Valve to group by supply instead.</span></div>`
				: "") +
			keys
				.map((k) => {
					const list = groups[k];
					const onCount = list.filter((v) => v.effective_state === "ON").length;
					const overrides = list.filter((v) => v.override_active).length;
					return `<div class="card">
	<div class="card__head">
		<h3>${icon("drop")}${charts.esc(labels[k])}</h3>
		<span class="meta">${list.length} valve${list.length === 1 ? "" : "s"} · ${onCount} open${overrides ? ` · ${overrides} override${overrides === 1 ? "" : "s"}` : ""}</span>
		<div class="valve-actions group-actions" data-group="${charts.esc(k)}" data-label="${charts.esc(labels[k])}">
			${STATES.map(([state, label, kind]) => `<button class="vbtn ${kind}" data-group-state="${state}" type="button" title="${label} — every valve in ${charts.esc(labels[k])}">${label} all</button>`).join("")}
		</div>
	</div>
	<div class="valve-grid stagger">${list.map((v, i) => this.valveCard(v, i)).join("")}</div>
</div>`;
				})
				.join("");

		this.bindOverrides();
	},

	valveCard(v, i = 0) {
		const { charts } = this.ctx;
		const cls = v.override_active ? "forced" : v.effective_state === "ON" ? "on" : "";
		const sev = v.override_active ? "warn" : v.effective_state === "ON" ? "ok" : "ink";
		const pill = v.effective_state + (v.override_active ? " · override" : "");

		let timing = "";
		if (v.effective_state === "ON" && v.schedule_ends_at) {
			timing = `<div class="valve-row"><span>Ends</span><b>${charts.esc(charts.fmtClock(v.schedule_ends_at))}</b></div>`;
		} else if (v.next_scheduled_at) {
			timing = `<div class="valve-row"><span>Next on</span><b>${charts.esc(charts.fmtDayClock(v.next_scheduled_at))}</b></div>`;
		}

		return `<div class="valve ${cls}" data-valve="${charts.esc(v.name)}">
	<div class="valve-head">
		<div class="n">${charts.esc(v.asset_label || v.name)}</div>
		<span class="sev ${sev}">${charts.esc(pill)}</span>
	</div>
	<div class="valve-row"><span>Block</span><b>${charts.esc(v.block || "—")}</b></div>
	<div class="valve-row"><span>Schedule</span><b>${charts.esc(v.schedule_state)}</b></div>
	${timing}
	${v.override_active ? `<div class="valve-row"><span>Override by</span><b>${charts.esc(v.override_set_by || "—")}</b></div>` : ""}
	<div class="valve-actions">
		${STATES.map(([state, label, kind]) => `<button class="vbtn ${kind}${v.manual_state === state ? " active" : ""}" data-state="${state}" type="button">${label}</button>`).join("")}
	</div>
	${v.location_geojson ? `<a class="valve-locate" href="#map?valve=${encodeURIComponent(v.name)}">${icon("pin")}Show on map</a>` : ""}
</div>`;
	},

	/* Override many valves at once, after the operator confirms the count. */
	async bulk(state, names, what) {
		const { api } = this.ctx;
		const status = this.el.querySelector("#vc-status");
		if (!names.length) return;
		const verb = { Auto: "Reset to Auto", "Forced Open": "Force OPEN", "Forced Closed": "Force CLOSED" }[state];
		if (!window.confirm(`${verb} ${names.length} valve${names.length === 1 ? "" : "s"} — ${what}?`)) return;

		const buttons = this.el.querySelectorAll("#vc-bulk button, .group-actions button");
		buttons.forEach((b) => {
			b.disabled = true;
		});
		statusStrip(status, "");
		try {
			const { data } = await api.post("upande_irrigation.api.valves.set_override_bulk", {
				state,
				valves: JSON.stringify(names),
			});
			statusStrip(status, `${verb}: ${data ? data.count : 0} valve${data && data.count === 1 ? "" : "s"} changed.`, "ok");
			await this.refresh();
		} catch (err) {
			statusStrip(status, `Bulk override failed: ${err.message}`);
		} finally {
			buttons.forEach((b) => {
				b.disabled = false;
			});
		}
	},

	bindOverrides() {
		const { api } = this.ctx;
		const status = this.el.querySelector("#vc-status");

		this.el.querySelectorAll(".group-actions").forEach((bar) => {
			const key = bar.getAttribute("data-group");
			const label = bar.getAttribute("data-label");
			bar.querySelectorAll("[data-group-state]").forEach((btn) => {
				btn.addEventListener("click", () => {
					const names = ((this.groups || {})[key] || []).map((v) => v.name);
					this.bulk(btn.getAttribute("data-group-state"), names, label);
				});
			});
		});

		this.el.querySelectorAll(".valve").forEach((card) => {
			const valve = card.getAttribute("data-valve");
			card.querySelectorAll(".vbtn[data-state]").forEach((btn) => {
				btn.addEventListener("click", async () => {
					const state = btn.getAttribute("data-state");
					const buttons = card.querySelectorAll(".vbtn");
					buttons.forEach((b) => {
						b.disabled = true;
					});
					statusStrip(status, "");
					try {
						await api.post("upande_irrigation.api.valves.set_override", { valve, state });
						await this.refresh();
					} catch (err) {
						statusStrip(status, `Override failed: ${err.message}`);
						buttons.forEach((b) => {
							b.disabled = false;
						});
					}
				});
			});
		});
	},

	unmount() {
		if (this.unsubscribe) this.unsubscribe();
	},
};
