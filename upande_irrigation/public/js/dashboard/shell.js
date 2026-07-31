/* Dashboard shell: sidebar, hash routing, shared filters, poll ownership.
 *
 * Structure mirrors Task Work Hub's render()/bind() so the two pages read the
 * same. The shell knows nothing about any view's internals — it mounts one
 * view at a time through the module contract in each view_*.js:
 *
 *   { id, label, icon, mount(el, ctx), refresh(), unmount(), pollMs }
 *
 * Only the active view is mounted, so polls can't stack up behind view
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

const RAIL_KEY = "ui-irr-rail";
const DEFAULT_VIEW = "overview";

const ICONS = {
	overview:
		'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
	weather:
		'<path d="M17.5 19a4.5 4.5 0 1 0-.5-8.97A6 6 0 0 0 5 11a4 4 0 0 0 0 8h12.5z"/><path d="M8 21v2M12 21v2M16 21v2"/>',
	compare: '<path d="M3 3v18h18"/><path d="M7 14l4-4 4 4 5-5"/>',
	iot: '<circle cx="12" cy="12" r="2"/><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>',
	resources:
		'<path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"/><path d="M9 15h6"/>',
	map: '<polygon points="1 6 8 3 16 6 23 3 23 18 16 21 8 18 1 21"/><line x1="8" y1="3" x2="8" y2="18"/><line x1="16" y1="6" x2="16" y2="21"/>',
	now: '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15.5 14"/>',
	control:
		'<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
	desk: '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
	drop: '<path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"/>',
	alert: '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
	check: '<polyline points="20 6 9 17 4 12"/>',
	refresh:
		'<path d="M1 4v6h6M23 20v-6h-6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
	calendar:
		'<rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
	target: '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
	trend: '<polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/>',
	zap: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
};

export function icon(name, extra = "") {
	return `<svg viewBox="0 0 24 24" ${extra}>${ICONS[name] || ""}</svg>`;
}

/* Sidebar order. The separator splits "look at the data" from "act on it". */
const NAV = [
	["overview", "Overview"],
	["weather", "Weather"],
	["compare", "Compare"],
	["iot", "IoT Sensors"],
	["resources", "Water & Energy"],
	["map", "Field Map"],
	["__sep__"],
	["now", "Irrigation Now"],
	["control", "Valve Control"],
];

/* ══════════════════════════════════════════════ date helpers */

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
		this.rail = localStorage.getItem(RAIL_KEY) === "1";

		this.filters = {
			farm: "",
			days: 30,
			from: daysAgoStr(29),
			to: todayStr(),
			section: "",
		};
		/* Populated by whichever view first learns the farm list; the sidebar
		 * farm picker is shared, so it must not depend on view order. */
		this.farms = (this.boot.farms || []).slice();
		this.filterListeners = new Set();
	}

	/* ── context handed to every view ── */
	ctx() {
		return {
			api,
			charts,
			icon,
			shell: this,
			filters: this.filters,
			user: this.boot.user || "",
			setFarms: (farms) => this.setFarms(farms),
			onFilterChange: (fn) => {
				this.filterListeners.add(fn);
				return () => this.filterListeners.delete(fn);
			},
			go: (id) => this.go(id),
		};
	}

	render() {
		const nav = NAV.map(([id, label]) => {
			if (id === "__sep__") return '<div class="ui-navsep"></div>';
			const v = this.views.get(id);
			if (!v) return "";
			return `<a class="ui-navlink${this.activeId === id ? " on" : ""}" data-view="${id}" href="#${id}" title="${label}">${icon(id)}<span class="lbl">${label}</span><span class="n" data-count="${id}"></span></a>`;
		}).join("");

		const chevron = this.rail
			? '<polyline points="9 18 15 12 9 6"/>'
			: '<polyline points="15 18 9 12 15 6"/>';

		this.root.innerHTML = `
<div class="ui-shell${this.rail ? " ui-shell--rail" : ""}" id="ui-shell">
	<aside class="ui-side" style="position:sticky">
		<button class="ui-collapse" type="button" title="${this.rail ? "Expand sidebar" : "Collapse sidebar"}">
			<svg viewBox="0 0 24 24">${chevron}</svg>
		</button>
		<div class="ui-brand">
			<div class="ui-brand-mark">${icon("drop")}</div>
			<div class="ui-brand-text"><b>Upande Irrigation</b><small>${charts.esc(this.boot.site_label || "Smart Irrigation")}</small></div>
		</div>
		<div class="ui-label">Views</div>
		<nav class="ui-nav">
			${nav}
			<a class="ui-navlink ui-desklink" href="/app/smart-irrigation" title="Back to Desk">${icon("desk")}<span class="lbl">Desk</span></a>
		</nav>
		<div class="ui-collapsible">
			<div class="ui-label" style="margin-top:18px">Filters</div>
			<div class="ui-entry" style="grid-template-columns:1fr">
				<div>
					<label for="ui-farm">Farm</label>
					<select class="ui-select" id="ui-farm"><option value="">All farms</option></select>
				</div>
				<div>
					<label for="ui-days">Period</label>
					<select class="ui-select" id="ui-days">
						<option value="7">Last 7 days</option>
						<option value="30" selected>Last 30 days</option>
						<option value="90">Last 90 days</option>
						<option value="365">Last year</option>
						<option value="1825">Last 5 years</option>
						<option value="3650">All time</option>
					</select>
				</div>
				<div>
					<label for="ui-from">From</label>
					<input class="ui-input" type="date" id="ui-from">
				</div>
				<div>
					<label for="ui-to">To</label>
					<input class="ui-input" type="date" id="ui-to">
				</div>
			</div>
			<div class="ui-label" style="margin-top:18px">Legend</div>
			<div class="ui-legend">
				<span><i style="background:var(--ui-ok)"></i>On track / irrigating</span>
				<span><i style="background:var(--ui-warn)"></i>Watch / capped</span>
				<span><i style="background:var(--ui-hot)"></i>Deficit / fault</span>
				<span><i style="background:var(--ui-clay)"></i>Needs action</span>
			</div>
		</div>
	</aside>
	<div class="ui-main" id="ui-main"></div>
</div>`;

		this.el = { main: this.root.querySelector("#ui-main"), shell: this.root.querySelector("#ui-shell") };
		this.bind();
		this.syncFilterInputs();
		this.setFarms(this.farms);
	}

	bind() {
		this.root.querySelector(".ui-collapse").addEventListener("click", () => {
			this.rail = !this.rail;
			localStorage.setItem(RAIL_KEY, this.rail ? "1" : "0");
			const wasActive = this.activeId;
			this.render();
			/* Re-render rebuilt the DOM; remount the same view into it. */
			this.activeId = null;
			this.go(wasActive, { force: true });
		});

		this.root.querySelectorAll(".ui-navlink[data-view]").forEach((a) => {
			a.addEventListener("click", (e) => {
				e.preventDefault();
				this.go(a.getAttribute("data-view"));
			});
		});

		const farm = this.root.querySelector("#ui-farm");
		const days = this.root.querySelector("#ui-days");
		const from = this.root.querySelector("#ui-from");
		const to = this.root.querySelector("#ui-to");

		farm.addEventListener("change", () => {
			this.filters.farm = farm.value;
			this.emitFilters();
		});

		days.addEventListener("change", () => {
			const d = parseInt(days.value, 10) || 30;
			this.filters.days = d;
			this.filters.from = daysAgoStr(d - 1);
			this.filters.to = todayStr();
			from.value = this.filters.from;
			to.value = this.filters.to;
			this.emitFilters();
		});

		const applyRange = () => {
			if (!from.value || !to.value) return;
			if (from.value > to.value) {
				/* Keep the pair ordered rather than silently querying backwards. */
				if (document.activeElement === from) to.value = from.value;
				else from.value = to.value;
			}
			this.filters.from = from.value;
			this.filters.to = to.value;
			const ms = new Date(`${to.value}T00:00:00`) - new Date(`${from.value}T00:00:00`);
			this.filters.days = Math.max(1, Math.round(ms / 86400000) + 1);
			this.emitFilters();
		};
		from.addEventListener("change", applyRange);
		to.addEventListener("change", applyRange);

		window.addEventListener("hashchange", () => this.go(this.hashView()));
	}

	syncFilterInputs() {
		const to = this.root.querySelector("#ui-to");
		const from = this.root.querySelector("#ui-from");
		const days = this.root.querySelector("#ui-days");
		to.max = todayStr();
		to.value = this.filters.to;
		from.value = this.filters.from;
		days.value = String(this.filters.days);
	}

	setFarms(farms) {
		const names = [...new Set((farms || []).map((f) => (typeof f === "string" ? f : f.name)).filter(Boolean))].sort();
		if (!names.length) return;
		const same = names.length === this.farms.length && names.every((n, i) => this.farms[i] === n);
		this.farms = names;
		const sel = this.root.querySelector("#ui-farm");
		if (!sel || same) return;
		const cur = this.filters.farm;
		sel.innerHTML =
			'<option value="">All farms</option>' +
			names.map((n) => `<option value="${charts.esc(n)}">${charts.esc(n)}</option>`).join("");
		if (cur && names.includes(cur)) sel.value = cur;
	}

	setCount(viewId, text) {
		const el = this.root.querySelector(`.ui-navlink .n[data-count="${viewId}"]`);
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
			.catch((err) => {
				console.error(`[irrigation] ${this.activeId} refresh failed`, err);
			});
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

		this.activeId = next;
		this.active = this.views.get(next);

		this.root.querySelectorAll(".ui-navlink[data-view]").forEach((a) => {
			a.classList.toggle("on", a.getAttribute("data-view") === next);
		});
		if (location.hash.replace(/^#/, "") !== next) {
			history.replaceState(null, "", `#${next}`);
		}

		this.el.main.innerHTML = '<div class="ui-loading">Loading…</div>';
		try {
			this.active.mount(this.el.main, this.ctx());
		} catch (e) {
			console.error(`[irrigation] ${next} mount failed`, e);
			this.el.main.innerHTML = `<div class="ui-card"><div class="ui-alert hot">${icon("alert")}<span>This view failed to load: ${charts.esc(e.message || e)}</span></div></div>`;
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

/* `title` and `eyebrow` are escaped text. `sub` and `toolbar` are markup the
 * view supplies, so they are inserted as-is — every caller passes a literal
 * template, never user data. Views that need to update the eyebrow after a
 * fetch target [data-eyebrow] rather than embedding an element in the string. */
export function pagehead(title, eyebrow, sub, toolbar = "") {
	return `<div class="ui-pagehead">
	<div>
		<div class="eyebrow" data-eyebrow>${charts.esc(eyebrow || "")}</div>
		<h1>${charts.esc(title)}</h1>
		${sub ? `<p>${sub}</p>` : ""}
	</div>
	${toolbar ? `<div class="ui-toolbar">${toolbar}</div>` : ""}
</div>`;
}

export function kpi(kc, label, value, unit, note, spark = "") {
	/* "—" and "" mean "not measured", which should read as absence rather than
	 * as a giant dash sitting where a number belongs. */
	const blank = value == null || value === "" || value === "—";
	const shown = blank ? "no data" : value;
	return `<div class="ui-kpi" style="--kc:${kc}">
	<div class="l">${charts.esc(label)}</div>
	<div class="v${blank ? " none" : ""}">${shown}${unit && !blank ? `<span class="unit">${charts.esc(unit)}</span>` : ""}</div>
	<div class="u">${charts.esc(note || "")}</div>
	${spark ? `<div class="spark">${spark}</div>` : ""}
</div>`;
}

export function statusStrip(el, message, kind = "hot") {
	if (!el) return;
	if (!message) {
		el.innerHTML = "";
		return;
	}
	const glyph = kind === "ok" ? "check" : "alert";
	el.innerHTML = `<div class="ui-alert ${kind}">${icon(glyph)}<span>${charts.esc(message)}</span></div>`;
}

export function emptyCard(message) {
	return `<div class="ui-card"><div class="ui-empty">${charts.esc(message)}</div></div>`;
}
