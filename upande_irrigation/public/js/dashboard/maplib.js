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
