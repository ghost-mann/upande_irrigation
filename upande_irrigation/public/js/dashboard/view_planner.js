/* Irrigation plan — the daily water balance and the run sheet, in four tabs.
 *
 *   Today   the farm's run sheet: a per-pump timeline and each cycle with the
 *           operator's actions (Start / Done / Partial / Skip). What is ticked
 *           here is what the water balance counts as irrigated.
 *   Week    each shift's due days and hours for the next 7 days, and each
 *           pump's planned hours against its run-window hours.
 *   Blocks  every block's root-zone depletion against its irrigation point
 *           (RAW) and capacity (TAW); click one for its 30-day balance.
 *   Budget  per section and week: water the crop needed, water recorded as
 *           applied, and water the section meter says went out.
 *
 * Replaces the weekly-planner Gantt (api.planner), retired 2026-09-28.
 * Tabs deep-link: #planner?tab=blocks.
 */

import { pagehead, kpi, statusStrip, icon } from "./shell.js";

const TABS = [
	["today", "Today"],
	["week", "Week"],
	["blocks", "Blocks"],
	["budget", "Budget"],
];
const STATUS_TONE = { Planned: "ink", Running: "lo", Done: "lo", Partial: "mo", Skipped: "hi", "Not placed": "hi" };
const C = { raw: "#d9962e", taw: "#c4302b", d: "#228883", irr: "#3268c4", rain: "#8a8780" };

function tabFromHash() {
	const q = (location.hash || "").split("?")[1] || "";
	const t = new URLSearchParams(q).get("tab");
	return TABS.some(([id]) => id === t) ? t : "today";
}

function hhmm(s) {
	const d = new Date(String(s || "").replace(" ", "T"));
	return isNaN(d) ? "—" : d.toTimeString().slice(0, 5);
}

export default {
	id: "planner",
	label: "Irrigation plan",
	pollMs: 60000,

	mount(el, ctx) {
		this.ctx = ctx;
		this.el = el;
		this.tab = tabFromHash();
		/* Local date, not toISOString() — that is the UTC date, which between
		 * 00:00 and 03:00 in Kenya is still yesterday. */
		const d = new Date();
		this.date = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
		this.sort = { key: "ratio", dir: -1 };
		el.innerHTML = `
${pagehead(
	"Irrigation plan",
	"Daily water balance",
	"Each block's soil water, from weather and what was actually irrigated — and today's run sheet",
	`<input class="input" type="date" id="pl-date" value="${this.date}" aria-label="Run sheet date" style="width:150px">
	 <button class="btn" id="pl-generate" type="button">${icon("refresh")}Generate run sheet</button>`
)}
<div class="status" id="pl-status"></div>
<div class="kpi-grid stagger" id="pl-kpis"></div>
<div class="stabs" id="pl-tabs">${TABS.map(([id, label]) => `<button class="stab${id === this.tab ? " on" : ""}" type="button" data-tab="${id}">${label}</button>`).join("")}</div>
<div id="pl-body"><div class="loading">Loading…</div></div>`;

		el.querySelectorAll("#pl-tabs .stab").forEach((b) =>
			b.addEventListener("click", () => {
				this.tab = b.getAttribute("data-tab");
				history.replaceState(null, "", `#planner?tab=${this.tab}`);
				el.querySelectorAll("#pl-tabs .stab").forEach((x) => x.classList.toggle("on", x === b));
				this.refresh();
			})
		);
		el.querySelector("#pl-date").addEventListener("change", (e) => {
			this.date = e.target.value;
			this.refresh();
		});
		el.querySelector("#pl-generate").addEventListener("click", () => this.generate());
		this.unsubscribe = ctx.onFilterChange(() => {});
	},

	async generate() {
		const { api, filters } = this.ctx;
		const btn = this.el.querySelector("#pl-generate");
		const status = this.el.querySelector("#pl-status");
		btn.disabled = true;
		statusStrip(status, "Updating the water balance and planning…", "ok");
		try {
			const { data } = await api.post("upande_irrigation.api.runsheet.generate", { farm: filters.farm || "", date: this.date });
			statusStrip(status, (data || []).map((x) => `${x.farm}: ${x.summary}`).join(" · ") || "No farm has block profiles yet.", "ok");
			this.tab = "today";
			this.el.querySelectorAll("#pl-tabs .stab").forEach((x) => x.classList.toggle("on", x.getAttribute("data-tab") === "today"));
			await this.refresh(true);
		} catch (err) {
			statusStrip(status, `Could not generate: ${err.message}`);
		} finally {
			btn.disabled = false;
		}
	},

	async refresh(keepStatus) {
		const { api, filters, shell } = this.ctx;
		if (!keepStatus) statusStrip(this.el.querySelector("#pl-status"), "");
		const body = this.el.querySelector("#pl-body");
		try {
			const { data: blocks } = await api.get("upande_irrigation.api.balance.block_status", { farm: filters.farm });
			this.blocks = blocks || [];
			shell.setFarms([...new Set(this.blocks.map((b) => b.farm).filter(Boolean))]);
			this.renderKpis();
			if (this.tab === "today") await this.renderToday(body);
			else if (this.tab === "week") await this.renderWeek(body);
			else if (this.tab === "blocks") this.renderBlocks(body);
			else await this.renderBudget(body);
		} catch (err) {
			statusStrip(this.el.querySelector("#pl-status"), `Could not load the plan: ${err.message}`);
		}
	},

	renderKpis() {
		const b = this.blocks || [];
		const due = b.filter((x) => x.raw_mm && x.depletion_mm >= x.raw_mm).length;
		const soon = b.filter((x) => x.trigger_day != null && x.trigger_day <= 2 && !(x.depletion_mm >= x.raw_mm)).length;
		const mean = b.length ? Math.round(b.reduce((s, x) => s + x.depletion_pct, 0) / b.length) : null;
		const stale = b.filter((x) => x.stale_weather).length;
		const defaults = b.filter((x) => (x.using_defaults || []).some((f) => ["soil_texture", "planting_year", "emitter_flow_lph"].includes(f))).length;
		this.el.querySelector("#pl-kpis").innerHTML =
			kpi(due ? "var(--ui-hot)" : "var(--ui-ok)", "Due now", due, `of ${b.length}`, "blocks at or past their irrigation point") +
			kpi("var(--ui-warn)", "Due within 2 days", soon, "", "blocks") +
			kpi("var(--ui-teal)", "Mean depletion", mean, "%", "of available root-zone water") +
			kpi(stale ? "var(--ui-hot)" : "var(--ui-ok)", "Weather", stale ? "estimated" : "logged", "", stale ? `${stale} blocks on estimated weather` : "daily readings are current") +
			kpi(defaults ? "var(--ui-clay)" : "var(--ui-ok)", "On defaults", defaults, "", "blocks missing soil / age / emitters");
	},

	/* ── Today ─────────────────────────────────────────────────── */
	async renderToday(body) {
		const { api, charts, filters } = this.ctx;
		const { data } = await api.get("upande_irrigation.api.runsheet.today", { farm: filters.farm, date: this.date });
		const sheets = (data && data.sheets) || [];
		if (!sheets.length) {
			body.innerHTML = '<div class="card"><div class="empty">No farm has Irrigation Block Profiles yet.</div></div>';
			return;
		}
		body.innerHTML = sheets
			.map((sh) => {
				if (!sh.run_sheet) {
					return `<div class="card"><div class="card__head"><h3>${icon("drop")}${charts.esc(sh.farm)}</h3></div>
	<div class="empty">No run sheet for ${charts.esc(charts.fmtDate(this.date))} yet. <button class="btn small" type="button" data-gen="${charts.esc(sh.farm)}">Generate it</button></div></div>`;
				}
				const runs = sh.runs || [];
				return `<div class="card">
	<div class="card__head">
		<h3>${icon("drop")}${charts.esc(sh.farm)} · ${charts.esc(charts.fmtDate(sh.date))}</h3>
		<span class="meta">${charts.esc(sh.summary || "")}${sh.generated_at ? ` · generated ${charts.esc(charts.fmtClock(sh.generated_at))}` : ""}
			· <a href="/app/irrigation-run-sheet/${encodeURIComponent(sh.run_sheet)}" target="_blank" rel="noopener">open record</a></span>
	</div>
	${this.timeline(runs)}
	<div class="tablewrap scroll"><table class="table rs-table">
		<thead><tr><th>Time</th><th>Shift</th><th>Cycle</th><th class="num">Hours</th><th class="num">Refill</th><th>Blocks (depletion)</th><th>Status</th><th></th></tr></thead>
		<tbody>${runs.map((r) => this.runRow(r)).join("") || '<tr><td colspan="8"><div class="empty small">Nothing due today — every block is above its irrigation point.</div></td></tr>'}</tbody>
	</table></div>
</div>`;
			})
			.join("");
		body.querySelectorAll("[data-gen]").forEach((b) => b.addEventListener("click", () => this.generate()));
		body.querySelectorAll("[data-act]").forEach((b) =>
			b.addEventListener("click", () => this.act(b.getAttribute("data-run"), b.getAttribute("data-act"), b.getAttribute("data-from")))
		);
	},

	timeline(runs) {
		const { charts } = this.ctx;
		const placed = runs.filter((r) => r.planned_start);
		if (!placed.length) return "";
		const pumps = [...new Set(placed.map((r) => r.pump))];
		const day0 = new Date(`${this.date}T00:00:00`).getTime();
		const pct = (s) => Math.max(0, Math.min(100, ((new Date(String(s).replace(" ", "T")).getTime() - day0) / 864e5) * 100));
		const now = (Date.now() - day0) / 864e5;
		return `<div class="rs-tl">
	<div class="rs-tl__scale">${[0, 3, 6, 9, 12, 15, 18, 21, 24].map((h) => `<span style="left:${(h / 24) * 100}%">${String(h).padStart(2, "0")}</span>`).join("")}</div>
	${pumps
		.map(
			(p) => `<div class="rs-tl__row"><div class="rs-tl__pump" title="${charts.esc(p)}">${charts.esc(p)}</div><div class="rs-tl__lane">
		${now >= 0 && now <= 1 ? `<i class="rs-tl__now" style="left:${now * 100}%"></i>` : ""}
		${placed
			.filter((r) => r.pump === p)
			.map((r) => {
				const a = pct(r.planned_start);
				const w = Math.max(0.6, pct(r.planned_end) - a);
				return `<span class="rs-tl__bar ${String(r.status).toLowerCase().replace(" ", "-")} ${r.kind === "Ahead" ? "ahead" : ""}" style="left:${a}%;width:${w}%" title="${charts.esc(`${r.shift} · cycle ${r.cycle_no}/${r.cycles} · ${hhmm(r.planned_start)}–${hhmm(r.planned_end)} · ${r.status}`)}">${charts.esc(r.shift.replace(/^.* - SHIFT /, "S"))}</span>`;
			})
			.join("")}
	</div></div>`
		)
		.join("")}
</div>`;
	},

	runRow(r) {
		const { charts } = this.ctx;
		const acts = [];
		if (r.status === "Planned" || r.status === "Not placed") acts.push(["Running", "Start"]);
		if (["Planned", "Running", "Not placed"].includes(r.status)) acts.push(["Done", "Done"], ["Partial", "Partial"], ["Skipped", "Skip"]);
		if (["Done", "Partial", "Skipped", "Running"].includes(r.status)) acts.push(["Planned", "Undo"]);
		const blocks = (r.blocks || [])
			.map((b) => `<span class="rs-chip ${b.depletion_mm >= b.raw_mm ? "due" : ""}" title="${charts.esc(`${b.depletion_mm} mm of ${b.raw_mm} mm RAW`)}">${charts.esc(b.label)} ${Math.round(b.depletion_pct)}%</span>`)
			.join("");
		const time = r.planned_start ? `${hhmm(r.planned_start)}–${hhmm(r.planned_end)}` : "—";
		const actual = r.status === "Partial" || r.status === "Done" ? `<div class="list__meta">ran ${charts.fmtNum(r.actual_hours || 0, 1)} h</div>` : "";
		const why = [r.skip_reason ? `Skipped: ${r.skip_reason}` : "", r.reason || ""].filter(Boolean).join(" · ");
		return `<tr>
	<td class="num">${time}</td>
	<td><b>${charts.esc(r.shift)}</b>${r.kind === "Ahead" ? ' <span class="sev gold">ahead</span>' : ""}<div class="list__meta">${charts.esc(r.pump || "")}</div></td>
	<td>${r.cycle_no || 1}/${r.cycles || 1}</td>
	<td class="num">${charts.fmtNum(r.planned_hours, 1)}${actual}</td>
	<td class="num">${r.net_mm != null ? `${charts.fmtNum(r.net_mm, 0)} mm` : "—"}</td>
	<td>${blocks}${why ? `<div class="list__meta rs-why">${charts.esc(why)}</div>` : ""}</td>
	<td><span class="sev ${STATUS_TONE[r.status] || "ink"}">${charts.esc(r.status)}</span></td>
	<td class="rs-acts">${acts.map(([s, l]) => `<button class="btn ghost small" type="button" data-run="${charts.esc(r.name)}" data-act="${s}" data-from="${charts.esc(r.status)}">${l}</button>`).join("")}</td>
</tr>`;
	},

	async act(run, status, from) {
		const { api } = this.ctx;
		const args = { run, status };
		/* A cycle that was never placed has no planned time to credit, so Done
		 * asks for the hours actually run, like Partial. */
		if (status === "Partial" || (status === "Done" && from === "Not placed")) {
			const h = window.prompt("Hours actually run?");
			if (!h) return;
			args.actual_hours = h;
		}
		if (status === "Skipped") {
			const why = window.prompt("Why was it skipped? (e.g. pipe burst, power cut, rain)");
			if (!why) return;
			args.skip_reason = why;
		}
		try {
			await api.post("upande_irrigation.api.runsheet.set_status", args);
			await this.refresh();
		} catch (err) {
			statusStrip(this.el.querySelector("#pl-status"), `Could not update: ${err.message}`);
		}
	},

	/* ── Week ──────────────────────────────────────────────────── */
	async renderWeek(body) {
		const { api, charts, filters } = this.ctx;
		const { data } = await api.get("upande_irrigation.api.runsheet.week", { farm: filters.farm, date: this.date });
		const farms = (data && data.farms) || [];
		const dayLabel = (d) => {
			const x = new Date(`${d}T00:00:00`);
			return `${x.toLocaleDateString([], { weekday: "short" })} ${x.getDate()}`;
		};
		body.innerHTML =
			farms
				.map(
					(f) => `<div class="card">
	<div class="card__head"><h3>${icon("calendar")}${charts.esc(f.farm)} — next 7 days</h3><span class="meta">assumes no rain and that each due run refills the shift's driest block</span></div>
	${f.shifts.length ? "" : '<div class="empty">No shifts are mapped to this farm\'s blocks yet — set them on Irrigation Scheduler.</div>'}
	<div class="tablewrap scroll"${f.shifts.length ? "" : " hidden"}><table class="table wk-table">
		<thead><tr><th>Shift</th>${f.days.map((d) => `<th class="num">${dayLabel(d)}</th>`).join("")}</tr></thead>
		<tbody>
			${Object.keys(f.load)
				.map(
					(p) => `<tr class="wk-load"><td><b>${charts.esc(p)}</b><div class="list__meta">planned h / window h</div></td>${f.days
						.map((d) => {
							const h = f.load[p][d] || 0;
							const cap = (f.capacity[p] || {})[d] || 0;
							return `<td class="num"><span class="sev ${h > cap ? "hi" : h ? "lo" : "ink"}">${charts.fmtNum(h, 1)} / ${charts.fmtNum(cap, 0)}</span></td>`;
						})
						.join("")}</tr>`
				)
				.join("")}
			${f.shifts
				.map(
					(s) => `<tr><td><b>${charts.esc(s.shift)}</b><div class="list__meta">${charts.esc(s.pump)}</div></td>${s.cells
						.map((c) => (c.due ? `<td class="num"><span class="wk-due">${charts.fmtNum(c.hours, 1)} h</span></td>` : `<td class="num wk-dry">${Math.round(c.depletion_pct)}%</td>`))
						.join("")}</tr>`
				)
				.join("")}
		</tbody>
	</table></div>
</div>`
				)
				.join("") || '<div class="card"><div class="empty">No shifts with block profiles.</div></div>';
	},

	/* ── Blocks ────────────────────────────────────────────────── */
	renderBlocks(body) {
		const { charts } = this.ctx;
		const rows = [...(this.blocks || [])].map((b) => ({ ...b, ratio: b.raw_mm ? b.depletion_mm / b.raw_mm : 0 }));
		const k = this.sort.key;
		rows.sort((a, b) => (a[k] > b[k] ? 1 : a[k] < b[k] ? -1 : 0) * this.sort.dir);
		const th = (key, label, cls = "") => `<th class="${cls} sortable" data-sort="${key}">${label}${this.sort.key === key ? (this.sort.dir > 0 ? " ▲" : " ▼") : ""}</th>`;
		body.innerHTML = `<div class="row-2-eq bk-wrap">
	<div class="card">
		<div class="card__head"><h3>${icon("drop")}Blocks</h3><span class="meta">${rows.length} blocks · click one for its balance</span></div>
		<div class="tablewrap scroll" style="max-height:640px"><table class="table bk-table">
			<thead><tr>${th("label", "Block")}${th("ratio", "Soil water")}${th("depletion_pct", "Depleted", "num")}${th("trigger_day", "Due in", "num")}${th("last_irrigated", "Last irrigated")}${th("rate_mm_hr", "Rate", "num")}</tr></thead>
			<tbody>${rows
				.map(
					(b) => `<tr data-block="${charts.esc(b.block)}" class="${this.selected === b.block ? "on" : ""}">
	<td><b>${charts.esc(b.label)}</b><div class="list__meta">${charts.esc((b.section || "").replace(/_SECTION.*$/, ""))}${(b.using_defaults || []).length ? " · defaults" : ""}</div></td>
	<td>${this.gauge(b)}</td>
	<td class="num">${Math.round(b.depletion_pct)}%</td>
	<td class="num">${b.depletion_mm >= b.raw_mm ? '<span class="sev hi">now</span>' : b.trigger_day != null ? `${b.trigger_day} d` : "> 7 d"}</td>
	<td>${b.last_irrigated ? charts.esc(charts.fmtDate(b.last_irrigated)) : '<span class="list__meta">none recorded</span>'}</td>
	<td class="num">${b.rate_mm_hr ? `${charts.fmtNum(b.rate_mm_hr, 2)} mm/h` : '<span class="sev hi">none</span>'}</td>
</tr>`
				)
				.join("")}</tbody>
		</table></div>
	</div>
	<div class="card" id="bk-detail"><div class="empty">Choose a block to see its last 30 days and the week ahead.</div></div>
</div>`;
		body.querySelectorAll("th[data-sort]").forEach((h) =>
			h.addEventListener("click", () => {
				const key = h.getAttribute("data-sort");
				this.sort = { key, dir: this.sort.key === key ? -this.sort.dir : -1 };
				this.renderBlocks(body);
			})
		);
		body.querySelectorAll("tr[data-block]").forEach((tr) =>
			tr.addEventListener("click", () => {
				this.selected = tr.getAttribute("data-block");
				body.querySelectorAll("tr[data-block]").forEach((x) => x.classList.toggle("on", x === tr));
				this.renderDetail(this.selected);
			})
		);
		if (this.selected) this.renderDetail(this.selected);
	},

	/* Depletion against the irrigation point (RAW, amber tick) and the root
	 * zone's capacity (TAW, the full bar). */
	gauge(b) {
		if (!b.taw_mm) return "—";
		const d = Math.min(100, (100 * b.depletion_mm) / b.taw_mm);
		const raw = (100 * b.raw_mm) / b.taw_mm;
		const tone = b.depletion_mm >= b.raw_mm ? "var(--ui-hot)" : d >= raw * 0.8 ? "var(--ui-warn)" : "var(--ui-teal)";
		return `<div class="bk-gauge" title="${b.depletion_mm} mm used of ${b.taw_mm} mm · irrigate at ${b.raw_mm} mm"><i style="width:${d}%;background:${tone}"></i><b style="left:${raw}%"></b></div>`;
	},

	async renderDetail(block) {
		const { api, charts } = this.ctx;
		const host = this.el.querySelector("#bk-detail");
		if (!host) return;
		host.innerHTML = '<div class="loading">Loading…</div>';
		const { data } = await api.get("upande_irrigation.api.balance.block_series", { block, days: 30 });
		const rows = (data && data.rows) || [];
		const st = (data && data.status) || {};
		if (!rows.length) {
			host.innerHTML = '<div class="empty">No balance yet for this block — generate a run sheet to build it.</div>';
			return;
		}
		const proj = st.projection || [];
		const labels = rows.map((r) => r.date).concat(proj.map((_, i) => `+${i + 1}d`));
		const dep = rows.map((r) => r.depletion_mm).concat(proj.map(() => null));
		const fut = rows.map((_, i) => (i === rows.length - 1 ? rows[i].depletion_mm : null)).concat(proj.map((v) => Math.min(v, st.taw_mm || v)));
		const raw = labels.map(() => st.raw_mm);
		host.innerHTML = `<div class="card__head"><h3>${charts.esc(st.label || block)}</h3>
	<span class="meta">${charts.esc(st.section || "")} · rate ${charts.fmtNum(st.rate_mm_hr || 0, 2)} mm/h · ETc ${charts.fmtNum(st.mean_etc_mm || 0, 1)} mm/day
	· <a href="/app/irrigation-block-profile/${encodeURIComponent(block)}" target="_blank" rel="noopener">profile</a></span></div>
<div id="bk-chart"></div>
<div class="clegend">
	<span><i class="ln" style="background:${C.d}"></i>Depletion (mm below full)</span>
	<span><i class="ln" style="background:${C.d};opacity:.5"></i>Projected, no rain</span>
	<span><i class="ln" style="background:${C.raw}"></i>Irrigate at (RAW ${charts.fmtNum(st.raw_mm, 0)} mm)</span>
	<span><i style="background:${C.irr}"></i>Irrigation recorded</span>
</div>
<div class="bk-facts">
	<span>Root zone holds <b>${charts.fmtNum(st.taw_mm, 0)} mm</b></span>
	<span>Now <b>${charts.fmtNum(st.depletion_mm, 1)} mm</b> used (${Math.round(st.depletion_pct || 0)}%)</span>
	<span>Due <b>${st.depletion_mm >= st.raw_mm ? "now" : st.trigger_day != null ? `in ${st.trigger_day} d` : "not within 7 d"}</b></span>
	<span>Last irrigated <b>${st.last_irrigated ? charts.esc(charts.fmtDate(st.last_irrigated)) : "none recorded"}</b></span>
	${(st.using_defaults || []).length ? `<span>Defaults for <b>${charts.esc(st.using_defaults.map((x) => x.replace(/_/g, " ")).join(", "))}</b></span>` : ""}
</div>`;
		charts.mkChart(
			host.querySelector("#bk-chart"),
			[
				{ label: "Irrigation", color: C.irr, type: "bar", values: rows.map((r) => r.irrigation_mm || null).concat(proj.map(() => null)), unit: "mm", opacity: 0.55 },
				{ label: "Depletion", color: C.d, values: dep, unit: "mm", width: 2.4 },
				{ label: "Projected", color: C.d, values: fut, unit: "mm", width: 2, dash: "5 4" },
				{ label: "RAW", color: C.raw, values: raw, unit: "mm", width: 1.5, dash: "2 3" },
			],
			620,
			260,
			{ xLabels: labels, tooltip: true, noFill: true, yMin: 0, yMax: Math.ceil((st.taw_mm || 50) / 10) * 10 }
		);
	},

	/* ── Budget ────────────────────────────────────────────────── */
	async renderBudget(body) {
		const { api, charts, filters } = this.ctx;
		const { data } = await api.get("upande_irrigation.api.runsheet.budget", { farm: filters.farm, weeks: 8 });
		const rows = (data && data.rows) || [];
		if (!rows.length) {
			body.innerHTML = '<div class="card"><div class="empty">No balance history yet.</div></div>';
			return;
		}
		const n = (v) => (v == null ? "—" : Number(v).toLocaleString());
		body.innerHTML = `<div class="card">
	<div class="card__head"><h3>${icon("resources")}Water budget</h3><span class="meta">m³ per section per week (Monday start) · needed = crop use over the blocks' area</span></div>
	<div class="tablewrap scroll"><table class="table">
		<thead><tr><th>Section</th><th>Week of</th><th class="num">Crop needed</th><th class="num">Rain supplied</th><th class="num">Irrigation recorded</th><th class="num">Metered</th><th class="num">Meter vs recorded</th></tr></thead>
		<tbody>${rows
			.map(
				(r) => `<tr>
	<td><b>${charts.esc((r.section || "—").replace(/_SECTION.*$/, ""))}</b></td>
	<td>${charts.esc(charts.fmtDate(r.week))}</td>
	<td class="num">${n(r.needed_m3)}</td>
	<td class="num">${n(r.rain_m3)}</td>
	<td class="num">${n(r.recorded_m3)}</td>
	<td class="num">${n(r.metered_m3)}</td>
	<td class="num">${r.gap_pct == null ? '<span class="list__meta">no meter</span>' : `<span class="sev ${Math.abs(r.gap_pct) > 20 ? "hi" : "lo"}">${r.gap_pct > 0 ? "+" : ""}${r.gap_pct}%</span>`}</td>
</tr>`
			)
			.join("")}</tbody>
	</table></div>
	<p class="list__meta" style="margin-top:10px">A gap over 20% means the meter and the recorded runs disagree — a run not ticked on the run sheet, a leak, or a meter reading to check.</p>
</div>`;
	},

	unmount() {
		if (this.unsubscribe) this.unsubscribe();
	},
};
