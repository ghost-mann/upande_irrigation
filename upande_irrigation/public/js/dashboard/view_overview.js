/* Overview — the landing view.
 *
 * Read-only. Everything comes from one call to api.overview.fetch, which
 * computes tiles, alerts, the week grid and the deficit trend server-side next
 * to the planning engine that produces the underlying numbers.
 *
 * The week grid is the centrepiece: one column per day of the planning week,
 * one row per section, one block per shift labelled with its hours. That is the
 * shape assign_daily_blocks() produces, so the grid *is* the schedule.
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

function shortSection(name) {
	const base = String(name || "")
		.replace(/ - [A-Z]{2}$/, "")
		.replace("_SECTION", "")
		.trim();
	return base.endsWith("HA") ? `${base.slice(0, -2)} Ha` : base || "—";
}

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
	'<span id="ov-week">Loading this week…</span>',
	`<button class="ui-btn ghost" id="ov-refresh" type="button">${icon("refresh")}Refresh</button>`
)}
<div class="ui-status" id="ov-status"></div>
<div class="ui-kpis ui-stagger" id="ov-tiles">
	${'<div class="ui-skel ui-skel-kpi"></div>'.repeat(6)}
</div>

<div class="ui-card" id="ov-week-card">
	<div class="ui-cardhead">
		<h3>${icon("calendar")}This week's schedule</h3>
		<span class="meta">one column per day · each block is a shift, labelled with its hours</span>
	</div>
	<div id="ov-week-grid"><div class="ui-skel ui-skel-card" style="height:170px"></div></div>
</div>

<div class="ui-row2">
	<div class="ui-card">
		<div class="ui-cardhead">
			<h3>${icon("alert")}Needs attention</h3>
			<span class="meta" id="ov-alert-meta"></span>
		</div>
		<div id="ov-alerts"><div class="ui-skel ui-skel-row"></div><div class="ui-skel ui-skel-row"></div><div class="ui-skel ui-skel-row"></div></div>
	</div>
	<div class="ui-card">
		<div class="ui-cardhead"><h3>${icon("drop")}Water delivered</h3><span class="meta">this week</span></div>
		<div id="ov-donut"></div>
	</div>
</div>

<div class="ui-row2eq">
	<div class="ui-card">
		<div class="ui-cardhead"><h3>${icon("target")}Demand met by section</h3><span class="meta">delivered ÷ required</span></div>
		<div id="ov-coverage"></div>
	</div>
	<div class="ui-card">
		<div class="ui-cardhead"><h3>${icon("trend")}Deficit carried</h3><span class="meta">mm unmet, recent weeks</span></div>
		<div id="ov-trend"></div>
	</div>
</div>`;

		el.querySelector("#ov-refresh").addEventListener("click", () => this.refresh());
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
			const span = `Week of ${charts.fmtDate(week.from_date)} – ${charts.fmtDate(week.to_date)}`;
			/* "0 planners across 0 farms · 0 shift hours" is noise; say the one
			 * thing that is true instead. */
			weekEl.textContent = week.planners
				? `${span} · ${week.planners} planner${week.planners === 1 ? "" : "s"} across ` +
					`${week.farms} farm${week.farms === 1 ? "" : "s"} · ${week.shift_hours} shift hours`
				: `${span} · no planners generated yet`;
		}

		this.renderTiles(data.tiles || []);
		this.renderWeek(data.schedule);
		this.renderAlerts(data.alerts || [], shell);
		this.renderDonut(data.tiles || []);
		this.renderCoverage(data.schedule);
		this.renderTrend(data.deficit_trend);
	},

	renderTiles(tiles) {
		const { charts } = this.ctx;
		const host = this.el.querySelector("#ov-tiles");
		if (!tiles.length) {
			host.innerHTML = "";
			return;
		}
		host.innerHTML = tiles
			.map((t, i) => {
				const isInt = typeof t.value === "number" && Number.isInteger(t.value);
				const shown = typeof t.value === "number" ? charts.fmtNum(t.value, isInt ? 0 : 1) : charts.esc(t.value);
				return kpi(TONES[t.tone] || TONES.ink, t.label, shown, t.unit, t.note).replace(
					'<div class="ui-kpi"',
					`<div class="ui-kpi" style="--i:${i}"`
				);
			})
			.join("");

		/* Count the numeric tiles up so the row animates as one gesture. */
		host.querySelectorAll(".ui-kpi").forEach((card, i) => {
			const t = tiles[i];
			if (!t || typeof t.value !== "number") return;
			const vEl = card.querySelector(".v");
			if (!vEl) return;
			const unit = vEl.querySelector(".unit");
			const num = document.createElement("span");
			vEl.insertBefore(num, unit || null);
			[...vEl.childNodes].forEach((nd) => {
				if (nd.nodeType === Node.TEXT_NODE) nd.remove();
			});
			charts.countUp(num, t.value, { dec: Number.isInteger(t.value) ? 0 : 1 });
		});
	},

	renderWeek(schedule) {
		const { charts } = this.ctx;
		const host = this.el.querySelector("#ov-week-grid");
		if (!schedule || !(schedule.sections || []).length) {
			host.innerHTML = `<div class="ui-empty lg">
	<div class="ic">${icon("calendar")}</div>
	<h4>No shifts scheduled this week</h4>
	<p>The scheduler builds the week's planners every Friday at 06:00. Run it now to fill this week.</p>
	<div class="acts">
		<a class="ui-btn" href="/app/irrigation-scheduler">Open scheduler</a>
		<a class="ui-btn ghost" href="/app/irrigation-planner">See all planners</a>
	</div>
</div>`;
			return;
		}

		const days = schedule.days || [];
		let g = `<div class="ui-week-grid"><div></div>${days
			.map((d) => `<div class="ui-week-head${d.today ? " today" : ""}">${charts.esc(d.label)}</div>`)
			.join("")}`;

		schedule.sections.forEach((sec) => {
			g += `<div class="ui-week-row-label" title="${charts.esc(sec.section)}">${charts.esc(shortSection(sec.section))}</div>`;
			(sec.days || []).forEach((shifts, di) => {
				const today = days[di] && days[di].today ? " today" : "";
				const blocks = (shifts || []).length
					? shifts
							.map(
								(s) =>
									`<div class="ui-week-shift ${s.state}" title="${charts.esc(s.shift)} · ${s.hours} hr${s.cycles > 1 ? ` · ${s.cycles} cycles` : ""}">${s.state === "dry" ? "no water" : `${s.hours}h`}</div>`
							)
							.join("")
					: '<div class="ui-week-shift dry">—</div>';
				g += `<div class="ui-week-cell${today}">${blocks}</div>`;
			});
		});
		g += "</div>";

		host.innerHTML = `<div class="ui-week">${g}</div>
<div class="ui-legend row" style="margin-top:14px">
	<span><i style="background:var(--ui-clay)"></i>Scheduled</span>
	<span><i style="background:var(--ui-ok)"></i>Running now</span>
	<span><i style="background:var(--ui-warn)"></i>Pump-capped</span>
	<span><i style="background:rgba(10,10,10,.08)"></i>No irrigation needed</span>
</div>`;
	},

	renderAlerts(alerts, shell) {
		const { charts } = this.ctx;
		shell.setCount("overview", alerts.length || "");
		this.el.querySelector("#ov-alert-meta").textContent = alerts.length
			? `${alerts.length} item${alerts.length === 1 ? "" : "s"}`
			: "";

		const host = this.el.querySelector("#ov-alerts");
		if (!alerts.length) {
			host.innerHTML = `<div class="ui-empty">${icon("check")}Nothing needs attention — deficits are covered, sensors are reporting and the scheduler is healthy.</div>`;
			return;
		}

		host.innerHTML = `<div class="ui-inbox ui-stagger">${alerts
			.map((a, i) => {
				const glyph = SEV_GLYPH[a.severity] || "alert";
				const action = a.route
					? `<a class="ui-btn ghost small" href="${charts.esc(a.route)}" style="text-decoration:none">Open</a>`
					: "";
				return `<div class="ui-inboxrow" style="--i:${i}">
	<div class="ic ${charts.esc(a.severity)}">${icon(glyph)}</div>
	<div>
		<div class="t">${charts.esc(a.title)}</div>
		<div class="m">${charts.esc(a.detail)}</div>
	</div>
	${action}
</div>`;
			})
			.join("")}</div>`;

		host.querySelectorAll('a[href^="/upande-irrigation#"]').forEach((a) => {
			a.addEventListener("click", (e) => {
				e.preventDefault();
				this.ctx.go(a.getAttribute("href").split("#")[1]);
			});
		});
	},

	renderDonut(tiles) {
		const { charts } = this.ctx;
		const host = this.el.querySelector("#ov-donut");
		const get = (key) => {
			const t = tiles.find((x) => x.key === key);
			return t ? Number(t.value) || 0 : 0;
		};
		const delivered = get("delivered");
		const unmet = get("unmet");
		if (delivered <= 0 && unmet <= 0) {
			host.innerHTML = '<div class="ui-empty small">No demand recorded this week, so nothing to split.</div>';
			return;
		}
		const pct = Math.round((100 * delivered) / (delivered + unmet || 1));
		/* Explicit colours: the second slice is unmet demand, which should read
		 * as a deficit rather than as the palette's neutral ink. */
		charts.donut(host, [["Delivered", delivered], ["Still owed", unmet]], {
			total: String(pct),
			unit: "% met",
			colors: ["var(--ui-ok)", "var(--ui-hot)"],
		});
	},

	renderCoverage(schedule) {
		const { charts } = this.ctx;
		const host = this.el.querySelector("#ov-coverage");
		const sections = (schedule && schedule.sections) || [];
		const rows = sections
			.filter((s) => s.coverage_pct != null)
			.map((s) => ({
				label: shortSection(s.section),
				pct: s.coverage_pct,
				value: `${Math.round(s.coverage_pct)}%`,
			}));
		if (!rows.length) {
			host.innerHTML = '<div class="ui-empty small">Coverage appears once this week\'s planners carry a water demand.</div>';
			return;
		}
		charts.healthBars(host, rows);
	},

	renderTrend(trend) {
		const { charts } = this.ctx;
		const host = this.el.querySelector("#ov-trend");
		if (!trend || !(trend.values || []).length) {
			host.innerHTML = '<div class="ui-empty small">Needs a few weeks of planners before a trend means anything.</div>';
			return;
		}
		/* An all-zero series plots as five "0" gridlines and a flat line, which
		 * looks like a broken axis. The fact itself is good news — say it. */
		if (trend.values.every((v) => !v)) {
			host.innerHTML = `<div class="ui-empty">${icon("check")}No deficit carried in the last ${trend.values.length} weeks — every shift met its demand.</div>`;
			return;
		}
		charts.mkChart(
			host,
			[{ label: "Unmet", color: "var(--ui-hot)", values: trend.values, unit: "mm" }],
			560,
			195,
			{ xLabels: trend.labels, tooltip: true }
		);
	},

	unmount() {
		if (this.unsubscribe) this.unsubscribe();
	},
};
