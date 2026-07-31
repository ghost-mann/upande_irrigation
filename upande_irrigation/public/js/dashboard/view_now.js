/* Irrigation Now — which shift is running, per section, with a live countdown.
 *
 * Ported from the standalone /irrigation-now page. Polls live_sections every
 * 30 s (the shell owns that interval) and re-ticks countdowns, progress bars and
 * the cycle chain every second from started_at/ends_at, so the numbers stay
 * honest between polls instead of drifting off a 30-second-old
 * seconds_remaining.
 *
 * Note this view depends on Irrigation Planner.cycles_count / cycle_hours_each,
 * which patches/v1_0/add_planner_cycle_fields.py creates — live_sections raised
 * "Unknown column" before that patch, taking this whole page with it.
 */

import { pagehead, kpi, statusStrip, icon } from "./shell.js";

const TICK_MS = 1000;

/* Recompute from the window rather than trusting the polled remainder. */
function timing(startStr, endStr) {
	const start = new Date(String(startStr || "").replace(" ", "T"));
	const end = new Date(String(endStr || "").replace(" ", "T"));
	if (isNaN(start) || isNaN(end)) return { remaining: 0, pct: 0 };
	const total = Math.max(1, (end - start) / 1000);
	const remaining = Math.max(0, Math.floor((end - new Date()) / 1000));
	return { remaining, pct: Math.min(100, Math.max(0, ((total - remaining) / total) * 100)) };
}

function cycleState(startStr, endStr) {
	const s = new Date(String(startStr || "").replace(" ", "T"));
	const e = new Date(String(endStr || "").replace(" ", "T"));
	const now = new Date();
	if (isNaN(s) || isNaN(e)) return "upcoming";
	if (now >= e) return "done";
	if (now < s) return "upcoming";
	return "running";
}

function currentCycle(startStr, count, each) {
	if (!count || count <= 1 || !each || each <= 0) return count ? 1 : 0;
	const start = new Date(String(startStr || "").replace(" ", "T"));
	if (isNaN(start)) return 1;
	const elapsedH = Math.max(0, (new Date() - start) / 3600000);
	return Math.min(count, Math.floor(elapsedH / each) + 1);
}

export default {
	id: "now",
	label: "Irrigation Now",
	pollMs: 30000,

	mount(el, ctx) {
		this.ctx = ctx;
		this.el = el;
		this.lastFetch = 0;

		el.innerHTML = `
${pagehead(
	"Irrigation Now",
	"Live section view",
	'Auto-refresh every 30 s · countdown ticks each second',
	`<span class="ui-sev ink" id="now-clock">—</span>
	 <button class="ui-btn ghost" id="now-refresh" type="button">${icon("refresh")}Refresh</button>`
)}
<div class="ui-status" id="now-status"></div>
<div class="ui-kpis" id="now-kpis"></div>
<div id="now-body"><div class="ui-loading">Loading…</div></div>`;

		el.querySelector("#now-refresh").addEventListener("click", () => this.refresh());
		this.unsubscribe = ctx.onFilterChange(() => {});
		this.tickTimer = setInterval(() => this.tick(), TICK_MS);
	},

	async refresh() {
		const { api, charts, filters, shell } = this.ctx;
		const status = this.el.querySelector("#now-status");
		statusStrip(status, "");

		let data;
		try {
			({ data } = await api.get("upande_irrigation.api.scheduler.live_sections", {
				farm: filters.farm,
			}));
		} catch (err) {
			statusStrip(status, `Could not load live sections: ${err.message}`);
			return;
		}
		if (!data) return;
		this.lastFetch = Date.now();

		const farms = data.farms || [];
		shell.setFarms(farms.map((f) => f.farm));

		const sections = farms.flatMap((f) => (f.sections || []).map((s) => ({ farm: f.farm, ...s })));
		const active = sections.filter((s) => s.current);
		shell.setCount("now", active.length || "");

		this.el.querySelector("#now-kpis").innerHTML =
			kpi("var(--ui-ink4)", "Sections", sections.length, "", "with planners scheduled") +
			kpi("var(--ui-ok)", "Irrigating now", active.length, "", active.length ? "running" : "none running") +
			kpi("var(--ui-mute)", "Idle", sections.length - active.length, "", "awaiting next shift") +
			kpi("var(--ui-clay)", "Farms", farms.length, "", "in this view");

		const body = this.el.querySelector("#now-body");
		if (!farms.length) {
			body.innerHTML = `<div class="ui-card"><div class="ui-empty">No active or upcoming shifts. Run the scheduler to generate this week's planners.<div style="margin-top:12px"><a class="ui-btn ghost small" href="/app/irrigation-scheduler" style="text-decoration:none">Open scheduler</a></div></div></div>`;
			return;
		}

		body.innerHTML = farms
			.map((f) => {
				const activeCount = (f.sections || []).filter((s) => s.current).length;
				return `<div class="ui-card">
	<div class="ui-cardhead">
		<h3>${icon("drop")}${charts.esc(f.farm)}</h3>
		<span class="meta">${(f.sections || []).length} section(s) · ${activeCount} irrigating</span>
	</div>
	<div class="ui-live-grid">${(f.sections || []).map((s) => this.sectionCard(s)).join("")}</div>
</div>`;
			})
			.join("");

		this.tick();
	},

	sectionCard(s) {
		const { charts } = this.ctx;
		const on = !!s.current;
		let body;

		if (on) {
			const c = s.current;
			const t = timing(c.started_at, c.ends_at);
			const count = c.cycles_count || 0;
			const each = c.cycle_hours_each || 0;

			const cycleLine =
				count >= 1
					? `<div class="cycleline"><b>${count} cycle${count === 1 ? "" : "s"} × ${each.toFixed(2)} hr</b>${count > 1 ? `<span class="ui-sev ok" data-cycle>cycle ${currentCycle(c.started_at, count, each)} of ${count}</span>` : ""}</div>`
					: "";

			const seps =
				count > 1
					? Array.from({ length: count - 1 }, (_, i) => `<div class="sep" style="left:${((100 * (i + 1)) / count).toFixed(2)}%"></div>`).join("")
					: "";

			const steps =
				(c.cycles || []).length > 1
					? `<div class="ui-csteps">${c.cycles
							.map(
								(cy) =>
									`<div class="ui-cstep ${cycleState(cy.starts_at, cy.ends_at)}" data-cstart="${charts.esc(cy.starts_at)}" data-cend="${charts.esc(cy.ends_at)}">
	<span class="cn">cyc ${cy.n}</span>
	<span class="ct">${charts.esc(charts.fmtClock(cy.starts_at))}</span>
</div>`
							)
							.join("")}</div>`
					: "";

			body = `<div class="shift">${charts.esc(c.shift)}</div>
<div class="countdown" data-countdown data-started="${charts.esc(c.started_at)}" data-ends="${charts.esc(c.ends_at)}" data-count="${count}" data-each="${each}">${charts.fmtCountdown(t.remaining)} left</div>
${cycleLine}
<div class="times"><span>Started ${charts.esc(charts.fmtClock(c.started_at))}</span><span>Ends ${charts.esc(charts.fmtClock(c.ends_at))}</span></div>
<div class="ui-progress"><i data-progress style="width:${t.pct.toFixed(1)}%"></i>${seps}</div>
${steps}`;
		} else {
			const next = (s.upcoming || [])[0];
			body = `<div class="ui-empty small" style="text-align:left;padding:8px 0">${next ? `No shift running. Next at ${charts.esc(charts.fmtDayClock(next.starts_at))}.` : "No shift running and none scheduled."}</div>`;
		}

		const upcoming = (s.upcoming || []).length
			? `<div class="ui-upnext">
	<div class="ui-label">Up next</div>
	${s.upcoming
		.map(
			(u) => `<div class="ui-uprow">
	<span class="s">${charts.esc(u.shift)}</span>
	<span class="t">${charts.esc(charts.fmtDayClock(u.starts_at))} · ${(u.shift_hours || 0).toFixed(1)} hr${(u.cycles_count || 0) > 1 ? ` · ${u.cycles_count}×${(u.cycle_hours_each || 0).toFixed(1)}h` : ""}</span>
</div>`
		)
		.join("")}
</div>`
			: "";

		return `<div class="ui-live ${on ? "on" : ""}">
	<div class="ui-live-head">
		<div class="n">${charts.esc(s.section)}</div>
		<span class="ui-sev ${on ? "ok" : "ink"}">${on ? "IRRIGATING" : "IDLE"}</span>
	</div>
	${body}
	${upcoming}
</div>`;
	},

	/* Re-tick countdowns without refetching. */
	tick() {
		const { charts } = this.ctx;
		const clock = this.el.querySelector("#now-clock");
		if (clock) {
			clock.textContent = new Date().toLocaleTimeString([], {
				hour: "2-digit",
				minute: "2-digit",
				second: "2-digit",
			});
		}

		this.el.querySelectorAll(".ui-live.on").forEach((card) => {
			const cd = card.querySelector("[data-countdown]");
			const bar = card.querySelector("[data-progress]");
			if (!cd || !bar) return;

			const t = timing(cd.getAttribute("data-started"), cd.getAttribute("data-ends"));
			cd.textContent = `${charts.fmtCountdown(t.remaining)} left`;
			bar.style.width = `${t.pct.toFixed(1)}%`;

			const badge = card.querySelector("[data-cycle]");
			const count = parseInt(cd.getAttribute("data-count"), 10) || 0;
			const each = parseFloat(cd.getAttribute("data-each")) || 0;
			if (badge && count > 1) {
				badge.textContent = `cycle ${currentCycle(cd.getAttribute("data-started"), count, each)} of ${count}`;
			}

			card.querySelectorAll(".ui-cstep").forEach((step) => {
				const st = cycleState(step.getAttribute("data-cstart"), step.getAttribute("data-cend"));
				step.classList.remove("done", "running", "upcoming");
				step.classList.add(st);
			});

			/* A shift that just ended means the next one has started — pull once,
			 * rate-limited so a row of finished cards can't stampede. */
			if (t.remaining === 0 && Date.now() - this.lastFetch > 5000) {
				this.lastFetch = Date.now();
				this.refresh();
			}
		});
	},

	unmount() {
		if (this.tickTimer) clearInterval(this.tickTimer);
		if (this.unsubscribe) this.unsubscribe();
	},
};
