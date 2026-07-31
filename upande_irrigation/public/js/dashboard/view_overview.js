/* Overview — the landing view.
 *
 * Read-only. Tiles and the "needs attention" list both come from one call to
 * api.overview.fetch, which computes them server-side next to the planning
 * engine that produces the underlying numbers.
 */

import { pagehead, kpi, statusStrip, icon } from "./shell.js";

const TONES = {
	ok: "var(--ui-ok)",
	warn: "var(--ui-warn)",
	hot: "var(--ui-hot)",
	clay: "var(--ui-clay)",
	rain: "var(--ui-rain)",
	ink: "var(--ui-ink4)",
};

const SEV_GLYPH = { hot: "alert", warn: "alert", clay: "drop", ok: "check" };

export default {
	id: "overview",
	label: "Overview",
	pollMs: 60000,

	mount(el, ctx) {
		this.ctx = ctx;
		this.el = el;
		el.innerHTML = `
${pagehead(
	"Overview",
	"Upande Irrigation",
	'<span id="ov-week">this week</span>',
	`<button class="ui-btn ghost" id="ov-refresh" type="button">${icon("refresh")}Refresh</button>`
)}
<div class="ui-status" id="ov-status"></div>
<div class="ui-kpis" id="ov-tiles"></div>
<div class="ui-card">
	<div class="ui-cardhead">
		<h3>${icon("alert")}Needs attention</h3>
		<span class="meta" id="ov-alert-meta"></span>
	</div>
	<div id="ov-alerts"><div class="ui-empty">Checking…</div></div>
</div>
<div class="ui-card tight">
	<div class="ui-cardhead">
		<h3>${icon("now")}Jump to</h3>
	</div>
	<div class="ui-toolbar">
		<button class="ui-chip" data-go="now">Live shifts</button>
		<button class="ui-chip" data-go="weather">Log today's weather</button>
		<button class="ui-chip" data-go="control">Valve control</button>
		<button class="ui-chip" data-go="resources">Water &amp; energy</button>
		<a class="ui-chip" href="/app/irrigation-scheduler" style="text-decoration:none">Scheduler settings</a>
	</div>
</div>`;

		el.querySelector("#ov-refresh").addEventListener("click", () => this.refresh());
		el.querySelectorAll("[data-go]").forEach((b) => {
			b.addEventListener("click", () => ctx.go(b.getAttribute("data-go")));
		});
		this.unsubscribe = ctx.onFilterChange(() => {});
	},

	async refresh() {
		const { api, charts, filters, shell } = this.ctx;
		const status = this.el.querySelector("#ov-status");
		statusStrip(status, "");

		let data;
		try {
			({ data } = await api.get("upande_irrigation.api.overview.fetch", {
				farm: filters.farm,
			}));
		} catch (err) {
			statusStrip(status, `Could not load the overview: ${err.message}`);
			return;
		}
		if (!data) return;

		const week = data.week || {};
		const weekEl = this.el.querySelector("#ov-week");
		if (weekEl) {
			weekEl.textContent =
				`Week of ${charts.fmtDate(week.from_date)} – ${charts.fmtDate(week.to_date)} · ` +
				`${week.planners} planner(s) across ${week.farms} farm(s) · ` +
				`${week.shift_hours} shift hours`;
		}

		this.el.querySelector("#ov-tiles").innerHTML = (data.tiles || [])
			.map((t) =>
				kpi(
					TONES[t.tone] || TONES.ink,
					t.label,
					typeof t.value === "number" ? charts.fmtNum(t.value, Number.isInteger(t.value) ? 0 : 1) : charts.esc(t.value),
					t.unit,
					t.note
				)
			)
			.join("");

		const alerts = data.alerts || [];
		shell.setCount("overview", alerts.length || "");
		this.el.querySelector("#ov-alert-meta").textContent = alerts.length
			? `${alerts.length} item(s)`
			: "";

		const host = this.el.querySelector("#ov-alerts");
		if (!alerts.length) {
			host.innerHTML = `<div class="ui-empty">${icon("check")}Nothing needs attention — deficits are covered, sensors are reporting and the scheduler is healthy.</div>`;
			return;
		}

		host.innerHTML = `<div class="ui-inbox">${alerts
			.map((a) => {
				const glyph = SEV_GLYPH[a.severity] || "alert";
				const action = a.route
					? `<a class="ui-btn ghost small" href="${charts.esc(a.route)}" style="text-decoration:none">Open</a>`
					: "";
				return `<div class="ui-inboxrow">
	<div class="ic ${charts.esc(a.severity)}">${icon(glyph)}</div>
	<div>
		<div class="t">${charts.esc(a.title)}</div>
		<div class="m">${charts.esc(a.detail)}</div>
	</div>
	${action}
</div>`;
			})
			.join("")}</div>`;

		/* Internal hash routes should switch views, not reload the page. */
		host.querySelectorAll('a[href^="/upande-irrigation#"]').forEach((a) => {
			a.addEventListener("click", (e) => {
				e.preventDefault();
				this.ctx.go(a.getAttribute("href").split("#")[1]);
			});
		});
	},

	unmount() {
		if (this.unsubscribe) this.unsubscribe();
	},
};
