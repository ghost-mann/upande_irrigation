/* Field Map — 3D valves over block boundaries on a MapLibre basemap.
 *
 * Ported from meniscus's irrigation tab. Two changes worth knowing:
 *
 * 1. Valve highlights now reflect REAL state. Meniscus paired fake switch ids
 *    ("70ha-v3") to real valves with a string-matching heuristic
 *    (BONDENI|WESA|KINYORO → 70ha, DAIRY splitting on its trailing number, …)
 *    because its switch grid was a simulation. The grid is gone, so each valve
 *    is keyed by its own asset_name and lit from api.valves.list_states'
 *    effective_state. No pairing, no guessing.
 *
 * 2. MapLibre and Three.js are imported dynamically here rather than loaded in
 *    the page head, so the other seven views don't pay for ~1 MB of map
 *    libraries they never use.
 */

import { pagehead, kpi, statusStrip, icon } from "./shell.js";

const MAPLIBRE_JS = "https://unpkg.com/maplibre-gl@5/dist/maplibre-gl.js";
const MAPLIBRE_CSS = "https://unpkg.com/maplibre-gl@5/dist/maplibre-gl.css";
const THREE_ESM = "https://unpkg.com/three@0.160.0/build/three.module.js";
const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";

const BLOCK_FILL = "#c25a2e";
const BLOCK_LINE = "#7c2f16";
const WHEEL_CLOSED = 0xd9962e;
const WHEEL_OPEN = 0x3f8f4f;

let libsPromise = null;

/* Load the map libraries once per page life. MapLibre is a UMD global; Three is
 * an ES module. */
function loadLibs() {
	if (libsPromise) return libsPromise;
	libsPromise = (async () => {
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
		const THREE = await import(/* webpackIgnore: true */ THREE_ESM);
		return { maplibregl: window.maplibregl, THREE };
	})().catch((err) => {
		libsPromise = null;
		throw err;
	});
	return libsPromise;
}

function bounds(features) {
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

/* Custom MapLibre layer rendering each valve as a Three.js group. */
function makeValvesLayer(THREE, maplibregl, features) {
	return {
		id: "valves3d",
		type: "custom",
		renderingMode: "3d",
		features,
		valves: new Map(),
		open: new Map(),

		onAdd(map, gl) {
			this.map = map;
			this.camera = new THREE.Camera();
			this.scene = new THREE.Scene();
			this.scene.add(new THREE.AmbientLight(0xffffff, 0.55));
			const sun = new THREE.DirectionalLight(0xffffff, 0.9);
			sun.position.set(0.4, 1, 0.6);
			this.scene.add(sun);
			this.renderer = new THREE.WebGLRenderer({ canvas: map.getCanvas(), context: gl, antialias: true });
			this.renderer.autoClear = false;

			if (!this.features.length) return;

			const [lng0, lat0] = this.features[0].geometry.coordinates;
			this.anchor = maplibregl.MercatorCoordinate.fromLngLat([lng0, lat0], 0);
			const meter = this.anchor.meterInMercatorCoordinateUnits();

			const pipeMat = new THREE.MeshLambertMaterial({ color: 0x4b6079, flatShading: true });
			const stemMat = new THREE.MeshLambertMaterial({ color: 0xb8c0c8, flatShading: true });

			for (const f of this.features) {
				const props = f.properties || {};
				const [lng, lat] = f.geometry.coordinates;
				const m = maplibregl.MercatorCoordinate.fromLngLat([lng, lat], 0);
				const dx = (m.x - this.anchor.x) / meter;
				const dz = (m.y - this.anchor.y) / meter;
				const h = Math.max(0.1, parseFloat(props.height) || 3);
				const r = Math.max(0.05, parseFloat(props.radius) || 0.5);
				const VS = 10;
				const vh = h * VS;
				const vr = r * VS;

				const group = new THREE.Group();
				const pipeH = vh * 0.55;
				const pipe = new THREE.Mesh(new THREE.CylinderGeometry(vr, vr, pipeH, 16), pipeMat);
				pipe.position.y = pipeH / 2;
				group.add(pipe);

				const stemH = vh * 0.4;
				const stem = new THREE.Mesh(new THREE.CylinderGeometry(vr * 0.22, vr * 0.22, stemH, 12), stemMat);
				stem.position.y = pipeH + stemH / 2;
				group.add(stem);

				const wheelR = vr * 1.6;
				const wheelH = Math.max(0.05, vh * 0.05);
				const wheel = new THREE.Mesh(
					new THREE.CylinderGeometry(wheelR, wheelR, wheelH, 24),
					new THREE.MeshBasicMaterial({ color: WHEEL_CLOSED })
				);
				wheel.position.y = pipeH + stemH + wheelH / 2;
				group.add(wheel);

				const halo = new THREE.Mesh(
					new THREE.RingGeometry(vr * 1.8, vr * 3.2, 40),
					new THREE.MeshBasicMaterial({ color: WHEEL_OPEN, transparent: true, opacity: 0.45, side: THREE.DoubleSide, depthWrite: false })
				);
				halo.rotation.x = -Math.PI / 2;
				halo.position.y = 0.05;
				halo.visible = false;
				group.add(halo);

				const beaconH = vh * 0.6;
				const beacon = new THREE.Mesh(
					new THREE.CylinderGeometry(vr * 0.35, vr * 0.05, beaconH, 16),
					new THREE.MeshBasicMaterial({ color: 0xb6f0c2, transparent: true, opacity: 0.55, depthWrite: false })
				);
				beacon.position.y = pipeH + stemH + wheelH + beaconH / 2;
				beacon.visible = false;
				group.add(beacon);

				group.position.set(dx, 0, dz);
				this.scene.add(group);
				const name = props.asset_name || `valve-${this.valves.size}`;
				this.valves.set(name, { group, wheel, halo, beacon });
				this.open.set(name, false);
			}
		},

		setOpen(name, isOpen) {
			const v = this.valves.get(name);
			if (!v) return;
			this.open.set(name, !!isOpen);
			if (!isOpen) {
				v.wheel.material.color.setHex(WHEEL_CLOSED);
				v.halo.visible = false;
				v.beacon.visible = false;
			} else {
				v.halo.visible = true;
				v.beacon.visible = true;
			}
			if (this.map) this.map.triggerRepaint();
		},

		render(gl, args) {
			if (!this.anchor) return;
			let matrix = args;
			if (args && !Array.isArray(args) && typeof args !== "function") {
				matrix =
					(args.defaultProjectionData &&
						(args.defaultProjectionData.mainMatrix || args.defaultProjectionData.matrix)) ||
					args.matrix ||
					args;
			}
			if (!matrix || matrix.length !== 16) return;

			/* Open valves pulse so they read at a glance. */
			const t = performance.now() * 0.004;
			const pulse = (Math.sin(t) + 1) / 2;
			let anyOpen = false;
			for (const [name, v] of this.valves) {
				if (this.open.get(name)) {
					anyOpen = true;
					v.wheel.material.color.setRGB(0.25 + pulse * 0.45, 0.73 + pulse * 0.2, 0.31 + pulse * 0.45);
					v.beacon.material.opacity = 0.35 + pulse * 0.55;
					v.halo.material.opacity = 0.3 + pulse * 0.45;
				}
			}

			const meter = this.anchor.meterInMercatorCoordinateUnits();
			const rotX = new THREE.Matrix4().makeRotationAxis(new THREE.Vector3(1, 0, 0), Math.PI / 2);
			const world = new THREE.Matrix4()
				.makeTranslation(this.anchor.x, this.anchor.y, this.anchor.z)
				.scale(new THREE.Vector3(meter, -meter, meter))
				.multiply(rotX);
			this.camera.projectionMatrix = new THREE.Matrix4().fromArray(matrix).multiply(world);
			this.renderer.resetState();
			this.renderer.render(this.scene, this.camera);
			if (anyOpen) this.map.triggerRepaint();
		},

		onRemove() {
			this.scene.traverse((o) => {
				if (o.geometry) o.geometry.dispose();
				if (o.material) o.material.dispose();
			});
		},
	};
}

export default {
	id: "map",
	label: "Field Map",
	pollMs: 60000,

	mount(el, ctx) {
		this.ctx = ctx;
		this.el = el;
		this.map = null;
		this.layer = null;
		this.valveFeatures = new Map();
		this.initialised = false;
		this.initialising = null;

		el.innerHTML = `
${pagehead(
	"Field Map",
	"Valves and block boundaries",
	'Live valve state · click a valve or block for detail',
	`<span class="ui-sev ink" id="map-stat">Loading…</span>
	 <button class="ui-btn ghost" id="map-refresh" type="button">${icon("refresh")}Refresh state</button>`
)}
<div class="ui-status" id="map-status"></div>
<div class="ui-kpis" id="map-kpis"></div>
<div class="ui-card">
	<div class="ui-map" id="map-canvas">
		<div class="ui-map-overlay" id="map-overlay">Loading map…</div>
	</div>
	<div class="ui-legend row">
		<span><i style="background:var(--ui-ok)"></i>Open · pulsing</span>
		<span><i style="background:var(--ui-warn)"></i>Closed</span>
		<span><i style="background:${BLOCK_FILL};opacity:.45"></i>Block boundary</span>
	</div>
</div>`;

		el.querySelector("#map-refresh").addEventListener("click", () => this.refresh());
		this.unsubscribe = ctx.onFilterChange(() => {});
	},

	async refresh() {
		/* Valve state is painted first and independently. The basemap and the 3D
		 * libraries come from external CDNs and WebGL may be unavailable; when
		 * that happens the operator should still get the counts rather than a
		 * view stuck on "Loading map…". */
		await this.paintState();

		if (this.initialised || this.mapFailed) return;
		if (!this.initialising) this.initialising = this.build();
		try {
			await this.initialising;
			/* build() is what counts the block boundaries, so repaint the tiles
			 * now that blockCount is known and light the valves in the layer. */
			await this.paintState();
		} catch (err) {
			/* build() has already written an explanation into the overlay. */
			this.mapFailed = true;
		}
	},

	async build() {
		const { api, charts } = this.ctx;
		const status = this.el.querySelector("#map-status");
		const overlay = this.el.querySelector("#map-overlay");

		let libs;
		try {
			libs = await loadLibs();
		} catch (err) {
			statusStrip(status, `${err.message} — the map needs internet access for its basemap and libraries.`);
			if (overlay) overlay.textContent = "Map libraries unavailable.";
			this.initialising = null;
			throw err;
		}
		const { maplibregl, THREE: three } = libs;

		/* Valves and blocks fetch in parallel; either can fail alone. */
		const [valves, blocksFC] = await Promise.all([
			api
				.get("upande_irrigation.api.valves.geojson", { asset_type: "Valve" })
				.then(({ data }) => ((data && data.features) || []).filter((f) => (f.properties || {}).asset_type === "Valve"))
				.catch((err) => {
					console.warn("[irrigation] valve geojson failed", err);
					return [];
				}),
			api
				.getList("Warehouse", {
					filters: [
						["warehouse_type", "=", "Block"],
						["disabled", "=", 0],
					],
					fields: ["name", "warehouse_name", "custom_farm", "parent_warehouse", "custom_raw_geojson"],
					orderBy: "name asc",
				})
				.then(({ data }) => {
					const features = [];
					(data || []).forEach((wh) => {
						if (!wh.custom_raw_geojson) return;
						let geo;
						try {
							geo = JSON.parse(wh.custom_raw_geojson);
						} catch (e) {
							return;
						}
						((geo && geo.features) || []).forEach((f) => {
							if (!f || !f.geometry) return;
							features.push({
								type: "Feature",
								geometry: f.geometry,
								properties: {
									...(f.properties || {}),
									block: wh.name,
									block_label: wh.warehouse_name || wh.name,
									farm: wh.custom_farm || "",
									section: wh.parent_warehouse || "",
								},
							});
						});
					});
					return { type: "FeatureCollection", features };
				})
				.catch((err) => {
					console.warn("[irrigation] block boundaries failed", err);
					return { type: "FeatureCollection", features: [] };
				}),
		]);

		valves.forEach((f) => {
			const name = (f.properties || {}).asset_name;
			if (name) this.valveFeatures.set(name, f);
		});
		this.blockCount = (blocksFC.features || []).length;

		const stat = this.el.querySelector("#map-stat");
		if (stat) stat.textContent = `${valves.length} valves · ${this.blockCount} blocks`;

		if (!valves.length && !this.blockCount) {
			if (overlay) overlay.textContent = "No valves or block boundaries found.";
			this.initialised = true;
			return;
		}

		const focus = valves.length ? valves : blocksFC.features || [];
		const b = bounds(focus);
		/* Fall back to the Lokitela area rather than dropping into the ocean. */
		const center = b.empty ? [34.86, 0.99] : [(b.minX + b.maxX) / 2, (b.minY + b.maxY) / 2];

		const map = new maplibregl.Map({
			container: "map-canvas",
			style: BASEMAP_STYLE,
			center,
			zoom: 15.5,
			pitch: 55,
			attributionControl: { compact: true },
		});
		map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
		this.map = map;

		/* Don't wait forever on "load": without WebGL, or behind a blocked tile
		 * host, that event never fires and the view would hang indefinitely. */
		const loaded = await new Promise((res) => {
			let settled = false;
			const done = (ok) => {
				if (settled) return;
				settled = true;
				res(ok);
			};
			map.on("load", () => done(true));
			map.on("error", (e) => {
				console.warn("[irrigation] map error", e && e.error);
			});
			setTimeout(() => done(false), 15000);
		});

		if (!loaded) {
			if (overlay) {
				overlay.textContent =
					"The basemap did not load. Valve counts above are live; the map needs access to tiles.openfreemap.org and unpkg.com.";
			}
			statusStrip(
				status,
				"Map unavailable — the basemap or 3D libraries could not load. Everything else on this page is unaffected.",
				"warn"
			);
			throw new Error("basemap did not load");
		}

		if (this.blockCount) this.addBlockLayers(maplibregl, map, blocksFC);

		if (valves.length) {
			/* The layer's methods close over the THREE module passed in here. */
			this.layer = makeValvesLayer(three, maplibregl, valves);
			map.addLayer(this.layer);
		}

		if (!b.empty && (b.minX !== b.maxX || b.minY !== b.maxY)) {
			map.fitBounds(
				[
					[b.minX, b.minY],
					[b.maxX, b.maxY],
				],
				{ padding: 80, pitch: 55, duration: 600 }
			);
		}

		map.on("click", (e) => {
			const onBlock = map.queryRenderedFeatures(e.point, { layers: ["irr-blocks-fill"] });
			if (onBlock && onBlock.length) return; // block handler runs instead
			const hit = this.nearestValve(e.point, 28);
			if (!hit) return;
			const p = hit.f.properties || {};
			const state = this.stateByName ? this.stateByName.get(hit.name) : null;
			new maplibregl.Popup({ closeOnClick: true })
				.setLngLat(hit.f.geometry.coordinates)
				.setHTML(
					`<div><strong>${charts.esc(p.asset_label || p.asset_name || "valve")}</strong><br>` +
						`<span style="color:var(--ui-mute)">${charts.esc(p.block || "")}${p.farm ? ` · ${charts.esc(p.farm)}` : ""}</span>` +
						(state
							? `<br>State: <strong>${charts.esc(state.effective_state)}</strong>${state.override_active ? " (override)" : ""}`
							: "") +
						"</div>"
				)
				.addTo(map);
		});

		this.initialised = true;
		if (overlay) overlay.classList.add("hidden");
	},

	addBlockLayers(maplibregl, map, fc) {
		const { charts } = this.ctx;
		if (map.getSource("irr-blocks")) return;
		map.addSource("irr-blocks", { type: "geojson", data: fc });
		map.addLayer({
			id: "irr-blocks-fill",
			type: "fill",
			source: "irr-blocks",
			paint: { "fill-color": BLOCK_FILL, "fill-opacity": 0.16 },
		});
		map.addLayer({
			id: "irr-blocks-line",
			type: "line",
			source: "irr-blocks",
			paint: { "line-color": BLOCK_LINE, "line-width": 1.4, "line-opacity": 0.85 },
		});
		map.addLayer({
			id: "irr-blocks-label",
			type: "symbol",
			source: "irr-blocks",
			layout: { "text-field": ["get", "block_label"], "text-size": 11, "text-allow-overlap": false },
			paint: { "text-color": "#3a3a34", "text-halo-color": "#f4f3ef", "text-halo-width": 1.5 },
		});
		map.on("click", "irr-blocks-fill", (e) => {
			const f = e.features && e.features[0];
			if (!f) return;
			const p = f.properties || {};
			new maplibregl.Popup({ closeOnClick: true })
				.setLngLat(e.lngLat)
				.setHTML(
					`<div><strong>${charts.esc(p.block_label || p.block || "block")}</strong><br><span style="color:var(--ui-mute)">${charts.esc(p.section || "")}${p.farm ? ` · ${charts.esc(p.farm)}` : ""}</span></div>`
				)
				.addTo(map);
		});
		map.on("mouseenter", "irr-blocks-fill", () => {
			map.getCanvas().style.cursor = "pointer";
		});
		map.on("mouseleave", "irr-blocks-fill", () => {
			map.getCanvas().style.cursor = "";
		});
	},

	/* Project each valve to screen space and take the closest within maxPx.
	 * Cheap at a few dozen valves, and avoids a second MapLibre source. */
	nearestValve(point, maxPx) {
		if (!this.map) return null;
		const limitSq = (maxPx || 28) ** 2;
		let best = null;
		let bestD = Infinity;
		for (const [name, f] of this.valveFeatures) {
			const p = this.map.project(f.geometry.coordinates);
			const d = (p.x - point.x) ** 2 + (p.y - point.y) ** 2;
			if (d < bestD) {
				bestD = d;
				best = { name, f };
			}
		}
		return best && bestD < limitSq ? best : null;
	},

	async paintState() {
		const { api, filters } = this.ctx;
		const status = this.el.querySelector("#map-status");

		let data;
		try {
			({ data } = await api.get("upande_irrigation.api.valves.list_states", { farm: filters.farm }));
		} catch (err) {
			statusStrip(status, `Map is showing geometry only — valve state failed: ${err.message}`, "warn");
			return;
		}
		if (!data) return;

		const valves = data.valves || [];
		this.stateByName = new Map(valves.map((v) => [v.name, v]));

		const on = valves.filter((v) => v.effective_state === "ON").length;
		const overrides = valves.filter((v) => v.override_active).length;
		this.el.querySelector("#map-kpis").innerHTML =
			kpi("var(--ui-ok)", "Open now", on, "", "pulsing on the map") +
			kpi("var(--ui-warn)", "Closed", valves.length - on, "", "not scheduled") +
			kpi("var(--ui-clay)", "Blocks mapped", this.blockCount || 0, "", "with boundary geometry") +
			kpi(overrides ? "var(--ui-warn)" : "var(--ui-ink4)", "Overrides", overrides, "", overrides ? "manual state set" : "all on schedule");

		if (!this.layer) return;
		/* Light every valve the layer knows about from its real state. */
		for (const name of this.layer.valves.keys()) {
			const v = this.stateByName.get(name);
			this.layer.setOpen(name, !!v && v.effective_state === "ON");
		}
		const stat = this.el.querySelector("#map-stat");
		if (stat) {
			stat.textContent = `${this.valveFeatures.size} valves · ${this.blockCount} blocks · ${on} open`;
		}
	},

	unmount() {
		if (this.unsubscribe) this.unsubscribe();
		if (this.map) {
			this.map.remove();
			this.map = null;
		}
		this.layer = null;
		this.initialised = false;
		this.initialising = null;
		this.valveFeatures = new Map();
	},
};
