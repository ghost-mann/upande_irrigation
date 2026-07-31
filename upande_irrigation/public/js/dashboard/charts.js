/* Hand-rolled inline-SVG charts.
 *
 * Ported from meniscus.html (mkChart / sparkline / arcGauge) with the palette
 * moved onto house tokens and the tooltip host owned here instead of being a
 * page-level global. No charting library: these render server-shaped arrays
 * straight to markup, which keeps the dashboard dependency-free.
 */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

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
	return d
		? d.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })
		: "—";
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
	if (out.length && out[out.length - 1] !== n - 1) out.push(n - 1);
	return out;
}

/* ══════════════════════════════════════════════ tooltip */

let tipEl = null;

function tip() {
	if (!tipEl) {
		tipEl = document.createElement("div");
		tipEl.className = "ui-tooltip";
		document.body.appendChild(tipEl);
	}
	return tipEl;
}

function showTip(html, x, y) {
	const el = tip();
	el.innerHTML = html;
	/* Flip before the viewport edge so long tooltips stay readable. */
	const w = 260;
	const left = x + w + 24 > window.innerWidth ? x - w - 14 : x + 14;
	el.style.left = `${Math.max(6, left)}px`;
	el.style.top = `${Math.min(window.innerHeight - 90, y + 14)}px`;
	el.classList.add("on");
}

export function hideTip() {
	if (tipEl) tipEl.classList.remove("on");
}

/* ══════════════════════════════════════════════ line / bar chart */

/* series: [{label, color, values[], type:'bar'|'line', dash, width, unit, dec,
 *           noPoints, opacity}]
 * opts:   {xLabels[], tooltip, yMin, yMax, margin} */
export function mkChart(host, series, W, H, opts = {}) {
	if (!host) return;
	const M = { t: 12, r: 14, b: 28, l: 40, ...(opts.margin || {}) };
	const iw = W - M.l - M.r;
	const ih = H - M.t - M.b;
	const allVals = series.flatMap((s) => s.values || []).filter((v) => v != null);
	const yMax = opts.yMax != null ? opts.yMax : niceMax(Math.max(...allVals, 0.001));
	const yMin = opts.yMin != null ? opts.yMin : 0;
	const ySpan = yMax - yMin || 1;
	const n = Math.max(...series.map((s) => (s.values || []).length), 0);
	if (!n) {
		host.innerHTML = '<div class="ui-empty small">no data</div>';
		return;
	}
	const step = n > 1 ? iw / (n - 1) : 0;
	const y = (v) => M.t + ih - ((v - yMin) / ySpan) * ih;
	const x = (i) => M.l + step * i;

	let defs = "";
	let body = "";

	for (let i = 0; i <= 4; i++) {
		const v = yMin + (ySpan * i) / 4;
		const yy = y(v);
		body += `<line class="grid-line" x1="${M.l}" y1="${yy.toFixed(1)}" x2="${W - M.r}" y2="${yy.toFixed(1)}"/>`;
		const lbl = Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(1)}k` : Math.abs(v) >= 10 ? v.toFixed(0) : v.toFixed(1);
		body += `<text x="${M.l - 5}" y="${(yy + 3).toFixed(1)}" text-anchor="end">${lbl}</text>`;
	}
	body += `<line class="axis-line" x1="${M.l}" y1="${(M.t + ih).toFixed(1)}" x2="${W - M.r}" y2="${(M.t + ih).toFixed(1)}"/>`;

	series.forEach((s, si) => {
		const vals = s.values || [];
		if (s.type === "bar") {
			const bw = Math.max(2, step * 0.7);
			vals.forEach((v, i) => {
				if (v == null) return;
				const bh = Math.max(0, ((v - yMin) / ySpan) * ih);
				body += `<rect x="${(x(i) - bw / 2).toFixed(1)}" y="${y(v).toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" rx="2" fill="${s.color}" opacity="${s.opacity || 0.85}"/>`;
			});
			return;
		}
		const pts = vals
			.map((v, i) => (v != null ? `${x(i).toFixed(1)},${y(v).toFixed(1)}` : null))
			.filter(Boolean);
		if (pts.length < 2) return;
		const pathD = `M ${pts.join(" L ")}`;
		if (si === 0 && !s.dash && !opts.noFill) {
			const gid = `g${si}${Math.abs(W * H)}${(s.label || "").replace(/\W/g, "")}`;
			defs += `<linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="${s.color}" stop-opacity=".18"/><stop offset="100%" stop-color="${s.color}" stop-opacity="0"/></linearGradient>`;
			body += `<path d="${pathD} L ${x(vals.length - 1).toFixed(1)},${(M.t + ih).toFixed(1)} L ${x(0).toFixed(1)},${(M.t + ih).toFixed(1)} Z" fill="url(#${gid})"/>`;
		}
		body += `<path d="${pathD}" fill="none" stroke="${s.color}" stroke-width="${s.width || 1.8}" stroke-linejoin="round"${s.dash ? ` stroke-dasharray="${s.dash}"` : ""}/>`;
		if (!s.noPoints && n <= 60) {
			vals.forEach((v, i) => {
				if (v != null) body += `<circle cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="2" fill="${s.color}"/>`;
			});
		}
	});

	const xLbls = opts.xLabels || [];
	/* Category labels ("Jan", "W12") must not go through fmtDate. */
	const xText = (i) => (xLbls[i] == null ? "" : opts.xRaw ? String(xLbls[i]) : fmtDate(xLbls[i]));
	pickIdx(n, 8).forEach((i) => {
		const lbl = xLbls[i] != null ? xText(i) : i + 1;
		body += `<text x="${x(i).toFixed(1)}" y="${H - 7}" text-anchor="middle">${esc(lbl)}</text>`;
	});

	if (opts.tooltip) {
		for (let i = 0; i < n; i++) {
			const xx = i === 0 ? M.l : x(i) - step / 2;
			const ww = i === 0 || i === n - 1 ? step / 2 : step;
			body += `<rect class="tooltip-hit" x="${xx.toFixed(1)}" y="${M.t}" width="${Math.max(1, ww).toFixed(1)}" height="${ih}" data-i="${i}"/>`;
		}
	}

	host.classList.add("ui-chart");
	host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">${defs ? `<defs>${defs}</defs>` : ""}${body}</svg>`;

	if (opts.tooltip) {
		host.querySelectorAll(".tooltip-hit").forEach((el) => {
			el.addEventListener("mousemove", (e) => {
				const i = +el.getAttribute("data-i");
				const d = xText(i);
				const lines = series
					.map((s) => {
						const v = (s.values || [])[i];
						return `<span class="k">${esc(s.label || "")}</span> ${v != null ? fmtNum(v, s.dec == null ? 1 : s.dec) : "—"} ${esc(s.unit || "")}`;
					})
					.join("<br>");
				showTip(`<span class="d">${esc(d)}</span>${lines}`, e.clientX, e.clientY);
			});
			el.addEventListener("mouseleave", hideTip);
		});
	}
}

/* ══════════════════════════════════════════════ sparkline */

export function sparkline(values, color, height) {
	const W = 120;
	const H = height || 22;
	const P = 1;
	const vals = (values || []).filter((v) => v != null && !isNaN(v));
	if (!vals.length) return "";
	const max = Math.max(...vals, 0.001);
	const min = Math.min(...vals, 0);
	const span = max - min || 1;
	const step = vals.length > 1 ? (W - 2 * P) / (vals.length - 1) : 0;
	const pts = vals.map((v, i) => [P + i * step, H - P - ((v - min) / span) * (H - 2 * P)]);
	const path = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
	const area = `${path} L ${pts[pts.length - 1][0].toFixed(1)},${H} L ${pts[0][0].toFixed(1)},${H} Z`;
	return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><path d="${area}" fill="${color}" opacity=".15"/><path d="${path}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round"/></svg>`;
}

/* ══════════════════════════════════════════════ arc gauge (irrometer) */

/* Soil tension in centibars, 0–80 mapped to a half arc. */
export function arcGauge(val, color, size = 44) {
	const r = 16;
	const cx = size / 2;
	const cy = size * 0.72;
	const h = Math.round(size * 0.6);
	const pct = val != null ? Math.min(1, Math.max(0, val / 80)) : 0;
	const angle = -Math.PI + Math.PI * pct;
	const x1 = cx + r * Math.cos(-Math.PI);
	const y1 = cy + r * Math.sin(-Math.PI);
	const x2 = cx + r * Math.cos(angle);
	const y2 = cy + r * Math.sin(angle);
	const largeArc = pct > 0.5 ? 1 : 0;
	const track = `<path d="M ${x1.toFixed(1)},${y1.toFixed(1)} A ${r} ${r} 0 1 1 ${(cx + r).toFixed(1)} ${cy.toFixed(1)}" fill="none" stroke="rgba(10,10,10,0.08)" stroke-width="4" stroke-linecap="round"/>`;
	const fill =
		val != null
			? `<path d="M ${x1.toFixed(1)},${y1.toFixed(1)} A ${r} ${r} 0 ${largeArc} 1 ${x2.toFixed(1)},${y2.toFixed(1)}" fill="none" stroke="${color}" stroke-width="4" stroke-linecap="round"/>`
			: "";
	return `<svg width="${size}" height="${h}" viewBox="0 0 ${size} ${h}">${track}${fill}</svg>`;
}

/* ══════════════════════════════════════════════ multi-series line (compare) */

/* Year-on-year overlay. Distinct dash patterns as well as hues so the series
 * stay separable when printed or viewed by a colour-blind operator. */
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

export function mkMultiLine(host, labels, datasets, W, H, opts = {}) {
	if (!host) return;
	const series = datasets.map((d, i) => ({
		label: d.label,
		color: d.color || SERIES_COLORS[i % SERIES_COLORS.length],
		values: d.values,
		dash: d.dash != null ? d.dash : SERIES_DASHES[i % SERIES_DASHES.length],
		width: 2,
		noPoints: labels.length > 26,
		unit: opts.unit || "",
		dec: opts.dec == null ? 1 : opts.dec,
	}));
	mkChart(host, series, W, H, {
		xLabels: labels,
		xRaw: true,
		tooltip: true,
		noFill: true,
		margin: { t: 12, r: 16, b: 30, l: 46 },
	});
}
