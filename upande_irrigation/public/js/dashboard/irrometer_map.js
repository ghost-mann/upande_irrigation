/* Irrometer map — the Weather view's soil-tension section drawn on the farm.
 *
 * Modelled on the v15 "Aerial Farm Layout" web page: Esri imagery, block
 * outlines from Warehouse.custom_raw_geojson (via api.valves.blocks_geojson),
 * search, and a card for the clicked block. Each block is shaded by its latest
 * 1 ft irrometer reading in the same tension bands as the gauges below it;
 * blocks that carry readings get a marker at their centre showing the value.
 * Clicking a block or its marker opens that block's readings and history.
 *
 * Irrometers are per block today: an Irrometer Reading names a block, not a
 * device position. Exact device pins come later, once devices have GPS points.
 */

import { basemapStyle, basemapToggle, bounds, loadMapLibre, MAP_MAX_ZOOM, setBasemap, whenLoaded } from "./maplib.js";

/* MapLibre paint needs real colours, not CSS variables: the tension bands'. */
const BAND = {
	ok: "#3f8f4f",
	warn: "#d9962e",
	clay: "#c25a2e",
	hot: "#c4302b",
	ink: "#8a8780",
};

let seq = 0;

export function irrometerMap(host, ctx, { tension, shortSection }) {
	const { api, charts } = ctx;
	const id = `irm-${++seq}`;
	const state = { map: null, farm: undefined, features: [], readings: new Map(), markers: [], gen: 0, basemap: "hybrid" };

	host.innerHTML = `
<div class="irm">
	<div class="irm__bar">
		<input class="input irm__search" id="${id}-search" type="search" placeholder="Find a block…" list="${id}-blocks" aria-label="Find a block">
		<datalist id="${id}-blocks"></datalist>
		<span class="meta" id="${id}-count"></span>
		${basemapToggle(`${id}-base`, state.basemap)}
	</div>
	<div class="irm__wrap">
		<div class="fieldmap irm__map" id="${id}-map"><div class="fieldmap__overlay" id="${id}-overlay">Loading map…</div></div>
		<aside class="irm__card" id="${id}-card" hidden></aside>
	</div>
</div>`;
	const $ = (sel) => host.querySelector(sel);

	host.querySelectorAll(`#${id}-base button`).forEach((b) => {
		b.addEventListener("click", () => {
			state.basemap = b.getAttribute("data-base");
			host.querySelectorAll(`#${id}-base button`).forEach((x) => x.classList.toggle("on", x === b));
			setBasemap(state.map, state.basemap);
		});
	});
	$(`#${id}-search`).addEventListener("change", (e) => {
		const q = String(e.target.value || "").trim().toLowerCase();
		const f = state.features.find(
			(x) => x.properties.block_label.toLowerCase() === q || x.properties.block.toLowerCase() === q
		);
		if (f) focus(f.properties.block, true);
	});

	function band(block) {
		const r = state.readings.get(block);
		return tension(r ? r.irrometer_1ft : null).cls;
	}

	function paint() {
		state.features.forEach((f) => {
			f.properties.color = BAND[band(f.properties.block)] || BAND.ink;
		});
		const src = state.map && state.map.getSource("irm-blocks");
		if (src) src.setData({ type: "FeatureCollection", features: state.features });

		state.markers.forEach((m) => m.remove());
		state.markers = [];
		if (!state.map) return;
		const maplibregl = window.maplibregl;
		state.features.forEach((f) => {
			const r = state.readings.get(f.properties.block);
			if (!r || r.irrometer_1ft == null) return;
			const b = bounds([f]);
			const el = document.createElement("button");
			el.type = "button";
			el.className = `irm-pin ${tension(r.irrometer_1ft).cls}`;
			el.title = `${f.properties.block_label}: ${r.irrometer_1ft} cb at 1 ft`;
			el.textContent = r.irrometer_1ft;
			el.addEventListener("click", (e) => {
				e.stopPropagation();
				focus(f.properties.block, false);
			});
			state.markers.push(
				new maplibregl.Marker({ element: el }).setLngLat([(b.minX + b.maxX) / 2, (b.minY + b.maxY) / 2]).addTo(state.map)
			);
		});
		const read = state.features.filter((f) => state.readings.get(f.properties.block)?.irrometer_1ft != null).length;
		$(`#${id}-count`).textContent = `${state.features.length} blocks · ${read} with readings`;
	}

	function card(block) {
		const el = $(`#${id}-card`);
		const f = state.features.find((x) => x.properties.block === block);
		if (!f) {
			el.hidden = true;
			return;
		}
		const p = f.properties;
		const r = state.readings.get(block);
		const hist = ((r && r.irrometer_history) || []).filter((h) => h.ft1 != null || h.ft2 != null);
		const t1 = tension(r ? r.irrometer_1ft : null);
		const t2 = tension(r ? r.irrometer_2ft : null);
		const reading = (label, v, t) =>
			`<div class="irm__reading"><small>${label}</small><b style="color:${t.color}">${v == null ? "—" : v}</b><span class="sev ${t.cls}">${charts.esc(t.label)}</span></div>`;
		el.innerHTML = `
<button class="irm__close" type="button" aria-label="Close">&times;</button>
<div class="irm__title">${charts.esc(p.block_label)}</div>
<div class="irm__meta">${charts.esc(shortSection(p.section) || p.section || "—")}${p.farm ? ` · ${charts.esc(p.farm)}` : ""}</div>
${
	r && r.irrometer_1ft != null
		? `<div class="irm__readings">${reading("1 ft", r.irrometer_1ft, t1)}${reading("2 ft", r.irrometer_2ft, t2)}</div>
<div class="irm__meta">Read ${charts.esc(charts.fmtDate(r.irrometer_date))} · centibars</div>
${
	hist.length > 1
		? `<div class="irm__spark"><small>1 ft history</small>${charts.sparkline(hist.map((h) => h.ft1), BAND[t1.cls] || BAND.ink, 34)}</div>
<table class="table irm__table"><thead><tr><th>Date</th><th class="num">1 ft</th><th class="num">2 ft</th></tr></thead><tbody>
${hist
	.slice(-8)
	.reverse()
	.map((h) => `<tr><td>${charts.esc(charts.fmtDate(h.date))}</td><td class="num">${h.ft1 == null ? "—" : h.ft1}</td><td class="num">${h.ft2 == null ? "—" : h.ft2}</td></tr>`)
	.join("")}
</tbody></table>`
		: ""
}`
		: `<div class="irm__empty">No irrometer readings for this block yet. They are entered in the Irrometer table on the Weather Reading form.</div>`
}`;
		el.hidden = false;
		el.querySelector(".irm__close").addEventListener("click", () => {
			el.hidden = true;
		});
	}

	function focus(block, fly) {
		const f = state.features.find((x) => x.properties.block === block);
		if (!f || !state.map) return;
		if (fly) {
			const b = bounds([f]);
			state.map.fitBounds(
				[
					[b.minX, b.minY],
					[b.maxX, b.maxY],
				],
				{ padding: 120, maxZoom: 17, duration: 700 }
			);
		}
		card(block);
	}

	async function build(farm) {
		const gen = ++state.gen;
		const overlay = () => $(`#${id}-overlay`);
		let maplibregl;
		let fc;
		try {
			[maplibregl, fc] = await Promise.all([
				loadMapLibre(),
				api.get("upande_irrigation.api.valves.blocks_geojson", { farm }).then(({ data }) => data || {}),
			]);
		} catch (err) {
			if (gen === state.gen && overlay()) overlay().textContent = `Map unavailable: ${err.message}`;
			return;
		}
		if (gen !== state.gen) return;

		state.features = ((fc && fc.features) || []).map((f) => ({ ...f, properties: { ...f.properties } }));
		$(`#${id}-blocks`).innerHTML = state.features
			.map((f) => `<option value="${charts.esc(f.properties.block_label)}"></option>`)
			.join("");
		if (!state.features.length) {
			const why = fc && fc.meta && fc.meta.reason ? ` (${fc.meta.reason})` : "";
			if (overlay()) overlay().textContent = `No block boundaries to draw${why}. Block outlines come from the Warehouse's Raw GeoJSON.`;
			return;
		}

		if (state.map) state.map.remove();
		const b = bounds(state.features);
		const map = new maplibregl.Map({
			container: `${id}-map`,
			style: basemapStyle(),
			center: [(b.minX + b.maxX) / 2, (b.minY + b.maxY) / 2],
			zoom: 14,
			maxZoom: MAP_MAX_ZOOM,
			attributionControl: { compact: true },
		});
		map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
		state.map = map;
		const loaded = await whenLoaded(map);
		if (gen !== state.gen) return;
		if (!loaded) {
			if (overlay()) overlay().textContent = "The basemap did not load — the map needs server.arcgisonline.com and unpkg.com.";
			return;
		}
		setBasemap(map, state.basemap);
		map.addSource("irm-blocks", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
		map.addLayer({
			id: "irm-fill",
			type: "fill",
			source: "irm-blocks",
			paint: { "fill-color": ["get", "color"], "fill-opacity": 0.42 },
		});
		map.addLayer({
			id: "irm-line",
			type: "line",
			source: "irm-blocks",
			paint: { "line-color": "#fafaf6", "line-width": 1.2, "line-opacity": 0.9 },
		});
		map.on("click", "irm-fill", (e) => {
			const f = e.features && e.features[0];
			if (f) focus(f.properties.block, false);
		});
		map.on("mouseenter", "irm-fill", () => {
			map.getCanvas().style.cursor = "pointer";
		});
		map.on("mouseleave", "irm-fill", () => {
			map.getCanvas().style.cursor = "";
		});
		/* Open on what has been read: with every farm selected the blocks span
		 * two farms ~20 km apart and start out too small to click. */
		const read = state.features.filter((x) => state.readings.get(x.properties.block)?.irrometer_1ft != null);
		const fb = read.length ? bounds(read) : b;
		map.fitBounds(
			[
				[fb.minX, fb.minY],
				[fb.maxX, fb.maxY],
			],
			{ padding: 60, maxZoom: 16, duration: 0 }
		);
		paint();
		if (overlay()) overlay().classList.add("hidden");
	}

	return {
		/* blocks: api.weather.fetch's `blocks` (latest reading + history per block). */
		async render(blocks, farm) {
			state.readings = new Map((blocks || []).map((x) => [x.name, x]));
			const f = farm || "";
			if (f !== state.farm) {
				state.farm = f;
				await build(f);
			} else {
				paint();
			}
			const open = $(`#${id}-card`);
			if (open && !open.hidden) {
				const title = open.querySelector(".irm__title");
				const f2 = title && state.features.find((x) => x.properties.block_label === title.textContent);
				if (f2) card(f2.properties.block);
			}
		},
		destroy() {
			state.gen++;
			state.markers.forEach((m) => m.remove());
			state.markers = [];
			if (state.map) state.map.remove();
			state.map = null;
		},
	};
}
