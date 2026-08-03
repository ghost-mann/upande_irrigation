/* Planning — how a week's irrigation was decided.
 *
 * Four questions, four visuals, in the order an operator asks them:
 *
 *   1. When does each shift run?              week Gantt, real clock time
 *   2. Is any pump over-committed?            hours per pump per day vs 24 h
 *   3. Are we keeping up over time?           demand / delivered / carried
 *   4. Where did this shift's hours come from? the calculation, step by step
 *
 * The Gantt is HTML rather than SVG: bars need selectable labels, a hover target
 * bigger than a 2px cycle, and to reflow on a narrow screen. Every chart carries
 * a legend and a Table toggle, which is what the gold in the series ramp owes —
 * it sits below 3:1 against the card surface, so colour is never the only channel.
 */

import { pagehead, kpi, statusStrip, icon } from "./shell.js";
import { SERIES, OTHER, BALANCE } from "./palette.js";

const DAY_MS = 86400000;

/* Where "now" sits across the week, as a percentage, or null when the week being
 * viewed is not the current one. */
function nowPct(from, to) {
	const start = new Date(`${from}T00:00:00`).getTime();
	const end = new Date(`${to}T00:00:00`).getTime() + DAY_MS;
	const t = Date.now();
	if (t < start || t > end) return null;
	return (100 * (t - start)) / (end - start);
}

export default {
	id: "planner",
	label: "Planning",
	pollMs: 120000,

	mount(el, ctx) {
		this.ctx = ctx;
		this.el = el;
		this.week = null;
		this.explained = null;
		this.tables = {};

		el.innerHTML = `
${pagehead(
	"Planning & Scheduling",
	"Upande Irrigation",
	'<span id="pl-sub">Loading the week…</span>',
	`<label class="side__field" style="margin:0">
		<select class="select" id="pl-week" aria-label="Planning week"></select>
	</label>
	<button class="btn ghost" id="pl-refresh" type="button">${icon("refresh")}Refresh</button>`
)}
<div class="status" id="pl-status"></div>
<div class="kpi-grid five stagger" id="pl-tiles">${'<div class="skel skel-kpi"></div>'.repeat(5)}</div>

<div class="card">
	<div class="card__head">
		<h3>${icon("calendar")}When each shift runs</h3>
		<span class="meta">real clock time across the week · each bar is one cycle</span>
		${this.toggle("gantt")}
	</div>
	<div id="pl-gantt"><div class="skel skel-card" style="height:220px"></div></div>
</div>

<div class="row-2-eq">
	<div class="card">
		<div class="card__head">
			<h3>${icon("zap")}Pump load by day</h3>
			<span class="meta">hours drawn · dashed line is the 24 h a day offers</span>
			${this.toggle("pump")}
		</div>
		<div id="pl-pump"></div>
	</div>
	<div class="card">
		<div class="card__head">
			<h3>${icon("trend")}Water balance</h3>
			<span class="meta">mean mm per shift, week by week</span>
			${this.toggle("balance")}
		</div>
		<div id="pl-balance"></div>
	</div>
</div>

<div class="card">
	<div class="card__head">
		<h3>${icon("target")}How a shift's hours were decided</h3>
		<span class="meta" id="pl-explain-meta">pick a shift</span>
		<label class="side__field" style="margin:0;min-width:200px">
			<select class="select" id="pl-shift" aria-label="Shift to explain"></select>
		</label>
	</div>
	<div id="pl-explain"><div class="empty small">Pick a shift to trace its calculation.</div></div>
</div>`;

		el.querySelector("#pl-refresh").addEventListener("click", () => this.refresh());
		el.querySelector("#pl-week").addEventListener("change", (e) => {
			this.week = e.target.value || null;
			this.explained = null;
			this.refresh();
		});
		el.querySelector("#pl-shift").addEventListener("change", (e) => {
			this.explained = e.target.value || null;
			this.renderExplain();
		});
		el.querySelectorAll("[data-table]").forEach((btn) => {
			btn.addEventListener("click", () => {
				const key = btn.getAttribute("data-table");
				this.tables[key] = !this.tables[key];
				btn.classList.toggle("on", this.tables[key]);
				btn.textContent = this.tables[key] ? "Chart" : "Table";
				this.redraw(key);
			});
		});

		this.unsubscribe = ctx.onFilterChange(() => {
			/* A farm change invalidates the chosen week and shift. */
			this.week = null;
			this.explained = null;
		});
	},

	toggle(key) {
		return `<button class="btn ghost small" type="button" data-table="${key}" style="margin-left:auto">Table</button>`;
	},

	async refresh() {
		const { api, charts, filters } = this.ctx;
		const status = this.el.querySelector("#pl-status");
		statusStrip(status, "");

		let data;
		try {
			({ data } = await api.get("upande_irrigation.api.planner.fetch", {
				farm: filters.farm,
				week: this.week || "",
			}));
		} catch (err) {
			statusStrip(status, `Could not load the plan: ${err.message}`);
			return;
		}
		if (!data) return;
		this.data = data;

		const w = data.week || {};
		this.week = w.from_date;

		const sel = this.el.querySelector("#pl-week");
		sel.innerHTML = (data.weeks || [])
			.map(
				(x) =>
					`<option value="${charts.esc(x.from_date)}"${x.from_date === w.from_date ? " selected" : ""}>${charts.fmtDate(x.from_date)} – ${charts.fmtDate(x.to_date)} · ${x.planners} shifts</option>`
			)
			.join("") || '<option value="">No weeks planned</option>';

		const sub = this.el.querySelector("#pl-sub");
		if (sub) {
			sub.textContent = w.planners
				? `${charts.fmtDate(w.from_date)} – ${charts.fmtDate(w.to_date)} · ${w.planners} shifts · ` +
					`${charts.fmtNum(w.granted_hours)} of ${charts.fmtNum(w.required_hours)} hours granted` +
					(w.is_current ? " · this week" : "")
				: "No planners for this week — the measured week was too thin to plan from.";
		}

		this.renderTiles(w);
		this.renderShiftPicker();
		this.redraw("gantt");
		this.redraw("pump");
		this.redraw("balance");
		this.renderExplain();
	},

	redraw(key) {
		if (key === "gantt") this.renderGantt();
		else if (key === "pump") this.renderPump();
		else if (key === "balance") this.renderBalance();
	},

	renderTiles(w) {
		const { charts } = this.ctx;
		const host = this.el.querySelector("#pl-tiles");
		if (!w || !w.planners) {
			host.innerHTML = "";
			return;
		}
		const met = w.met_pct;
		const tone = met == null ? "var(--ui-ink4)" : met >= 90 ? "var(--ui-ok)" : met >= 50 ? "var(--ui-warn)" : "var(--ui-hot)";
		host.innerHTML = [
			kpi("var(--ui-clay)", "Hours required", charts.fmtNum(w.required_hours), "hr", "to clear demand and debt"),
			kpi("var(--ui-ok)", "Hours granted", charts.fmtNum(w.granted_hours), "hr", "after pump capacity"),
			kpi(tone, "Demand met", met == null ? "—" : charts.fmtNum(met), "%", "granted ÷ required"),
			kpi(
				"var(--ui-rain)",
				"Delivered per shift",
				charts.fmtNum(w.delivered_mm_per_shift),
				"mm",
				`of ${charts.fmtNum(w.demand_mm_per_shift)} mm each shift needed`
			),
			kpi(
				w.unscheduled ? "var(--ui-hot)" : "var(--ui-ink4)",
				"Unscheduled",
				String(w.unscheduled || 0),
				w.unscheduled === 1 ? "shift" : "shifts",
				w.unscheduled ? "the week filled before their turn" : "every shift got a window"
			),
		].join("");
	},

	/* ── 1. Week Gantt ─────────────────────────────────────────── */
	renderGantt() {
		const { charts } = this.ctx;
		const host = this.el.querySelector("#pl-gantt");
		const data = this.data || {};
		const sections = (data.gantt && data.gantt.sections) || [];
		if (!sections.length) {
			host.innerHTML = `<div class="empty lg">
	<div class="ic">${icon("calendar")}</div>
	<h4>Nothing scheduled for this week</h4>
	<p>Either the week has no planners, or every shift found rainfall enough to skip.</p>
	<div class="acts"><a class="btn" href="/app/irrigation-scheduler">Open scheduler</a></div>
</div>`;
			return;
		}

		if (this.tables.gantt) {
			host.innerHTML = this.table(
				["Section", "Shift", "Required (hr)", "Granted (hr)", "Met", "Cycles", "First window"],
				sections.flatMap((sec) =>
					sec.shifts.map((s) => [
						sec.label,
						`Shift ${s.n}`,
						charts.fmtNum(s.required_hours, 2),
						charts.fmtNum(s.granted_hours, 2),
						s.met_pct == null ? "—" : `${charts.fmtNum(s.met_pct)}%`,
						String(s.bars.length),
						s.bars.length ? charts.fmtDayClock(s.bars[0].start) : "not scheduled",
					])
				)
			);
			return;
		}

		const days = data.days || [];
		const marker = data.week && data.week.is_current ? nowPct(data.week.from_date, data.week.to_date) : null;

		const head = `<div class="gantt__head">
	<div class="gantt__name"></div>
	<div class="gantt__lane">
		${days.map((d) => `<div class="gantt__day${d.today ? " today" : ""}">${charts.esc(d.label)}</div>`).join("")}
	</div>
	<div class="gantt__num">hr</div>
</div>`;

		const rows = sections
			.map((sec) => {
				const shifts = sec.shifts
					.map((s) => {
						const bars = s.bars
							.map(
								(b) =>
									`<div class="gantt__bar ${b.state}" style="left:${b.left_pct}%;width:${Math.max(0.35, b.width_pct)}%" title="${charts.esc(s.shift)} · ${charts.fmtDayClock(b.start)} → ${charts.fmtClock(b.end)} · ${b.hours} hr"></div>`
							)
							.join("");
						const flag = s.unscheduled
							? '<div class="gantt__none">not scheduled — carries forward</div>'
							: "";
						const capped = s.met_pct != null && s.met_pct < 99.5;
						return `<div class="gantt__row">
	<div class="gantt__name" title="${charts.esc(s.shift)}">Shift ${charts.esc(s.n)}${capped ? ' <i class="gantt__cap" title="pump-capped">▲</i>' : ""}</div>
	<div class="gantt__lane">
		${days.map((d) => `<div class="gantt__cell${d.today ? " today" : ""}"></div>`).join("")}
		${bars}${flag}
	</div>
	<div class="gantt__num">${charts.fmtNum(s.granted_hours, 2)}</div>
</div>`;
					})
					.join("");
				return `<div class="gantt__group"><div class="gantt__sec">${charts.esc(sec.label)}</div>${shifts}</div>`;
			})
			.join("");

		/* Only wrap in a scroller when it would actually overflow — a four-shift farm
		 * should not get a scrollbar it never needs. */
		const shiftCount = sections.reduce((n, s) => n + s.shifts.length, 0);
		const scroll = shiftCount > 18;

		host.innerHTML = `<div class="gantt">
	${head}
	<div class="gantt__body${scroll ? " gantt__scroll" : ""}">
		${rows}
		${marker != null ? `<div class="gantt__now" style="left:calc(var(--gantt-name) + (100% - var(--gantt-name) - var(--gantt-num)) * ${marker / 100})"><span>now</span></div>` : ""}
	</div>
</div>
<div class="clegend" style="margin-top:14px">
	<span><i style="background:var(--ui-ok)"></i>Finished</span>
	<span><i style="background:var(--ui-clay)"></i>Running now</span>
	<span><i style="background:#3268c4"></i>Still to run</span>
	<span><i style="background:var(--ui-hot)"></i>Never scheduled</span>
	<span>▲ pump-capped</span>
</div>`;
	},

	/* ── 2. Pump load ──────────────────────────────────────────── */
	renderPump() {
		const { charts } = this.ctx;
		const host = this.el.querySelector("#pl-pump");
		const pumps = (this.data && this.data.pump_load && this.data.pump_load.pumps) || [];
		const days = (this.data && this.data.days) || [];
		if (!pumps.length) {
			host.innerHTML = '<div class="empty small">No pump draw this week.</div>';
			return;
		}

		if (this.tables.pump) {
			host.innerHTML = this.table(
				["Pump", "Shifts", ...days.map((d) => d.label), "Total (hr)"],
				pumps.map((p) => [
					p.pump,
					String(p.shifts),
					...p.days.map((h) => charts.fmtNum(h, 1)),
					charts.fmtNum(p.total, 1),
				])
			);
			return;
		}

		charts.groupedBars(host, {
			labels: days.map((d) => d.label),
			groups: pumps.slice(0, SERIES.length + 1).map((p, i) => ({
				label: p.pump,
				color: i < SERIES.length ? SERIES[i] : OTHER,
				values: p.days,
			})),
			W: 600,
			H: 240,
			unit: " hr",
			dec: 1,
			refLine: { value: 24, label: "24 h — a full day" },
		});

		const peak = pumps[0];
		host.innerHTML += `<div class="clegend" style="margin-top:12px">
	${pumps
		.slice(0, SERIES.length + 1)
		.map(
			(p, i) =>
				`<span><i style="background:${i < SERIES.length ? SERIES[i] : OTHER}"></i>${charts.esc(p.pump)} · ${charts.fmtNum(p.total, 1)} hr</span>`
		)
		.join("")}
</div>
<p class="pagehead__sub" style="margin:10px 0 0">${
			peak.peak_day_pct >= 99
				? `<b>${charts.esc(peak.pump)}</b> runs a full 24 h on its busiest day — there is no headroom left to schedule into.`
				: `Busiest day reaches ${charts.fmtNum(Math.min(100, peak.peak_day_pct))}% of a 24 h day on <b>${charts.esc(peak.pump)}</b>.`
		}</p>`;
	},

	/* ── 3. Water balance ──────────────────────────────────────── */
	renderBalance() {
		const { charts } = this.ctx;
		const host = this.el.querySelector("#pl-balance");
		const b = (this.data && this.data.balance) || {};
		if (!(b.labels || []).length) {
			host.innerHTML = '<div class="empty small">Needs a few weeks of planners before a balance means anything.</div>';
			return;
		}

		if (this.tables.balance) {
			host.innerHTML = this.table(
				["Week of", "Shifts", "Needed (mm)", "Delivered (mm)", "Carried out (mm)"],
				b.labels.map((l, i) => [
					charts.fmtDate(l),
					String((b.shifts || [])[i] ?? "—"),
					charts.fmtNum(b.demand[i]),
					charts.fmtNum(b.delivered[i]),
					charts.fmtNum(b.carried[i]),
				])
			);
			return;
		}

		charts.mkMultiLine(
			host,
			b.labels,
			[
				{ label: "Needed", color: BALANCE.demand, values: b.demand },
				{ label: "Delivered", color: BALANCE.delivered, values: b.delivered },
				{ label: "Carried out", color: BALANCE.carried, values: b.carried },
			],
			600,
			240,
			{ unit: " mm", dec: 0 }
		);

		const last = b.labels.length - 1;
		const gap = b.demand[last] - b.delivered[last];
		host.innerHTML += `<div class="clegend" style="margin-top:12px">
	<span><i style="background:${BALANCE.demand}"></i>Needed</span>
	<span><i style="background:${BALANCE.delivered}"></i>Delivered</span>
	<span><i style="background:${BALANCE.carried}"></i>Carried out</span>
</div>
<p class="pagehead__sub" style="margin:10px 0 0">${
			gap > 0
				? `The average shift ended the most recent week ${charts.fmtNum(gap)} mm short; that becomes its opening debt. Depths are averaged, not summed — millimetres do not add across shifts.`
				: "Every shift met its demand in the most recent week."
		}</p>`;
	},

	/* ── 4. Calculation explainer ──────────────────────────────── */
	renderShiftPicker() {
		const { charts } = this.ctx;
		const sel = this.el.querySelector("#pl-shift");
		const sections = (this.data && this.data.gantt && this.data.gantt.sections) || [];
		const opts = sections.flatMap((sec) =>
			sec.shifts.map((s) => ({ v: s.planner, l: `${sec.label} · Shift ${s.n}` }))
		);
		if (!opts.length) {
			sel.innerHTML = '<option value="">No shifts this week</option>';
			this.explained = null;
			return;
		}
		if (!this.explained || !opts.some((o) => o.v === this.explained)) {
			this.explained = opts[0].v;
		}
		sel.innerHTML = opts
			.map(
				(o) =>
					`<option value="${charts.esc(o.v)}"${o.v === this.explained ? " selected" : ""}>${charts.esc(o.l)}</option>`
			)
			.join("");
	},

	async renderExplain() {
		const { api, charts } = this.ctx;
		const host = this.el.querySelector("#pl-explain");
		const meta = this.el.querySelector("#pl-explain-meta");
		if (!this.explained) {
			host.innerHTML = '<div class="empty small">No shift to trace for this week.</div>';
			meta.textContent = "";
			return;
		}

		let data;
		try {
			({ data } = await api.get("upande_irrigation.api.planner.explain", { planner: this.explained }));
		} catch (err) {
			host.innerHTML = `<div class="alert hot">${icon("alert")}<span>${charts.esc(err.message)}</span></div>`;
			return;
		}
		if (!data) return;

		meta.textContent = `${data.shift} · ${charts.fmtDate(data.week.from_date)} – ${charts.fmtDate(data.week.to_date)}`;

		/* The two steps that turn millimetres into hours, and the one where the pump
		 * intervenes, are where the number is usually questioned — mark them. */
		const PIVOTS = { required: "clay", granted: "cap", unmet: "hot" };

		host.innerHTML = `<ol class="steps">
	${data.steps
		.map((s, i) => {
			const tone = PIVOTS[s.key] || "";
			const value =
				s.value == null
					? ""
					: `<b>${charts.fmtNum(s.value, s.unit === "cycles" ? 0 : 2)}</b>${s.unit ? `<small>${charts.esc(s.unit)}</small>` : ""}`;
			return `<li class="steps__row ${tone}" style="--d:${i * 45}ms">
	<div class="steps__n">${i + 1}</div>
	<div class="steps__body">
		<div class="steps__label">${charts.esc(s.label)}</div>
		<div class="steps__detail">${charts.esc(s.detail || "")}</div>
		${s.note ? `<div class="steps__note">${charts.esc(s.note)}</div>` : ""}
	</div>
	<div class="steps__value">${value}</div>
</li>`;
		})
		.join("")}
</ol>
<div class="clegend" style="margin-top:14px">
	<span><i style="background:var(--ui-clay)"></i>What the week needed</span>
	<span><i style="background:var(--ui-warn)"></i>Where the pump intervened</span>
	<span><i style="background:var(--ui-hot)"></i>What carries forward</span>
	${data.disease.z_value ? `<span>Anthracnose Z ${charts.fmtNum(data.disease.z_value, 2)} · ${charts.esc(data.disease.band || "")}</span>` : ""}
</div>
<p class="pagehead__sub" style="margin:12px 0 0">
	<a class="btn ghost small" href="/app/irrigation-planner/${charts.esc(data.planner)}" style="text-decoration:none">Open ${charts.esc(data.planner)}</a>
</p>`;
	},

	/* A plain table of the same numbers — the relief the gold in the series ramp
	 * owes, and the accessible reading of every chart on the page. */
	table(headers, rows) {
		const { charts } = this.ctx;
		if (!rows.length) return '<div class="empty small">Nothing to tabulate.</div>';
		return `<div class="tbl-wrap"><table class="tbl">
	<thead><tr>${headers.map((h) => `<th>${charts.esc(h)}</th>`).join("")}</tr></thead>
	<tbody>${rows
		.map((r) => `<tr>${r.map((c, i) => `<td${i ? ' class="num"' : ""}>${charts.esc(String(c))}</td>`).join("")}</tr>`)
		.join("")}</tbody>
</table></div>`;
	},

	unmount() {
		if (this.unsubscribe) this.unsubscribe();
	},
};
