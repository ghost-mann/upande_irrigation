/* Valve Control — real valve state, grouped by tank, with operator override.
 *
 * Ported from the standalone /irrigation-control page. This is the only valve
 * surface now: meniscus's "Irrigation Control" tab was a simulation over
 * hardcoded valve counts with a fake emergency stop, and has been deleted rather
 * than carried across.
 *
 * effective_state comes from api.valves.list_states — manual override wins, else
 * the schedule window from the active planner.
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
	"Grouped by tank",
	"Auto-refresh every 30 s · operator override takes precedence over the schedule",
	`<span class="ui-sev ink" id="vc-clock">—</span>
	 <button class="ui-btn ghost" id="vc-refresh" type="button">${icon("refresh")}Refresh</button>`
)}
<div class="ui-status" id="vc-status"></div>
<div class="ui-kpis" id="vc-kpis"></div>
<div id="vc-body"><div class="ui-loading">Loading…</div></div>`;

		el.querySelector("#vc-refresh").addEventListener("click", () => this.refresh());
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
			body.innerHTML = '<div class="ui-card"><div class="ui-empty">No valves match the current filter. Valves are Tank And Valve records with asset_type = Valve.</div></div>';
			return;
		}

		const tankLabel = {};
		tanks.forEach((t) => {
			tankLabel[t.name] = t.asset_label || t.name;
		});
		const groups = {};
		valves.forEach((v) => {
			const key = v.tank || "__none__";
			(groups[key] = groups[key] || []).push(v);
		});
		/* Unassigned valves sort last — they're a data-quality tail, not a tank. */
		const keys = Object.keys(groups).sort((a, b) =>
			a === "__none__" ? 1 : b === "__none__" ? -1 : a.localeCompare(b)
		);

		body.innerHTML = keys
			.map((k) => {
				const list = groups[k];
				const label = k === "__none__" ? "No tank assigned" : tankLabel[k] || k;
				const onCount = list.filter((v) => v.effective_state === "ON").length;
				return `<div class="ui-card">
	<div class="ui-cardhead">
		<h3>${icon("drop")}${charts.esc(label)}</h3>
		<span class="meta">${list.length} valve(s) · ${onCount} open</span>
	</div>
	<div class="ui-valve-grid">${list.map((v) => this.valveCard(v)).join("")}</div>
</div>`;
			})
			.join("");

		this.bindOverrides();
	},

	valveCard(v) {
		const { charts } = this.ctx;
		const cls = v.override_active ? "forced" : v.effective_state === "ON" ? "on" : "";
		const sev = v.override_active ? "warn" : v.effective_state === "ON" ? "ok" : "ink";
		const pill = v.effective_state + (v.override_active ? " · override" : "");

		let timing = "";
		if (v.effective_state === "ON" && v.schedule_ends_at) {
			timing = `<div class="ui-valve-row"><span>Ends</span><b>${charts.esc(charts.fmtClock(v.schedule_ends_at))}</b></div>`;
		} else if (v.next_scheduled_at) {
			timing = `<div class="ui-valve-row"><span>Next on</span><b>${charts.esc(charts.fmtDayClock(v.next_scheduled_at))}</b></div>`;
		}

		return `<div class="ui-valve ${cls}" data-valve="${charts.esc(v.name)}">
	<div class="ui-valve-head">
		<div class="n">${charts.esc(v.asset_label || v.name)}</div>
		<span class="ui-sev ${sev}">${charts.esc(pill)}</span>
	</div>
	<div class="ui-valve-row"><span>Block</span><b>${charts.esc(v.block || "—")}</b></div>
	<div class="ui-valve-row"><span>Schedule</span><b>${charts.esc(v.schedule_state)}</b></div>
	${timing}
	${v.override_active ? `<div class="ui-valve-row"><span>Override by</span><b>${charts.esc(v.override_set_by || "—")}</b></div>` : ""}
	<div class="ui-valve-actions">
		${STATES.map(([state, label, kind]) => `<button class="ui-vbtn ${kind}${v.manual_state === state ? " active" : ""}" data-state="${state}" type="button">${label}</button>`).join("")}
	</div>
</div>`;
	},

	bindOverrides() {
		const { api } = this.ctx;
		const status = this.el.querySelector("#vc-status");

		this.el.querySelectorAll(".ui-valve").forEach((card) => {
			const valve = card.getAttribute("data-valve");
			card.querySelectorAll(".ui-vbtn[data-state]").forEach((btn) => {
				btn.addEventListener("click", async () => {
					const state = btn.getAttribute("data-state");
					const buttons = card.querySelectorAll(".ui-vbtn");
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
