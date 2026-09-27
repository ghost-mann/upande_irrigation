/* Shared map plumbing for the Field Map and the irrigation block maps.
 *
 * Esri basemaps (the Upande map standard, as on the Aerial Farm Layout page):
 * Satellite / Hybrid / Streets all live in one style and switch by layer
 * visibility, because setStyle() would wipe every layer a view has added.
 * There is no `glyphs` key, so labels are HTML markers, never symbol layers.
 *
 * MapLibre (and, for the Field Map only, Three.js) load lazily on first use so
 * views without a map don't pay ~1 MB for libraries they never touch.
 */

const MAPLIBRE_JS = "https://unpkg.com/maplibre-gl@5/dist/maplibre-gl.js";
const MAPLIBRE_CSS = "https://unpkg.com/maplibre-gl@5/dist/maplibre-gl.css";
const THREE_ESM = "https://unpkg.com/three@0.160.0/build/three.module.js";
const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/";
/* Past z18 Esri answers with grey "Map data not available" tiles over Kenya. */
const SAT_MAXZOOM = 18;
export const MAP_MAX_ZOOM = 19;

const esri = (path, attribution) => ({
	type: "raster",
	tiles: [`${ESRI}${path}/MapServer/tile/{z}/{y}/{x}`],
	tileSize: 256,
	maxzoom: SAT_MAXZOOM,
	attribution,
});

export const BASEMAPS = {
	satellite: ["base-sat"],
	hybrid: ["base-sat", "base-ref"],
	streets: ["base-streets"],
};

/* A fresh copy per map: MapLibre keeps a reference to the style object. */
export function basemapStyle() {
	return {
		version: 8,
		sources: {
			sat: esri("World_Imagery", "Imagery © Esri, Maxar"),
			ref: esri("Reference/World_Boundaries_and_Places", ""),
			streets: esri("World_Street_Map", "© Esri"),
		},
		layers: [
			{ id: "base-sat", type: "raster", source: "sat" },
			{ id: "base-ref", type: "raster", source: "ref" },
			{ id: "base-streets", type: "raster", source: "streets", layout: { visibility: "none" } },
		],
	};
}

export function setBasemap(map, key) {
	if (!map || !BASEMAPS[key]) return;
	const visible = new Set(BASEMAPS[key]);
	["base-sat", "base-ref", "base-streets"].forEach((id) => {
		if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", visible.has(id) ? "visible" : "none");
	});
}

/* The Satellite/Hybrid/Streets pill group, matching the Field Map's. */
export function basemapToggle(id, current = "hybrid") {
	return `<div class="pillgroup" id="${id}" role="group" aria-label="Basemap">
	${["satellite", "hybrid", "streets"]
		.map((k) => `<button type="button" data-base="${k}"${k === current ? ' class="on"' : ""}>${k[0].toUpperCase()}${k.slice(1)}</button>`)
		.join("")}
</div>`;
}

let maplibrePromise = null;
let threePromise = null;

export function loadMapLibre() {
	if (maplibrePromise) return maplibrePromise;
	maplibrePromise = (async () => {
		if (!document.querySelector(`link[href="${MAPLIBRE_CSS}"]`)) {
			const link = document.createElement("link");
			link.rel = "stylesheet";
			link.href = MAPLIBRE_CSS;
			document.head.appendChild(link);
		}
		if (!window.maplibregl) {
			await new Promise((resolve, reject) => {
				const s = document.createElement("script");
				s.src = MAPLIBRE_JS;
				s.onload = resolve;
				s.onerror = () => reject(new Error("could not load MapLibre"));
				document.head.appendChild(s);
			});
		}
		return window.maplibregl;
	})().catch((err) => {
		maplibrePromise = null;
		throw err;
	});
	return maplibrePromise;
}

export function loadThree() {
	if (!threePromise) {
		threePromise = import(/* webpackIgnore: true */ THREE_ESM).catch((err) => {
			threePromise = null;
			throw err;
		});
	}
	return threePromise;
}

/* MapLibre is a UMD global; Three is an ES module. */
export async function loadLibs() {
	const [maplibregl, THREE] = await Promise.all([loadMapLibre(), loadThree()]);
	return { maplibregl, THREE };
}

export function bounds(features) {
	let minX = Infinity;
	let maxX = -Infinity;
	let minY = Infinity;
	let maxY = -Infinity;
	const visit = (c) => {
		if (typeof c[0] === "number") {
			if (c[0] < minX) minX = c[0];
			if (c[0] > maxX) maxX = c[0];
			if (c[1] < minY) minY = c[1];
			if (c[1] > maxY) maxY = c[1];
		} else {
			c.forEach(visit);
		}
	};
	features.forEach((f) => {
		if (f && f.geometry) visit(f.geometry.coordinates);
	});
	return { minX, maxX, minY, maxY, empty: !isFinite(minX) };
}

/* Wait for "load", but not forever: without WebGL, or behind a blocked tile
 * host, it never fires and the view would hang. Resolves true/false. */
export function whenLoaded(map, ms = 15000) {
	return new Promise((res) => {
		let settled = false;
		const done = (ok) => {
			if (settled) return;
			settled = true;
			res(ok);
		};
		map.on("load", () => done(true));
		map.on("error", (e) => console.warn("[irrigation] map error", e && e.error));
		setTimeout(() => done(false), ms);
	});
}

/* Block outlines that read on any imagery: a light line over a dark casing
 * (the Upande map standard), widening with zoom, plus a hover and a selected
 * outline. `fillPaint` is the caller's fill (the Field Map's flat clay, the
 * irrometer map's tension colours). Returns select(block) / hover(block). */
const W = (a, b) => ["interpolate", ["linear"], ["zoom"], 12, a, 15, (a + b) / 2, 18, b];

export function addBlockOutlines(map, source, prefix, fillPaint) {
	const none = ["==", ["get", "block"], ""];
	map.addLayer({ id: `${prefix}-fill`, type: "fill", source, paint: fillPaint });
	map.addLayer({
		id: `${prefix}-hover`,
		type: "fill",
		source,
		filter: none,
		paint: { "fill-color": "#fafaf6", "fill-opacity": 0.22 },
	});
	map.addLayer({
		id: `${prefix}-casing`,
		type: "line",
		source,
		layout: { "line-join": "round" },
		paint: { "line-color": "#0a0a0a", "line-opacity": 0.55, "line-width": W(2.6, 6) },
	});
	map.addLayer({
		id: `${prefix}-line`,
		type: "line",
		source,
		layout: { "line-join": "round" },
		paint: { "line-color": "#fdf8ef", "line-width": W(1.1, 2.6) },
	});
	map.addLayer({
		id: `${prefix}-sel-casing`,
		type: "line",
		source,
		filter: none,
		layout: { "line-join": "round" },
		paint: { "line-color": "#0a0a0a", "line-opacity": 0.7, "line-width": W(6, 10) },
	});
	map.addLayer({
		id: `${prefix}-sel`,
		type: "line",
		source,
		filter: none,
		layout: { "line-join": "round" },
		paint: { "line-color": "#f4b400", "line-width": W(3, 5) },
	});

	let hovered = "";
	map.on("mousemove", `${prefix}-fill`, (e) => {
		const b = (e.features && e.features[0] && e.features[0].properties.block) || "";
		if (b === hovered) return;
		hovered = b;
		map.setFilter(`${prefix}-hover`, ["==", ["get", "block"], b]);
		map.getCanvas().style.cursor = b ? "pointer" : "";
	});
	map.on("mouseleave", `${prefix}-fill`, () => {
		hovered = "";
		map.setFilter(`${prefix}-hover`, none);
		map.getCanvas().style.cursor = "";
	});

	return {
		select(block) {
			const f = ["==", ["get", "block"], block || ""];
			map.setFilter(`${prefix}-sel-casing`, f);
			map.setFilter(`${prefix}-sel`, f);
		},
	};
}
