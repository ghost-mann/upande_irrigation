/* Dashboard shell: sidebar, hash routing, shared filters, poll ownership.
 *
 * Frame follows the Mona Flowers scouting page — a rounded sidebar carrying
 * views, filters, today's numbers, a legend and the signed-in user, beside a
 * main column. The shell knows nothing about any view's internals; it mounts one
 * at a time through the contract each view_*.js exports:
 *
 *   { id, label, icon, mount(el, ctx), refresh(), unmount(), pollMs }
 *
 * Only the active view is mounted, so polls cannot stack up behind view
 * switches — a real bug shape in the old pages, where /irrigation-now and
 * /meniscus each ran their own intervals for as long as the tab lived.
 */

/* A relative specifier resolves against this module's path without its query,
 * so `import "./charts.js"` would bypass the ?v= cache token the page puts on
 * every asset URL and could serve a stale copy for up to twelve hours after a
 * deploy. Carry the token across explicitly. Views never import these two
 * directly — they receive them on ctx — so this is the only place it matters. */
const ASSET_VERSION = new URL(import.meta.url).search;

const [api, charts] = await Promise.all([
	import(`./api.js${ASSET_VERSION}`),
	import(`./charts.js${ASSET_VERSION}`),
]);

const DEFAULT_VIEW = "overview";

const ICONS = {
	overview:
		'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
	weather:
		'<path d="M17.5 19a4.5 4.5 0 1 0-.5-8.97A6 6 0 0 0 5 11a4 4 0 0 0 0 8h12.5z"/><path d="M8 21v2M12 21v2M16 21v2"/>',
	compare: '<path d="M3 3v18h18"/><path d="M7 14l4-4 4 4 5-5"/>',
	iot: '<circle cx="12" cy="12" r="2"/><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>',
	resources: '<path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"/><path d="M9 15h6"/>',
	map: '<polygon points="1 6 8 3 16 6 23 3 23 18 16 21 8 18 1 21"/><line x1="8" y1="3" x2="8" y2="18"/><line x1="16" y1="6" x2="16" y2="21"/>',
	now: '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15.5 14"/>',
	control:
		'<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
	desk: '<rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>',
	drop: '<path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"/>',
	alert: '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
	check: '<polyline points="20 6 9 17 4 12"/>',
	refresh:
		'<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
	planner:
		'<rect x="3" y="4" width="18" height="17" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="16" y1="2" x2="16" y2="6"/><rect x="6" y="12" width="5" height="3" rx="1"/><rect x="13" y="16" width="5" height="3" rx="1"/>',
	calendar:
		'<rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
	target: '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
	trend: '<polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/>',
	zap: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
	pin: '<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
	help: '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
	close: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
	gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h0a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h0a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
};

export function icon(name, extra = "") {
	return `<svg viewBox="0 0 24 24" ${extra}>${ICONS[name] || ""}</svg>`;
}

/* Sidebar order. The separator splits "look at the data" from "act on it". */
const NAV = [
	["overview", "Overview"],
	["planner", "Planning"],
	["weather", "Weather"],
	["compare", "Compare"],
	["iot", "IoT Sensors"],
	["resources", "Water & Energy"],
	["map", "Field Map"],
	["__sep__"],
	["now", "Irrigation Now"],
	["control", "Valve Control"],
];

const PERIODS = [
	[7, "Last 7 days"],
	[30, "Last 30 days"],
	[90, "Last 90 days"],
	[365, "Last year"],
	[1825, "Last 5 years"],
	[3650, "All time"],
];

function todayStr() {
	const d = new Date();
	return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function daysAgoStr(n) {
	const d = new Date();
	d.setDate(d.getDate() - n);
	return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/* ══════════════════════════════════════════════ Shell */

export class Shell {
	constructor({ mount, views, boot }) {
		this.root = mount;
		this.views = new Map(views.map((v) => [v.id, v]));
		this.boot = boot || {};
		this.active = null;
		this.activeId = null;
		this.pollTimer = null;

		this.filters = {
			farm: "",
			days: 30,
			from: daysAgoStr(29),
			to: todayStr(),
			section: "",
		};
		/* Populated by whichever view first learns the farm list; the sidebar
		 * picker is shared, so it must not depend on view order. */
		this.farms = (this.boot.farms || []).slice();
		this.filterListeners = new Set();
	}

	ctx() {
		return {
			api,
			charts,
			icon,
			shell: this,
			filters: this.filters,
			user: this.boot.user || "",
			setFarms: (farms) => this.setFarms(farms),
			setStats: (rows, label) => this.setStats(rows, label),
			onFilterChange: (fn) => {
				this.filterListeners.add(fn);
				return () => this.filterListeners.delete(fn);
			},
			go: (id) => this.go(id),
		};
	}

	render() {
		const nav = NAV.map(([id, label]) => {
			if (id === "__sep__") return '<div class="side__sep"></div>';
			if (!this.views.has(id)) return "";
			return `<a class="side__link${this.activeId === id ? " on" : ""}" data-view="${id}" href="#${id}" title="${label}">${icon(id)}<span>${label}</span><span class="n" data-count="${id}"></span></a>`;
		}).join("");

		const b = this.boot;

		this.root.innerHTML = `
<div class="page">
	<aside class="side">
		<div class="side__section">
			<div class="side__label">Views</div>
			<nav class="side__nav">
				${nav}
				<div class="side__sep"></div>
				<a class="side__link" href="/desk/upande-irrigation" title="Upande Irrigation workspace">${icon("desk")}<span>Desk</span></a>
			</nav>
		</div>

		<div class="side__section">
			<div class="side__label">Filters</div>
			<div class="side__date" id="f-range"><span>—</span>${icon("calendar")}</div>
			<div class="side__label" style="margin-top:16px">Farm</div>
			<div class="side__chips" id="f-farms"></div>
			<div class="side__label" style="margin-top:16px">Period</div>
			<label class="side__field">
				<select class="select" id="f-days">
					${PERIODS.map(([d, l]) => `<option value="${d}"${d === this.filters.days ? " selected" : ""}>${l}</option>`).join("")}
				</select>
			</label>
			<div class="side__chips" style="gap:8px">
				<label class="side__field" style="flex:1 1 100px;margin:0">
					<input class="input" type="date" id="f-from" aria-label="From">
				</label>
				<label class="side__field" style="flex:1 1 100px;margin:0">
					<input class="input" type="date" id="f-to" aria-label="To">
				</label>
			</div>
		</div>

		<div class="side__section" id="side-stats-wrap" hidden>
			<div class="side__label" id="side-stats-label">This week</div>
			<div id="side-stats"></div>
		</div>

		<div class="side__section">
			<div class="side__label">Legend</div>
			<div class="side__legend">
				<span><i style="background:var(--sev-high)"></i>Deficit · action needed</span>
				<span><i style="background:var(--sev-mod)"></i>Watch · pump-capped</span>
				<span><i style="background:var(--sev-low)"></i>On track · irrigating</span>
				<span><i style="background:var(--trap-500)"></i>Scheduled shift</span>
				<span><i style="background:rgba(10,10,10,0.06)"></i>Not scheduled</span>
			</div>
		</div>

		<div class="side__section side__user">
			<div class="avatar">${charts.esc(b.user_initials || "??")}</div>
			<div class="side__user-info">
				<b>${charts.esc(b.user_label || b.user || "")}</b>
				<small>${charts.esc(b.organisation ? `Irrigation · ${b.organisation}` : "Irrigation")}</small>
			</div>
			<a class="side__gear" href="/app/irrigation-settings" title="Irrigation Settings">${icon("gear")}</a>
		</div>
	</aside>
	<div class="main" id="ui-main"></div>
</div>`;

		this.el = { main: this.root.querySelector("#ui-main") };
		this.bind();
		this.syncFilterInputs();
		this.renderFarmChips();
	}

	bind() {
		this.root.querySelectorAll(".side__link[data-view]").forEach((a) => {
			a.addEventListener("click", (e) => {
				e.preventDefault();
				this.go(a.getAttribute("data-view"));
			});
		});

		const days = this.root.querySelector("#f-days");
		const from = this.root.querySelector("#f-from");
		const to = this.root.querySelector("#f-to");

		days.addEventListener("change", () => {
			const d = parseInt(days.value, 10) || 30;
			this.filters.days = d;
			this.filters.from = daysAgoStr(d - 1);
			this.filters.to = todayStr();
			from.value = this.filters.from;
			to.value = this.filters.to;
			this.syncRangeLabel();
			this.emitFilters();
		});

		const applyRange = () => {
			if (!from.value || !to.value) return;
			if (from.value > to.value) {
				/* Keep the pair ordered rather than querying backwards. */
				if (document.activeElement === from) to.value = from.value;
				else from.value = to.value;
			}
			this.filters.from = from.value;
			this.filters.to = to.value;
			const ms = new Date(`${to.value}T00:00:00`) - new Date(`${from.value}T00:00:00`);
			this.filters.days = Math.max(1, Math.round(ms / 86400000) + 1);
			this.syncRangeLabel();
			this.emitFilters();
		};
		from.addEventListener("change", applyRange);
		to.addEventListener("change", applyRange);

		window.addEventListener("hashchange", () => this.go(this.hashView()));
	}

	syncFilterInputs() {
		const to = this.root.querySelector("#f-to");
		const from = this.root.querySelector("#f-from");
		const days = this.root.querySelector("#f-days");
		to.max = todayStr();
		to.value = this.filters.to;
		from.value = this.filters.from;
		days.value = String(this.filters.days);
		this.syncRangeLabel();
	}

	syncRangeLabel() {
		const el = this.root.querySelector("#f-range span");
		if (el) {
			el.textContent = `${charts.fmtDate(this.filters.from)} – ${charts.fmtDate(this.filters.to)}`;
		}
	}

	/* Farms render as chips, as on the reference page — an "All farms" chip plus
	 * one per farm, which reads better than a select when there are few. */
	renderFarmChips() {
		const host = this.root.querySelector("#f-farms");
		if (!host) return;

		/* Some sites carry a farm per business unit — sixteen on kaitet.local —
		 * which floods a 264px sidebar. Show a handful and let the operator
		 * expand, as the reference page does with "+15 more". The selected farm
		 * is always visible even if it sorts past the cut. */
		const CAP = 6;
		let farms = this.farms.slice();
		let hidden = 0;
		if (!this.farmsExpanded && farms.length > CAP) {
			const shown = farms.slice(0, CAP);
			if (this.filters.farm && !shown.includes(this.filters.farm)) {
				shown[CAP - 1] = this.filters.farm;
			}
			hidden = farms.length - shown.length;
			farms = shown;
		}

		const chips = [["", "All farms"]].concat(farms.map((f) => [f, f]));
		host.innerHTML =
			chips
				.map(
					([value, label]) =>
						`<button class="side__chip${value === this.filters.farm ? " on" : ""}" type="button" data-farm="${charts.esc(value)}">${charts.esc(label)}</button>`
				)
				.join("") +
			(hidden
				? `<button class="side__chip" type="button" data-more="1">+ ${hidden} more</button>`
				: this.farmsExpanded && this.farms.length > CAP
					? '<button class="side__chip" type="button" data-more="0">Show fewer</button>'
					: "");

		host.querySelectorAll("[data-farm]").forEach((btn) => {
			btn.addEventListener("click", () => {
				this.filters.farm = btn.getAttribute("data-farm");
				this.renderFarmChips();
				this.emitFilters();
			});
		});
		const more = host.querySelector("[data-more]");
		if (more) {
			more.addEventListener("click", () => {
				this.farmsExpanded = more.getAttribute("data-more") === "1";
				this.renderFarmChips();
			});
		}
	}

	setFarms(farms) {
		/* The page boots with the farms flagged is_irrigation_farm. Some APIs
		 * (weather.fetch) return every farm on the site, and letting those
		 * override would make the sidebar's farm list change depending on which
		 * view you happened to open. The boot list wins when it exists. */
		if ((this.boot.farms || []).length) return;
		const names = [
			...new Set((farms || []).map((f) => (typeof f === "string" ? f : f && f.name)).filter(Boolean)),
		].sort();
		if (!names.length) return;
		const same = names.length === this.farms.length && names.every((n, i) => this.farms[i] === n);
		if (same) return;
		this.farms = names;
		this.renderFarmChips();
	}

	/* Views feed the sidebar's numbers block; it hides itself when empty. */
	setStats(rows, label) {
		const wrap = this.root.querySelector("#side-stats-wrap");
		const host = this.root.querySelector("#side-stats");
		if (!wrap || !host) return;
		if (!rows || !rows.length) {
			wrap.hidden = true;
			host.innerHTML = "";
			return;
		}
		if (label) this.root.querySelector("#side-stats-label").textContent = label;
		wrap.hidden = false;
		host.innerHTML = rows
			.map(
				(r) =>
					`<div class="side__stat"><small>${charts.esc(r.label)}</small><b>${charts.esc(String(r.value))}</b></div>`
			)
			.join("");
	}

	setCount(viewId, text) {
		const el = this.root.querySelector(`.side__link .n[data-count="${viewId}"]`);
		if (el) el.textContent = text == null ? "" : String(text);
	}

	emitFilters() {
		this.filterListeners.forEach((fn) => {
			try {
				fn(this.filters);
			} catch (e) {
				console.error("[irrigation] filter listener failed", e);
			}
		});
		if (this.active && this.active.refresh) this.safeRefresh();
	}

	hashView() {
		const id = (location.hash || "").replace(/^#/, "").split("?")[0];
		return this.views.has(id) ? id : DEFAULT_VIEW;
	}

	safeRefresh() {
		Promise.resolve()
			.then(() => this.active.refresh())
			.catch((err) => console.error(`[irrigation] ${this.activeId} refresh failed`, err));
	}

	go(id, { force = false } = {}) {
		const next = this.views.has(id) ? id : DEFAULT_VIEW;
		if (next === this.activeId && !force) return;

		/* Tear the old view down first: its timers must not outlive it. */
		if (this.pollTimer) {
			clearInterval(this.pollTimer);
			this.pollTimer = null;
		}
		if (this.active && this.active.unmount) {
			try {
				this.active.unmount();
			} catch (e) {
				console.error(`[irrigation] ${this.activeId} unmount failed`, e);
			}
		}
		charts.hideTip();
		this.setStats(null);

		this.activeId = next;
		this.active = this.views.get(next);

		this.root.querySelectorAll(".side__link[data-view]").forEach((a) => {
			a.classList.toggle("on", a.getAttribute("data-view") === next);
		});
		document.querySelectorAll(".topbar__nav a[href^='#']").forEach((a) => {
			a.classList.toggle("on", a.getAttribute("href") === `#${next}`);
		});
		/* Keep a deep link's query (#map?valve=…): only the view id is normalised. */
		if (location.hash.replace(/^#/, "").split("?")[0] !== next) {
			history.replaceState(null, "", `#${next}`);
		}

		this.el.main.innerHTML = '<div class="loading">Loading…</div>';
		try {
			this.active.mount(this.el.main, this.ctx());
		} catch (e) {
			console.error(`[irrigation] ${next} mount failed`, e);
			this.el.main.innerHTML = `<div class="card"><div class="alert hot">${icon("alert")}<span>This view failed to load: ${charts.esc(e.message || e)}</span></div></div>`;
			return;
		}
		this.safeRefresh();

		if (this.active.pollMs) {
			this.pollTimer = setInterval(() => this.safeRefresh(), this.active.pollMs);
		}
		window.scrollTo({ top: 0, behavior: "smooth" });
	}

	start() {
		this.render();
		this.go(this.hashView(), { force: true });
	}
}

/* ══════════════════════════════════════════════ shared view helpers */

/* `title` and `eyebrow` are escaped text. `sub` and `tools` are markup the view
 * supplies — every caller passes a literal template, never user data. Views that
 * update the eyebrow after a fetch target [data-eyebrow]. */
export function pagehead(title, eyebrow, sub, tools = "") {
	return `<div class="pagehead">
	<div>
		<div class="pagehead__eyebrow" data-eyebrow>${charts.esc(eyebrow || "")}</div>
		<h1 class="pagehead__title">${charts.esc(title)}</h1>
		${sub ? `<p class="pagehead__sub">${sub}</p>` : ""}
	</div>
	${tools ? `<div class="pagehead__tools">${tools}</div>` : ""}
</div>`;
}

/* kpi(accent, label, value, unit, note, spark, trend)
 *
 * `unit` sits inline after the number; `note` is the line under it; `spark` is
 * the small corner chart; `trend` is {text, tone} rendered as the pill along the
 * bottom, where tone is up | down | warn | flat. */
export function kpi(accent, label, value, unit, note, spark = "", trend = null) {
	const blank = value == null || value === "" || value === "—";
	const trendHtml = trend
		? `<div class="kpi__trend ${trend.tone || "flat"}">${charts.esc(trend.text)}${trend.note ? ` <small>${charts.esc(trend.note)}</small>` : ""}</div>`
		: "";
	return `<div class="kpi" style="--kc:${accent}">
	${spark ? `<div class="kpi__spark">${spark}</div>` : ""}
	<div class="kpi__label">${charts.esc(label)}</div>
	<div class="kpi__value${blank ? " none" : ""}">${blank ? "no data" : value}${unit && !blank ? `<small>${charts.esc(unit)}</small>` : ""}</div>
	<div class="kpi__unit">${charts.esc(note || "")}</div>
	${trendHtml}
</div>`;
}

export function statusStrip(el, message, kind = "hot") {
	if (!el) return;
	if (!message) {
		el.innerHTML = "";
		return;
	}
	const glyph = kind === "ok" ? "check" : "alert";
	el.innerHTML = `<div class="alert ${kind}">${icon(glyph)}<span>${charts.esc(message)}</span></div>`;
}

export function emptyCard(message) {
	return `<div class="card"><div class="empty">${charts.esc(message)}</div></div>`;
}
