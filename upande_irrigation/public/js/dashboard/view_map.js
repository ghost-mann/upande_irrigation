/* Field Map — 3D valves over block boundaries on a MapLibre basemap.
 *
 * Ported from meniscus's irrigation tab. Changes worth knowing:
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
 *
 * 3. Basemaps are Esri (Satellite / Hybrid / Streets), the Upande map standard.
 *    All three live in one style and switch by layer visibility — setStyle()
 *    would wipe the block layers and the Three.js valve layer. There is no
 *    `glyphs` key, so block labels are HTML markers, not a symbol layer.
 *
 * 4. The map is also a control surface: a valve's popup carries Auto/On/Off,
 *    and #map?valve=<name> (Valve Control's "Show on map") flies to a valve.
 *    Geometry follows the farm filter; changing farm rebuilds the map.
 */

import { pagehead, kpi, statusStrip, icon } from "./shell.js";
import { BASEMAPS, MAP_MAX_ZOOM, addBlockOutlines, basemapStyle, bounds, loadLibs, setBasemap as applyBasemap } from "./maplib.js";

/* Block names appear once the blocks are big enough on screen to carry them. */
const LABEL_MIN_ZOOM = 15.5;
const STATES = [
	["Auto", "Auto", "auto"],
	["Forced Open", "On", "on"],
	["Forced Closed", "Off", "off"],
];

/* Thrown by a build() that a newer mount/unmount/farm change has superseded. */
const STALE = new Error("stale map build");

/* "#map?valve=X" → "X". */
function hashParam(key) {
	const q = (location.hash || "").split("?")[1] || "";
	return new URLSearchParams(q).get(key);
}

const BLOCK_FILL = "#c25a2e";
const WHEEL_CLOSED = 0xd9962e;
const WHEEL_OPEN = 0x3f8f4f;

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
		/* The view object outlives each visit. A build left running by the last
		 * visit must neither mark this one failed nor draw into its canvas, so
		 * every visit (and teardown) starts a new generation. */
		this.gen = (this.gen || 0) + 1;
		this.mapFailed = false;
		this.builtFarm = undefined;

		el.innerHTML = `
${pagehead(
	"Field Map",
	"Valves and block boundaries",
	'Live valve state · click a valve or block for detail',
	`<span class="sev ink" id="map-stat">Loading…</span>
	 <div class="pillgroup" id="map-basemap" role="group" aria-label="Basemap">
		<button type="button" data-base="satellite">Satellite</button>
		<button type="button" data-base="hybrid" class="on">Hybrid</button>
		<button type="button" data-base="streets">Streets</button>
	 </div>
	 <button class="btn ghost" id="map-refresh" type="button">${icon("refresh")}Refresh state</button>`
)}
<div class="status" id="map-status"></div>
<div class="kpi-grid" id="map-kpis"></div>
<div class="card">
	<div class="irm__wrap">
		<div class="fieldmap" id="map-canvas">
			<div class="fieldmap__overlay" id="map-overlay">Loading map…</div>
		</div>
		<aside class="irm__card" id="map-block-card" hidden></aside>
	</div>
	<div class="clegend">
		<span><i style="background:var(--ui-ok)"></i>Open · pulsing</span>
		<span><i style="background:var(--ui-warn)"></i>Closed</span>
		<span><i style="background:${BLOCK_FILL};opacity:.45"></i>Block boundary</span>
	</div>
</div>`;

		el.querySelector("#map-refresh").addEventListener("click", () => this.refresh());
		this.basemap = "hybrid";
		el.querySelectorAll("#map-basemap button").forEach((b) => {
			b.addEventListener("click", () => this.setBasemap(b.getAttribute("data-base")));
		});
		this.unsubscribe = ctx.onFilterChange(() => {});
		/* The shell ignores a hash change within the same view, so a second
		 * "Show on map" while already here is handled by the view itself. */
		this.onHash = () => {
			if ((location.hash || "").startsWith("#map")) this.focusFromHash();
		};
		window.addEventListener("hashchange", this.onHash);
	},

	setBasemap(key) {
		if (!BASEMAPS[key]) return;
		this.basemap = key;
		this.el.querySelectorAll("#map-basemap button").forEach((b) => {
			b.classList.toggle("on", b.getAttribute("data-base") === key);
		});
		applyBasemap(this.map, key);
	},

	/* Tear the map down so the next refresh rebuilds it for a new farm. */
	teardown() {
		this.gen = (this.gen || 0) + 1;
		if (this.labelMarkers) this.labelMarkers.forEach((m) => m.remove());
		this.labelMarkers = [];
		if (this.popup) this.popup.remove();
		this.popup = null;
		if (this.map) this.map.remove();
		this.map = null;
		this.layer = null;
		this.initialised = false;
		this.initialising = null;
		this.mapFailed = false;
		this.valveFeatures = new Map();
		const canvas = this.el.querySelector("#map-canvas");
		if (canvas) canvas.innerHTML = '<div class="fieldmap__overlay" id="map-overlay">Loading map…</div>';
	},

	async refresh() {
		/* Valve state is painted first and independently. The basemap and the 3D
		 * libraries come from external CDNs and WebGL may be unavailable; when
		 * that happens the operator should still get the counts rather than a
		 * view stuck on "Loading map…". */
		const farm = this.ctx.filters.farm || "";
		if (this.initialised && this.builtFarm !== farm) this.teardown();

		await this.paintState();

		if (this.initialised || this.mapFailed) return;
		const gen = this.gen;
		if (!this.initialising) this.initialising = this.build();
		try {
			await this.initialising;
		} catch (err) {
			/* A superseded build says nothing about this visit's map. Otherwise
			 * build() has already written an explanation into the overlay. */
			if (err !== STALE && gen === this.gen) this.mapFailed = true;
			return;
		}
		if (gen !== this.gen) return;
		/* The farm changed while the first build ran: rebuild for the new one. */
		if (this.builtFarm !== (this.ctx.filters.farm || "")) {
			this.teardown();
			return this.refresh();
		}
		/* build() is what counts the block boundaries, so repaint the tiles
		 * now that blockCount is known and light the valves in the layer. */
		await this.paintState();
		this.focusFromHash();
	},

	async build() {
		const { api, charts, filters } = this.ctx;
		const farm = filters.farm || "";
		this.builtFarm = farm;
		const gen = this.gen;
		const checkStale = () => {
			if (gen !== this.gen) throw STALE;
		};
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
		checkStale();
		const { maplibregl, THREE: three } = libs;

		/* Valves and blocks fetch in parallel; either can fail alone. */
		const [valves, blocksFC] = await Promise.all([
			api
				.get("upande_irrigation.api.valves.geojson", { asset_type: "Valve", farm })
				.then(({ data }) => ((data && data.features) || []).filter((f) => (f.properties || {}).asset_type === "Valve"))
				.catch((err) => {
					console.warn("[irrigation] valve geojson failed", err);
					return [];
				}),
			api
				.get("upande_irrigation.api.valves.blocks_geojson", { farm })
				.then(({ data }) => ({ type: "FeatureCollection", features: (data && data.features) || [] }))
				.catch((err) => {
					console.warn("[irrigation] block boundaries failed", err);
					return { type: "FeatureCollection", features: [] };
				}),
		]);

		checkStale();
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
			style: basemapStyle(),
			center,
			zoom: 15.5,
			maxZoom: MAP_MAX_ZOOM,
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
		/* unmount()/teardown() already removed this map. */
		checkStale();

		if (!loaded) {
			if (overlay) {
				overlay.textContent =
					"The basemap did not load. Valve counts above are live; the map needs access to server.arcgisonline.com and unpkg.com.";
			}
			statusStrip(
				status,
				"Map unavailable — the basemap or 3D libraries could not load. Everything else on this page is unaffected.",
				"warn"
			);
			throw new Error("basemap did not load");
		}

		this.setBasemap(this.basemap);
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

		/* Valves stand inside blocks, so a valve near the click wins; otherwise
		 * the block under it opens its card. */
		map.on("click", (e) => {
			const hit = this.nearestValve(e.point, 28);
			if (hit) {
				this.openValvePopup(hit.name);
				return;
			}
			const onBlock = map.getLayer("irr-blocks-fill")
				? map.queryRenderedFeatures(e.point, { layers: ["irr-blocks-fill"] })
				: [];
			if (onBlock && onBlock.length) this.openBlockCard((onBlock[0].properties || {}).block);
		});
		this.maplibregl = maplibregl;

		this.initialised = true;
		if (overlay) overlay.classList.add("hidden");
	},

	/* A valve's popup: identity, live state, and the same Auto/On/Off override
	 * the Valve Control cards offer. */
	openValvePopup(name) {
		const { charts } = this.ctx;
		const f = this.valveFeatures.get(name);
		if (!f || !this.map) return;
		const p = f.properties || {};
		const state = this.stateByName ? this.stateByName.get(name) : null;
		const manual = (state && state.manual_state) || "Auto";

		const el = document.createElement("div");
		el.className = "map-pop";
		el.innerHTML =
			`<strong>${charts.esc(p.asset_label || p.asset_name || "valve")}</strong>` +
			`<div class="map-pop__meta">${charts.esc(p.block || "")}${p.farm ? ` · ${charts.esc(p.farm)}` : ""}</div>` +
			(state
				? `<div class="map-pop__state">State <b>${charts.esc(state.effective_state)}</b>${state.override_active ? ` · override by ${charts.esc(state.override_set_by || "—")}` : " · on schedule"}</div>`
				: "") +
			`<div class="valve-actions">${STATES.map(
				([s, label, kind]) =>
					`<button class="vbtn ${kind}${manual === s ? " active" : ""}" data-state="${s}" type="button">${label}</button>`
			).join("")}</div>` +
			'<div class="map-pop__err" hidden></div>';

		el.querySelectorAll(".vbtn[data-state]").forEach((btn) => {
			btn.addEventListener("click", async () => {
				const err = el.querySelector(".map-pop__err");
				el.querySelectorAll(".vbtn").forEach((b) => {
					b.disabled = true;
				});
				err.hidden = true;
				try {
					await this.ctx.api.post("upande_irrigation.api.valves.set_override", {
						valve: name,
						state: btn.getAttribute("data-state"),
					});
					await this.paintState();
					this.openValvePopup(name);
				} catch (e) {
					err.textContent = `Override failed: ${e.message}`;
					err.hidden = false;
					el.querySelectorAll(".vbtn").forEach((b) => {
						b.disabled = false;
					});
				}
			});
		});

		if (this.popup) this.popup.remove();
		this.popup = new this.maplibregl.Popup({ closeOnClick: true, maxWidth: "260px" })
			.setLngLat(f.geometry.coordinates)
			.setDOMContent(el)
			.addTo(this.map);
	},

	/* #map?valve=<name>: fly to that valve and open its popup. */
	focusFromHash() {
		const name = hashParam("valve");
		if (!name || !this.map || !this.valveFeatures.has(name)) return;
		const f = this.valveFeatures.get(name);
		this.map.flyTo({ center: f.geometry.coordinates, zoom: 18, pitch: 55, duration: 900 });
		this.openValvePopup(name);
	},

	/* The block card: what the block is, which shifts water it, this week's
	 * plan for them, its valves (live state from list_states) and its latest
	 * irrometer reading. */
	async openBlockCard(block) {
		const { api, charts } = this.ctx;
		const el = this.el.querySelector("#map-block-card");
		if (!el || !block) return;
		const token = (this.cardToken = (this.cardToken || 0) + 1);
		if (this.outlines) this.outlines.select(block);
		el.hidden = false;
		el.innerHTML = '<div class="irm__meta">Loading block…</div>';
		let info;
		try {
			({ data: info } = await api.get("upande_irrigation.api.valves.block_info", { block }));
		} catch (err) {
			if (token === this.cardToken) el.innerHTML = `<button class="irm__close" type="button" aria-label="Close">&times;</button><div class="irm__empty">Could not load ${charts.esc(block)}: ${charts.esc(err.message)}</div>`;
			this.bindCardClose(el);
			return;
		}
		if (token !== this.cardToken || !info) return;

		const b = info.block || {};
		const hrs = (v) => (v == null ? "—" : `${charts.fmtNum(v, 1)} h`);
		const day = (d) => (d ? charts.fmtDate(d) : "—");
		const slot = (p) =>
			p.scheduled_start && p.scheduled_end && p.scheduled_start !== p.scheduled_end
				? `${charts.esc(charts.fmtDayClock(p.scheduled_start))} → ${charts.esc(charts.fmtClock(p.scheduled_end))}`
				: "not scheduled";
		const section = (title, body) => `<div class="bc__sec"><div class="bc__h">${title}</div>${body}</div>`;

		const valves = (info.valves || [])
			.map((v) => {
				const st = this.stateByName ? this.stateByName.get(v.name) : null;
				const on = st && st.effective_state === "ON";
				return `<button class="bc__valve" type="button" data-valve="${charts.esc(v.name)}">
	<span>${charts.esc(v.asset_label || v.name)}</span>
	<span class="sev ${st && st.override_active ? "warn" : on ? "ok" : "ink"}">${st ? charts.esc(st.effective_state + (st.override_active ? " · override" : "")) : "—"}</span>
</button>`;
			})
			.join("");

		const shifts = (info.shifts || [])
			.map(
				(s) => `<div class="bc__row">
	<b>${charts.esc(s.shift)}</b> <span class="sev ${s.is_active ? "lo" : "ink"}">${s.is_active ? "Active" : "Inactive"}</span>
	<div class="irm__meta">${s.application_rate_mm_hr ? `${charts.fmtNum(s.application_rate_mm_hr, 1)} mm/hr` : "farm-default rate"} · ${s.irrigation_coverage ? `${charts.fmtNum(s.irrigation_coverage, 0)}% coverage` : "farm-default coverage"}${
		s.other_blocks && s.other_blocks.length ? `<br>with ${s.other_blocks.map((x) => charts.esc(x.replace(/ - [A-Z]{2,4}$/, ""))).join(", ")}` : ""
	}</div>
</div>`
			)
			.join("");

		const plans = (info.planners || [])
			.map(
				(p) => `<a class="bc__row bc__plan" href="/app/irrigation-planner/${encodeURIComponent(p.name)}" target="_blank" rel="noopener">
	<b>${charts.esc(p.block)}</b> <span class="irm__meta">${day(p.from_date)} – ${day(p.to_date)}${p.is_current ? "" : " · latest"}</span>
	<div class="bc__grid">
		<span><small>Required</small>${hrs(p.required_hours)}</span>
		<span><small>Shift</small>${hrs(p.shift_hours)}</span>
		<span><small>Cycles</small>${p.cycles_count || "—"}${p.cycle_hours_each ? ` × ${charts.fmtNum(p.cycle_hours_each, 1)} h` : ""}</span>
		<span><small>Deficit</small>${p.this_week_deficit == null ? "—" : `${charts.fmtNum(p.this_week_deficit, 1)} mm`}</span>
	</div>
	<div class="irm__meta">${slot(p)}${p.z_risk_level ? ` · Z ${charts.esc(p.z_risk_level)}` : ""}${p.docstatus === 1 ? " · submitted" : " · draft"}</div>
	${p.no_irrigation_reason ? `<div class="irm__meta">${charts.esc(p.no_irrigation_reason)}</div>` : ""}
	${p.capacity_warning ? `<div class="irm__meta" style="color:var(--ui-warn)">${charts.esc(p.capacity_warning)}</div>` : ""}
</a>`
			)
			.join("");

		const ir = info.irrometer;
		el.innerHTML = `
<button class="irm__close" type="button" aria-label="Close">&times;</button>
<div class="irm__title">${charts.esc(b.label || block)}</div>
<div class="irm__meta">${charts.esc(b.section || "—")}${b.farm ? ` · ${charts.esc(b.farm)}` : ""}${b.area_ha ? ` · ${charts.fmtNum(b.area_ha, 2)} ha` : ""}</div>
${info.water ? section("Soil water", (() => {
	const w = info.water;
	const d = w.taw_mm ? Math.min(100, (100 * w.depletion_mm) / w.taw_mm) : 0;
	const raw = w.taw_mm ? (100 * w.raw_mm) / w.taw_mm : 0;
	const due = w.depletion_mm >= w.raw_mm;
	return `<div class="bk-gauge" style="width:100%;margin:4px 0 8px"><i style="width:${d}%;background:${due ? "var(--ui-hot)" : "var(--ui-teal)"}"></i><b style="left:${raw}%"></b></div>
<div class="irm__meta" style="margin:0">${charts.fmtNum(w.depletion_mm, 1)} of ${charts.fmtNum(w.taw_mm, 0)} mm used (${Math.round(w.depletion_pct)}%) · irrigate at ${charts.fmtNum(w.raw_mm, 0)} mm<br>
<b>${due ? "Due now" : w.trigger_day != null ? `Due in ${w.trigger_day} d` : "Not due within 7 days"}</b> · last irrigated ${w.last_irrigated ? charts.esc(charts.fmtDate(w.last_irrigated)) : "— none recorded"}
${w.stale_weather ? '<br><span style="color:var(--ui-warn)">Weather estimated — no reading for 3+ days</span>' : ""}</div>
<a class="valve-locate" href="#planner?tab=blocks">Open in the irrigation plan</a>`;
})()) : ""}
${section(`Valves · ${(info.valves || []).length}`, valves || '<div class="irm__empty">No valve is mapped to this block.</div>')}
${section("Shifts", shifts || '<div class="irm__empty">Not in any shift — set it on Irrigation Scheduler.</div>')}
${section("This week", plans || '<div class="irm__empty">No planner for this block\'s shifts yet.</div>')}
${section(
	"Irrometer",
	ir
		? `<div class="irm__meta" style="margin:0">1 ft <b>${ir.irrometer_1ft_reading ?? "—"}</b> · 2 ft <b>${ir.irrometer_2ft_reading ?? "—"}</b> cb · ${day(ir.date)}</div>`
		: '<div class="irm__empty">No readings yet.</div>'
)}
<a class="valve-locate" href="/app/warehouse/${encodeURIComponent(block)}" target="_blank" rel="noopener">Open block record</a>`;

		el.querySelectorAll(".bc__valve").forEach((btn) => {
			btn.addEventListener("click", () => this.openValvePopup(btn.getAttribute("data-valve")));
		});
		this.bindCardClose(el);
	},

	bindCardClose(el) {
		const close = el.querySelector(".irm__close");
		if (close) {
			close.addEventListener("click", () => {
				el.hidden = true;
				if (this.outlines) this.outlines.select("");
				this.cardToken = (this.cardToken || 0) + 1;
			});
		}
	},

	addBlockLayers(maplibregl, map, fc) {
		const { charts } = this.ctx;
		if (map.getSource("irr-blocks")) return;
		map.addSource("irr-blocks", { type: "geojson", data: fc });
		this.outlines = addBlockOutlines(map, "irr-blocks", "irr-blocks", {
			"fill-color": BLOCK_FILL,
			"fill-opacity": 0.14,
		});
		/* No glyph server, so labels are HTML markers at each block's centre. */
		this.labelMarkers = (fc.features || []).map((f) => {
			const b = bounds([f]);
			const el = document.createElement("div");
			el.className = "map-label";
			el.textContent = (f.properties || {}).block_label || (f.properties || {}).block || "";
			return new maplibregl.Marker({ element: el })
				.setLngLat([(b.minX + b.maxX) / 2, (b.minY + b.maxY) / 2])
				.addTo(map);
		});
		const showLabels = () => {
			const on = map.getZoom() >= LABEL_MIN_ZOOM;
			this.labelMarkers.forEach((m) => {
				m.getElement().style.display = on ? "" : "none";
			});
		};
		map.on("zoom", showLabels);
		showLabels();
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
		this.gen = (this.gen || 0) + 1;
		if (this.unsubscribe) this.unsubscribe();
		if (this.onHash) window.removeEventListener("hashchange", this.onHash);
		if (this.labelMarkers) this.labelMarkers.forEach((m) => m.remove());
		this.labelMarkers = [];
		if (this.map) {
			this.map.remove();
			this.map = null;
		}
		this.layer = null;
		this.popup = null;
		this.initialised = false;
		this.initialising = null;
		this.valveFeatures = new Map();
	},
};
