/* Charts and formatters.
 *
 * Weight and finish follow the Upande house style: 3px round-cap
 * strokes, a gradient wash under the first series, and 4px dots ringed in the
 * card colour so they read against the fill. Thin 1.8px lines with bare 2px
 * dots were the main reason the ported charts looked weaker than the original.
 *
 * Motion is opt-out: every animation is a CSS class defined in shell.css under
 * a prefers-reduced-motion guard, so this module only sets custom properties
 * and lets the stylesheet decide whether to move.
 */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/* Donut palette from the reference page's observation mix — gold first, then
 * plum, teal, olive, grey and red, which stay distinguishable in order. */
export const DONUT_COLORS = ["#d9a514", "#7d4a72", "#228883", "#7aa23f", "#8a8780", "#c4302b"];

export const SERIES_COLORS = [
	"#c25a2e",
	"#228883",
	"#3f8f4f",
	"#7c5cbf",
	"#c4302b",
	"#d9962e",
	"#3d7ea6",
	"#7c2f16",
];
export const SERIES_DASHES = ["", "5 3", "2 2", "8 3", "4 2 1 2"];

let uid = 0;
const nextId = () => `ui${(uid += 1)}`;

export function esc(s) {
	return String(s == null ? "" : s).replace(
		/[&<>"']/g,
		(c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
	);
}

export function fmtDate(d) {
	if (!d) return "";
	const x = new Date(String(d).replace(" ", "T"));
	if (isNaN(x)) return String(d).slice(5, 10);
	return `${x.getDate()} ${MONTHS[x.getMonth()]}`;
}

export function fmtNum(v, dec = 1) {
	if (v == null || isNaN(v)) return "—";
	const n = Number(v);
	return Math.abs(n) >= 1000 ? `${(n / 1000).toFixed(1)}k` : n.toFixed(dec);
}

export function timeAgo(ts) {
	if (!ts) return "—";
	const t = new Date(String(ts).replace(" ", "T"));
	if (isNaN(t)) return String(ts);
	const sec = Math.max(0, Math.floor((Date.now() - t.getTime()) / 1000));
	if (sec < 60) return `${sec}s ago`;
	if (sec < 3600) return `${Math.floor(sec / 60)} min ago`;
	if (sec < 86400) return `${Math.floor(sec / 3600)} hr ago`;
	return `${Math.floor(sec / 86400)}d ago`;
}

export function parseDt(s) {
	if (!s) return null;
	const d = new Date(String(s).replace(" ", "T"));
	return isNaN(d) ? null : d;
}

export function fmtClock(s) {
	const d = parseDt(s);
	return d ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—";
}

export function fmtDayClock(s) {
	const d = parseDt(s);
	return d ? d.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "—";
}

export function fmtCountdown(seconds) {
	let s = seconds == null || seconds < 0 ? 0 : Math.floor(seconds);
	const h = Math.floor(s / 3600);
	const m = Math.floor((s % 3600) / 60);
	s = s % 60;
	if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m ${String(s).padStart(2, "0")}s`;
	if (m > 0) return `${m}m ${String(s).padStart(2, "0")}s`;
	return `${s}s`;
}

export function prefersReducedMotion() {
	return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function niceMax(v) {
	if (v <= 0) return 1;
	const p = Math.pow(10, Math.floor(Math.log10(v)));
	const n = v / p;
	return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

function pickIdx(n, max = 8) {
	const step = Math.max(1, Math.ceil(n / max));
	const out = [];
	for (let i = 0; i < n; i += step) out.push(i);
	/* Always label the final point, but not when it would sit on top of the
	 * previous label — that produced overlaps like "29 J30 Jul". */
	if (out.length) {
		const last = out[out.length - 1];
		if (last !== n - 1) {
			if (n - 1 - last < step * 0.6) out[out.length - 1] = n - 1;
			else out.push(n - 1);
		}
	}
	return out;
}

/* One decimal count for the whole axis, chosen from its span, so ticks read as
 * a set: 0 · 3 · 5 · 8 · 10 rather than 0.0 · 2.5 · 5.0 · 7.5 · 10. */
function axisFormatter(span) {
	const dec = span >= 20 ? 0 : span >= 4 ? 1 : 2;
	return (v) => {
		if (Math.abs(v) < 1e-9) return "0";
		if (Math.abs(v) >= 1000) return `${(v / 1000).toFixed(1)}k`;
		/* Drop a trailing ".0" so a tick set reads 0 · 1.3 · 2.5 · 3.8 · 5
		 * rather than mixing "0" with "5.0". */
		return v.toFixed(dec).replace(/\.0+$/, "");
	};
}

/* Expand sparse rows onto one point per day between two dates.
 * Returns {labels, pick} where pick(field) yields an aligned array with null
 * for days that were never recorded. */
export function dailyAxis(rows, startISO, endISO, dateField = "date") {
	const start = new Date(`${String(startISO).slice(0, 10)}T00:00:00`);
	const end = new Date(`${String(endISO).slice(0, 10)}T00:00:00`);
	if (isNaN(start) || isNaN(end) || end < start) {
		const labels = rows.map((r) => r[dateField]);
		return { labels, pick: (f, fn) => rows.map((r) => (fn ? fn(r) : r[f])) };
	}
	const days = Math.min(3700, Math.round((end - start) / 86400000) + 1);
	const labels = [];
	const index = new Map();
	for (let i = 0; i < days; i++) {
		const d = new Date(start.getTime() + i * 86400000).toISOString().slice(0, 10);
		labels.push(d);
		index.set(d, i);
	}
	const slots = new Array(days).fill(null);
	rows.forEach((r) => {
		const key = String(r[dateField] || "").slice(0, 10);
		const i = index.get(key);
		if (i != null) slots[i] = r;
	});
	return {
		labels,
		pick: (field, fn) =>
			slots.map((r) => {
				if (!r) return null;
				const v = fn ? fn(r) : r[field];
				return v == null || isNaN(v) ? null : Number(v);
			}),
		rows: slots,
	};
}

/* ══════════════════════════════════════════════ tooltip */

let tipEl = null;

function tip() {
	if (!tipEl) {
		tipEl = document.createElement("div");
		tipEl.className = "tooltip";
		document.body.appendChild(tipEl);
	}
	return tipEl;
}

function showTip(html, x, y) {
	const el = tip();
	el.innerHTML = html;
	const w = 260;
	const left = x + w + 24 > window.innerWidth ? x - w - 14 : x + 14;
	el.style.left = `${Math.max(6, left)}px`;
	el.style.top = `${Math.min(window.innerHeight - 100, y + 14)}px`;
	el.classList.add("on");
}

export function hideTip() {
	if (tipEl) tipEl.classList.remove("on");
}

/* ══════════════════════════════════════════════ animation */

/* Give each drawn path its own length so the CSS keyframe can sweep it. */
function animatePaths(host) {
	if (prefersReducedMotion()) return;
	host.querySelectorAll("path.ui-draw").forEach((p) => {
		let len = 0;
		try {
			len = p.getTotalLength();
		} catch (e) {
			return;
		}
		if (!len) return;
		p.style.setProperty("--len", len.toFixed(1));
	});
}

/* Count a KPI number up to its value. Cheap, one rAF chain per element, and
 * skipped entirely under reduced motion. */
export function countUp(el, to, { dec = 1, dur = 650, prefix = "", suffix = "" } = {}) {
	if (!el) return;
	const target = Number(to);
	if (isNaN(target)) {
		el.textContent = `${prefix}—${suffix}`;
		return;
	}
	const render = (v) => {
		el.textContent = `${prefix}${Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(1)}k` : v.toFixed(dec)}${suffix}`;
	};
	if (prefersReducedMotion() || dur <= 0) {
		render(target);
		return;
	}
	const t0 = performance.now();
	const step = (now) => {
		const p = Math.min(1, (now - t0) / dur);
		/* easeOutCubic — fast then settles, so the final value is legible. */
		render(target * (1 - Math.pow(1 - p, 3)));
		if (p < 1) requestAnimationFrame(step);
		else render(target);
	};
	requestAnimationFrame(step);
}

/* ══════════════════════════════════════════════ line / bar chart */

/* series: [{label, color, values[], type:'bar'|'line', dash, width, unit, dec,
 *           noPoints, opacity}]
 * opts:   {xLabels[], xRaw, tooltip, yMin, yMax, margin, noFill, band} */
export function mkChart(host, series, W, H, opts = {}) {
	if (!host) return;
	const M = { t: 14, r: 16, b: 30, l: 44, ...(opts.margin || {}) };
	const iw = W - M.l - M.r;
	const ih = H - M.t - M.b;
	const allVals = series.flatMap((s) => s.values || []).filter((v) => v != null);
	const yMax = opts.yMax != null ? opts.yMax : niceMax(Math.max(...allVals, 0.001));
	const yMin = opts.yMin != null ? opts.yMin : Math.min(0, ...allVals);
	const ySpan = yMax - yMin || 1;
	const n = Math.max(...series.map((s) => (s.values || []).length), 0);
	if (!n) {
		host.innerHTML = '<div class="empty small">Nothing recorded in this period.</div>';
		return;
	}
	const step = n > 1 ? iw / (n - 1) : 0;
	const y = (v) => M.t + ih - ((v - yMin) / ySpan) * ih;
	const x = (i) => M.l + step * i;

	let defs = "";
	let body = "";

	/* Gridlines sit behind everything and stay hairline — the data carries the
	 * weight, not the chrome. */
	const fmtAxis = axisFormatter(ySpan);
	for (let i = 0; i <= 4; i++) {
		const v = yMin + (ySpan * i) / 4;
		const yy = y(v);
		body += `<line class="grid-line" x1="${M.l}" y1="${yy.toFixed(1)}" x2="${W - M.r}" y2="${yy.toFixed(1)}"/>`;
		body += `<text class="ax" x="${M.l - 7}" y="${(yy + 3.5).toFixed(1)}" text-anchor="end">${fmtAxis(v)}</text>`;
	}
	/* A zero rule, drawn darker, when the series crosses it (SWD does). */
	if (yMin < 0 && yMax > 0) {
		body += `<line class="zero-line" x1="${M.l}" y1="${y(0).toFixed(1)}" x2="${W - M.r}" y2="${y(0).toFixed(1)}"/>`;
	}
	body += `<line class="axis-line" x1="${M.l}" y1="${(M.t + ih).toFixed(1)}" x2="${W - M.r}" y2="${(M.t + ih).toFixed(1)}"/>`;

	/* Optional min/max envelope behind the lines (temperature). */
	if (opts.band && opts.band.lo && opts.band.hi) {
		const gid = nextId();
		defs += `<linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="${opts.band.color}" stop-opacity=".20"/><stop offset="100%" stop-color="${opts.band.color}" stop-opacity=".05"/></linearGradient>`;
		const top = opts.band.hi.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" L ");
		const bot = opts.band.lo
			.map((v, i) => ({ v, i }))
			.reverse()
			.map((p) => `${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`)
			.join(" L ");
		body += `<path d="M ${top} L ${bot} Z" fill="url(#${gid})"/>`;
	}

	series.forEach((s, si) => {
		const vals = s.values || [];
		if (s.type === "bar") {
			const bw = Math.max(2.5, step * 0.62);
			const base = y(Math.max(yMin, 0));
			vals.forEach((v, i) => {
				if (v == null) return;
				const top = y(v);
				const h = Math.max(1, Math.abs(base - top));
				body += `<rect class="ui-rise" style="--d:${(i * 12).toFixed(0)}ms" x="${(x(i) - bw / 2).toFixed(1)}" y="${Math.min(base, top).toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="2.5" fill="${s.color}" opacity="${s.opacity || 0.9}"/>`;
			});
			return;
		}
		/* Break the line at gaps instead of joining across them. A month with
		 * 11 readings must not draw as a continuous series — that invents data
		 * the station never recorded. */
		const runs = [];
		let run = [];
		vals.forEach((v, i) => {
			if (v == null) {
				if (run.length) runs.push(run);
				run = [];
				return;
			}
			run.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`);
		});
		if (run.length) runs.push(run);
		const drawable = runs.filter((r) => r.length >= 2);
		const isLead = si === 0 && !s.dash;

		/* Isolated readings still deserve a mark, or a series of single days
		 * would vanish entirely. */
		runs
			.filter((r) => r.length === 1)
			.forEach((r) => {
				const [cx, cy] = r[0].split(",");
				body += `<circle class="ui-pop" cx="${cx}" cy="${cy}" r="4" fill="${s.color}" stroke="var(--card)" stroke-width="2"/>`;
			});
		if (!drawable.length) return;

		const pathD = drawable.map((r) => `M ${r.join(" L ")}`).join(" ");
		if (isLead && !opts.noFill) {
			const gid = nextId();
			defs += `<linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="${s.color}" stop-opacity=".22"/><stop offset="100%" stop-color="${s.color}" stop-opacity="0"/></linearGradient>`;
			const floor = y(Math.max(yMin, 0));
			/* One wash per contiguous run, so gaps stay empty under the line. */
			const fillD = drawable
				.map((r) => {
					const first = r[0].split(",")[0];
					const last = r[r.length - 1].split(",")[0];
					return `M ${r.join(" L ")} L ${last},${floor.toFixed(1)} L ${first},${floor.toFixed(1)} Z`;
				})
				.join(" ");
			body += `<path class="ui-fade" d="${fillD}" fill="url(#${gid})"/>`;
		}
		const width = s.width || (isLead ? 3 : 2);
		body += `<path class="${s.dash ? "ui-fade" : "ui-draw"}" d="${pathD}" fill="none" stroke="${s.color}" stroke-width="${width}" stroke-linejoin="round" stroke-linecap="round"${s.dash ? ` stroke-dasharray="${s.dash}"` : ""}/>`;
		/* Ringed dots — they hold up over the gradient wash.
		 * Capped low: past ~20 points the dots crowd the line instead of
		 * marking readings. */
		if (!s.noPoints && n <= 20) {
			vals.forEach((v, i) => {
				if (v == null) return;
				body += `<circle class="ui-pop" style="--d:${(i * 14).toFixed(0)}ms" cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="4" fill="${s.color}" stroke="var(--card)" stroke-width="2"/>`;
			});
		}
	});

	const xLbls = opts.xLabels || [];
	const xText = (i) => (xLbls[i] == null ? "" : opts.xRaw ? String(xLbls[i]) : fmtDate(xLbls[i]));
	pickIdx(n, 8).forEach((i) => {
		const lbl = xLbls[i] != null ? xText(i) : i + 1;
		body += `<text class="ax" x="${x(i).toFixed(1)}" y="${H - 9}" text-anchor="middle">${esc(lbl)}</text>`;
	});

	if (opts.tooltip) {
		body += `<line class="ui-crosshair" x1="0" y1="${M.t}" x2="0" y2="${M.t + ih}" style="opacity:0"/>`;
		for (let i = 0; i < n; i++) {
			const xx = i === 0 ? M.l : x(i) - step / 2;
			const ww = i === 0 || i === n - 1 ? step / 2 : step;
			body += `<rect class="tooltip-hit" x="${xx.toFixed(1)}" y="${M.t}" width="${Math.max(1, ww).toFixed(1)}" height="${ih}" data-i="${i}"/>`;
		}
	}

	host.classList.add("chart");
	host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img">${defs ? `<defs>${defs}</defs>` : ""}${body}</svg>`;
	animatePaths(host);

	if (opts.tooltip) {
		const cross = host.querySelector(".ui-crosshair");
		host.querySelectorAll(".tooltip-hit").forEach((el) => {
			el.addEventListener("mousemove", (e) => {
				const i = +el.getAttribute("data-i");
				if (cross) {
					cross.setAttribute("x1", x(i).toFixed(1));
					cross.setAttribute("x2", x(i).toFixed(1));
					cross.style.opacity = "1";
				}
				const lines = series
					.map((s) => {
						const v = (s.values || [])[i];
						return `<span class="sw" style="background:${s.color}"></span><span class="k">${esc(s.label || "")}</span> ${v != null ? fmtNum(v, s.dec == null ? 1 : s.dec) : "—"} ${esc(s.unit || "")}`;
					})
					.join("<br>");
				showTip(`<span class="d">${esc(xText(i))}</span>${lines}`, e.clientX, e.clientY);
			});
			el.addEventListener("mouseleave", () => {
				if (cross) cross.style.opacity = "0";
				hideTip();
			});
		});
	}
}

/* ══════════════════════════════════════════════ donut */

/* entries: [[label, value], …]. Geometry matches the reference page:
 * r=80, 46px stroke on a 300 box, swept from 12 o'clock. */
export function donut(host, entries, { total, unit, colors } = {}) {
	if (!host) return;
	const list = (entries || []).filter(([, v]) => Number(v) > 0);
	if (!list.length) {
		host.innerHTML = '<div class="empty small">Nothing to split yet.</div>';
		return;
	}
	const palette = colors || DONUT_COLORS;
	const sum = list.reduce((s, [, v]) => s + Number(v), 0) || 1;
	const C = 502.65;
	let offset = 0;
	const arcs = list
		.map(([, v], i) => {
			const len = (Number(v) / sum) * C;
			const seg = `<circle class="ui-arc" style="--d:${i * 90}ms" r="80" fill="none" stroke="${palette[i % palette.length]}" stroke-width="46" stroke-dasharray="${len.toFixed(1)} ${C}" stroke-dashoffset="${(-offset).toFixed(1)}" transform="rotate(-90)"/>`;
			offset += len;
			return seg;
		})
		.join("");
	const legend = list
		.map(
			([k, v], i) =>
				`<span><i style="background:${palette[i % palette.length]}"></i>${esc(k)} · ${Math.round((Number(v) / sum) * 100)}%</span>`
		)
		.join("");
	const centre = total == null ? fmtNum(sum, 0) : total;
	host.innerHTML = `
<div class="piewrap">
	<svg viewBox="0 0 300 300"><g transform="translate(150 150)">${arcs}</g></svg>
	<div class="piewrap__center"><b>${esc(String(centre))}</b><small>${esc(unit || "")}</small></div>
</div>
<div class="clegend">${legend}</div>`;
}

/* ══════════════════════════════════════════════ health bars */

/* rows: [{label, pct, value, tone}] — tone overrides the pct→colour rule. */
export function healthBars(host, rows) {
	if (!host) return;
	if (!rows || !rows.length) {
		host.innerHTML = '<div class="empty small">No sections to compare yet.</div>';
		return;
	}
	host.innerHTML = rows
		.map((r, i) => {
			const pct = Math.max(0, Math.min(100, Number(r.pct) || 0));
			/* high = meeting target, low = badly short — matches the reference. */
			const tone = r.tone || (pct >= 90 ? "high" : pct >= 70 ? "mid" : "low");
			return `<div class="hb">
	<div class="hb__name" title="${esc(r.label)}">${esc(r.label)}</div>
	<div class="hb__lane">
		<div class="hb__est" style="width:100%"></div>
		<div class="hb__act ${tone} ui-grow" style="--w:${pct}%;--d:${i * 60}ms"></div>
		${r.tick != null ? `<div class="hb__tick" style="left:${Math.max(0, Math.min(100, Number(r.tick)))}%" title="target"></div>` : ""}
	</div>
	<div class="hb__pct">${esc(r.value != null ? r.value : `${Math.round(pct)}%`)}</div>
</div>`;
		})
		.join("");
}

/* ══════════════════════════════════════════════ sparkline */

export function sparkline(values, color, height) {
	const W = 120;
	const H = height || 22;
	const P = 1.5;
	const vals = (values || []).filter((v) => v != null && !isNaN(v));
	if (!vals.length) return "";
	const max = Math.max(...vals, 0.001);
	const min = Math.min(...vals, 0);
	const span = max - min || 1;
	const step = vals.length > 1 ? (W - 2 * P) / (vals.length - 1) : 0;
	const pts = vals.map((v, i) => [P + i * step, H - P - ((v - min) / span) * (H - 2 * P)]);
	const path = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
	const area = `${path} L ${pts[pts.length - 1][0].toFixed(1)},${H} L ${pts[0][0].toFixed(1)},${H} Z`;
	return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><path d="${area}" fill="${color}" opacity=".16"/><path d="${path}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}

/* ══════════════════════════════════════════════ arc gauge (irrometer) */

export function arcGauge(val, color, size = 46) {
	const r = 17;
	const cx = size / 2;
	const cy = size * 0.74;
	const h = Math.round(size * 0.62);
	const pct = val != null ? Math.min(1, Math.max(0, val / 80)) : 0;
	const angle = -Math.PI + Math.PI * pct;
	const x1 = cx + r * Math.cos(-Math.PI);
	const y1 = cy + r * Math.sin(-Math.PI);
	const x2 = cx + r * Math.cos(angle);
	const y2 = cy + r * Math.sin(angle);
	const largeArc = pct > 0.5 ? 1 : 0;
	const track = `<path d="M ${x1.toFixed(1)},${y1.toFixed(1)} A ${r} ${r} 0 1 1 ${(cx + r).toFixed(1)} ${cy.toFixed(1)}" fill="none" stroke="rgba(10,10,10,0.08)" stroke-width="5" stroke-linecap="round"/>`;
	const fill =
		val != null
			? `<path class="ui-draw" d="M ${x1.toFixed(1)},${y1.toFixed(1)} A ${r} ${r} 0 ${largeArc} 1 ${x2.toFixed(1)},${y2.toFixed(1)}" fill="none" stroke="${color}" stroke-width="5" stroke-linecap="round"/>`
			: "";
	return `<svg class="gauge" width="${size}" height="${h}" viewBox="0 0 ${size} ${h}">${track}${fill}</svg>`;
}

/* Run after inserting gauges so their arcs sweep in. */
export function animateIn(host) {
	if (host) animatePaths(host);
}

/* ══════════════════════════════════════════════ multi-series line */

export function mkMultiLine(host, labels, datasets, W, H, opts = {}) {
	if (!host) return;
	const series = datasets.map((d, i) => ({
		label: d.label,
		color: d.color || SERIES_COLORS[i % SERIES_COLORS.length],
		values: d.values,
		dash: d.dash != null ? d.dash : SERIES_DASHES[i % SERIES_DASHES.length],
		width: i === 0 ? 3 : 2.2,
		noPoints: labels.length > 26,
		unit: opts.unit || "",
		dec: opts.dec == null ? 1 : opts.dec,
	}));
	mkChart(host, series, W, H, {
		xLabels: labels,
		xRaw: true,
		tooltip: true,
		noFill: true,
		margin: { t: 14, r: 18, b: 32, l: 48 },
	});
}

/* ══════════════════════════════════════════════ fleet time series
 *
 * A tab of identical sensors is 28 lines, which reads as spaghetti and hides
 * the thing an operator wants: is the fleet normal, and is this device an
 * outlier? So the fleet is drawn as a p10–p90 envelope with the median through
 * it, and only the devices explicitly picked are overlaid as accent lines.
 */

function quantile(sorted, q) {
	if (!sorted.length) return null;
	const pos = (sorted.length - 1) * q;
	const lo = Math.floor(pos);
	const hi = Math.ceil(pos);
	if (lo === hi) return sorted[lo];
	return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

/* Per time-slot spread across every device in the tab. */
export function fleetStats(devices) {
	const n = Math.max(0, ...devices.map((d) => (d.series || []).length));
	const lo = [];
	const mid = [];
	const hi = [];
	const count = [];
	for (let i = 0; i < n; i++) {
		const col = devices
			.map((d) => (d.series || [])[i])
			.filter((v) => v != null && !isNaN(v))
			.sort((a, b) => a - b);
		count.push(col.length);
		if (!col.length) {
			lo.push(null);
			mid.push(null);
			hi.push(null);
			continue;
		}
		lo.push(+quantile(col, 0.1).toFixed(3));
		mid.push(+quantile(col, 0.5).toFixed(3));
		hi.push(+quantile(col, 0.9).toFixed(3));
	}
	return { lo, mid, hi, count, points: n };
}

/* Adaptive time labels: a multi-week window wants dates, a single day wants
 * clock times, and a couple of days wants both. */
function timeTicks(labels, maxTicks = 7) {
	const dates = labels.map((l) => new Date(String(l).replace(" ", "T")));
	const valid = dates.filter((d) => !isNaN(d));
	if (!valid.length) return { fmt: (i) => String(labels[i] || ""), idx: [] };
	const spanHours = (valid[valid.length - 1] - valid[0]) / 3600000;
	const fmt = (i) => {
		const d = dates[i];
		if (!d || isNaN(d)) return "";
		if (spanHours <= 30) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
		if (spanHours <= 24 * 6) {
			return `${d.getDate()} ${MONTHS[d.getMonth()]} ${String(d.getHours()).padStart(2, "0")}:00`;
		}
		return `${d.getDate()} ${MONTHS[d.getMonth()]}`;
	};
	const step = Math.max(1, Math.ceil(labels.length / maxTicks));
	const idx = [];
	for (let i = 0; i < labels.length; i += step) idx.push(i);
	if (idx.length && idx[idx.length - 1] !== labels.length - 1) {
		if (labels.length - 1 - idx[idx.length - 1] < step * 0.6) idx[idx.length - 1] = labels.length - 1;
		else idx.push(labels.length - 1);
	}
	return { fmt, idx };
}

/* opts: {labels[], stats:{lo,mid,hi}, picks:[{label,color,values}], unit,
 *        bandColor, medianColor, W, H} */
export function mkFleetChart(host, opts) {
	if (!host) return;
	const labels = opts.labels || [];
	const stats = opts.stats || { lo: [], mid: [], hi: [] };
	const picks = opts.picks || [];
	const W = opts.W || 1180;
	const H = opts.H || 340;
	const M = { t: 18, r: 18, b: 34, l: 52 };
	const iw = W - M.l - M.r;
	const ih = H - M.t - M.b;
	const n = labels.length;

	if (!n) {
		host.innerHTML = '<div class="empty small">No readings in this window.</div>';
		return;
	}

	const all = []
		.concat(stats.lo, stats.hi, stats.mid, ...picks.map((p) => p.values || []))
		.filter((v) => v != null && !isNaN(v));
	if (!all.length) {
		host.innerHTML = '<div class="empty small">No readings in this window.</div>';
		return;
	}
	let yMin = Math.min(...all);
	let yMax = Math.max(...all);
	const pad = (yMax - yMin || 1) * 0.12;
	yMin -= pad;
	yMax += pad;
	const span = yMax - yMin || 1;

	const x = (i) => M.l + (n > 1 ? (iw * i) / (n - 1) : iw / 2);
	const y = (v) => M.t + ih - ((v - yMin) / span) * ih;

	const bandColor = opts.bandColor || "var(--trap-500)";
	const medianColor = opts.medianColor || "var(--ink)";
	const gid = nextId();
	let defs = `<linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="${bandColor}" stop-opacity=".40"/><stop offset="100%" stop-color="${bandColor}" stop-opacity=".12"/></linearGradient>`;
	let body = "";

	/* Gridlines and axis */
	const fmtAxis = axisFormatter(span);
	for (let i = 0; i <= 4; i++) {
		const v = yMin + (span * i) / 4;
		const yy = y(v);
		body += `<line class="grid-line" x1="${M.l}" y1="${yy.toFixed(1)}" x2="${W - M.r}" y2="${yy.toFixed(1)}"/>`;
		body += `<text class="ax" x="${M.l - 8}" y="${(yy + 3.5).toFixed(1)}" text-anchor="end">${fmtAxis(v)}</text>`;
	}
	body += `<line class="axis-line" x1="${M.l}" y1="${(M.t + ih).toFixed(1)}" x2="${W - M.r}" y2="${(M.t + ih).toFixed(1)}"/>`;
	if (opts.unit) {
		body += `<text class="ax" x="${M.l - 8}" y="${M.t - 6}" text-anchor="end">${esc(opts.unit)}</text>`;
	}

	/* Envelope, split at gaps so a silent stretch stays empty. */
	const runs = [];
	let run = [];
	for (let i = 0; i < n; i++) {
		if (stats.lo[i] == null || stats.hi[i] == null) {
			if (run.length > 1) runs.push(run);
			run = [];
			continue;
		}
		run.push(i);
	}
	if (run.length > 1) runs.push(run);

	runs.forEach((r) => {
		const top = r.map((i) => `${x(i).toFixed(1)},${y(stats.hi[i]).toFixed(1)}`).join(" L ");
		const bot = r
			.slice()
			.reverse()
			.map((i) => `${x(i).toFixed(1)},${y(stats.lo[i]).toFixed(1)}`)
			.join(" L ");
		body += `<path class="ui-fade" d="M ${top} L ${bot} Z" fill="url(#${gid})"/>`;
	});

	/* Median through the band. */
	const medRuns = [];
	run = [];
	for (let i = 0; i < n; i++) {
		if (stats.mid[i] == null) {
			if (run.length > 1) medRuns.push(run);
			run = [];
			continue;
		}
		run.push(`${x(i).toFixed(1)},${y(stats.mid[i]).toFixed(1)}`);
	}
	if (run.length > 1) medRuns.push(run);
	if (medRuns.length) {
		body += `<path class="ui-draw" d="${medRuns.map((r) => `M ${r.join(" L ")}`).join(" ")}" fill="none" stroke="${medianColor}" stroke-width="2.6" stroke-linejoin="round" stroke-linecap="round"/>`;
	}

	/* Picked devices on top. */
	picks.forEach((p, pi) => {
		const vals = p.values || [];
		const segs = [];
		let seg = [];
		for (let i = 0; i < n; i++) {
			if (vals[i] == null) {
				if (seg.length > 1) segs.push(seg);
				seg = [];
				continue;
			}
			seg.push(`${x(i).toFixed(1)},${y(vals[i]).toFixed(1)}`);
		}
		if (seg.length > 1) segs.push(seg);
		if (!segs.length) return;
		body += `<path class="ui-draw" style="animation-delay:${120 + pi * 90}ms" d="${segs.map((sg) => `M ${sg.join(" L ")}`).join(" ")}" fill="none" stroke="${p.color}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>`;
		/* Mark the newest reading so the eye lands on "now". */
		for (let i = n - 1; i >= 0; i--) {
			if (vals[i] != null) {
				body += `<circle class="ui-pop" cx="${x(i).toFixed(1)}" cy="${y(vals[i]).toFixed(1)}" r="4.5" fill="${p.color}" stroke="var(--surface-2)" stroke-width="2"/>`;
				break;
			}
		}
	});

	/* Time axis */
	const ticks = timeTicks(labels);
	ticks.idx.forEach((i) => {
		body += `<text class="ax" x="${x(i).toFixed(1)}" y="${H - 10}" text-anchor="middle">${esc(ticks.fmt(i))}</text>`;
	});

	/* Hover */
	body += `<line class="ui-crosshair" x1="0" y1="${M.t}" x2="0" y2="${M.t + ih}" style="opacity:0"/>`;
	const step = n > 1 ? iw / (n - 1) : iw;
	for (let i = 0; i < n; i++) {
		const xx = i === 0 ? M.l : x(i) - step / 2;
		const ww = i === 0 || i === n - 1 ? step / 2 : step;
		body += `<rect class="tooltip-hit" x="${xx.toFixed(1)}" y="${M.t}" width="${Math.max(1, ww).toFixed(1)}" height="${ih}" data-i="${i}"/>`;
	}

	host.classList.add("chart");
	host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img"><defs>${defs}</defs>${body}</svg>`;
	animatePaths(host);

	const cross = host.querySelector(".ui-crosshair");
	host.querySelectorAll(".tooltip-hit").forEach((el) => {
		el.addEventListener("mousemove", (e) => {
			const i = +el.getAttribute("data-i");
			if (cross) {
				cross.setAttribute("x1", x(i).toFixed(1));
				cross.setAttribute("x2", x(i).toFixed(1));
				cross.style.opacity = "1";
			}
			const u = opts.unit ? ` ${opts.unit}` : "";
			const rows = [];
			if (stats.mid[i] != null) {
				rows.push(
					`<span class="sw" style="background:${medianColor}"></span><span class="k">Fleet median</span> ${fmtNum(stats.mid[i])}${u}`
				);
				rows.push(
					`<span class="sw" style="background:${bandColor};opacity:.5"></span><span class="k">p10–p90</span> ${fmtNum(stats.lo[i])} – ${fmtNum(stats.hi[i])}${u}`
				);
				if (stats.count) rows.push(`<span class="k">Reporting</span> ${stats.count[i]}`);
			}
			picks.forEach((p) => {
				const v = (p.values || [])[i];
				rows.push(
					`<span class="sw" style="background:${p.color}"></span><span class="k">${esc(p.label)}</span> ${v != null ? `${fmtNum(v)}${u}` : "—"}`
				);
			});
			showTip(`<span class="d">${esc(ticks.fmt(i))}</span>${rows.join("<br>")}`, e.clientX, e.clientY);
		});
		el.addEventListener("mouseleave", () => {
			if (cross) cross.style.opacity = "0";
			hideTip();
		});
	});
}
