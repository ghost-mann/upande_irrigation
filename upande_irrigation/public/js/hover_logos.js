// Upande app logos play only while hovered.
//
// The animated logos (an SVG with CSS @keyframes, drawn through <img>) play once
// whenever the image loads, so a desk full of them all move at once on every
// visit. This script shows each one at its last frame instead, and restarts it
// while the pointer is over its desk tile or sidebar header.
//
// An <img> SVG cannot see :hover, and setting the same src again does not
// restart it, so each play loads the file from a new blob: URL. The resting
// frame is the same file with every animation pushed far into the past; the
// logos all use `both` fill and finish on the plain Upande arrow badge.
//
// Every Upande app with an animated logo ships an identical copy of this file,
// so the logo still behaves when that app is installed alone; the flag below
// makes the copies after the first do nothing. Keep the copies byte-identical.
(() => {
	if (window.upande_hover_logos) return;
	window.upande_hover_logos = true;

	const ZONES = ".desktop-icon, .sidebar-header";
	const LOGOS = ".desktop-icon img.app-icon, .sidebar-header .header-logo img";
	const REST = "<style>*{animation-delay:-1000s !important}</style></svg>";
	const texts = {}; // asset url -> Promise of the SVG text, or null if it does not animate
	const rests = {}; // asset url -> blob url of its last frame, shared by every tile

	const to_blob = (text) => URL.createObjectURL(new Blob([text], { type: "image/svg+xml" }));

	function load(url) {
		texts[url] ||= fetch(url)
			.then((r) => (r.ok ? r.text() : null))
			.then((text) => (text && text.includes("@keyframes") ? text : null))
			.catch(() => null);
		return texts[url];
	}

	function prepare(img) {
		const url = img.getAttribute("src") || "";
		if (img.dataset.logoSrc || !/^\/assets\/[^?#]+\.svg$/.test(url)) return;
		img.dataset.logoSrc = url;
		// hidden until we know, so an animated logo never plays on load
		img.style.visibility = "hidden";
		load(url).then((text) => {
			if (text) {
				rests[url] ||= to_blob(text.replace(/<\/svg>\s*$/, REST));
				img.src = rests[url];
				img.dataset.logoAnimated = "1";
			}
			img.style.visibility = "";
		});
	}

	function scan(node) {
		if (node.nodeType !== 1) return;
		if (node.matches(LOGOS)) prepare(node);
		node.querySelectorAll(LOGOS).forEach(prepare);
	}

	function hovered(event) {
		const zone = event.target.closest && event.target.closest(ZONES);
		if (!zone || zone.contains(event.relatedTarget)) return null;
		const img = zone.querySelector(LOGOS);
		return img && img.dataset.logoAnimated ? img : null;
	}

	function stop(img) {
		if (img.dataset.logoPlay) URL.revokeObjectURL(img.dataset.logoPlay);
		delete img.dataset.logoPlay;
	}

	document.addEventListener("mouseover", (event) => {
		const img = hovered(event);
		if (!img) return;
		load(img.dataset.logoSrc).then((text) => {
			stop(img);
			img.dataset.logoPlay = to_blob(text);
			img.src = img.dataset.logoPlay;
		});
	});

	document.addEventListener("mouseout", (event) => {
		const img = hovered(event);
		if (!img) return;
		stop(img);
		img.src = rests[img.dataset.logoSrc];
	});

	function start() {
		scan(document.body);
		new MutationObserver((mutations) => {
			for (const m of mutations) m.addedNodes.forEach(scan);
		}).observe(document.body, { childList: true, subtree: true });
	}

	if (document.body) start();
	else document.addEventListener("DOMContentLoaded", start);
})();
