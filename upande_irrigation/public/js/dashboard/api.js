/* Frappe API access for the irrigation dashboard.
 *
 * One place that knows about CSRF, credentials and Frappe's error envelope.
 * Views never call fetch() directly — they call get()/post() and catch an
 * ApiError, which carries the server's own wording rather than "HTTP 417".
 */

const BASE = "/api/method/";

export class ApiError extends Error {
	constructor(message, { status = 0, serverMessages = [], excType = "" } = {}) {
		super(message);
		this.name = "ApiError";
		this.status = status;
		this.serverMessages = serverMessages;
		this.excType = excType;
	}
}

export function csrfToken() {
	if (window.frappe && window.frappe.csrf_token) return window.frappe.csrf_token;
	if (window.csrf_token && window.csrf_token !== "token") return window.csrf_token;
	const m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
	return m ? decodeURIComponent(m[1]) : "";
}

function headers(extra) {
	return {
		Accept: "application/json",
		"X-Frappe-CSRF-Token": csrfToken(),
		"X-Requested-With": "XMLHttpRequest",
		...(extra || {}),
	};
}

/* Frappe packs user-facing messages into _server_messages as a JSON string of
 * JSON strings. Unwrap to a flat list of plain strings. */
function parseServerMessages(payload) {
	const raw = payload && payload._server_messages;
	if (!raw) return [];
	let list;
	try {
		list = JSON.parse(raw);
	} catch (e) {
		return [String(raw)];
	}
	return (Array.isArray(list) ? list : [list])
		.map((item) => {
			try {
				const o = typeof item === "string" ? JSON.parse(item) : item;
				return String((o && (o.message || o.title)) || item);
			} catch (e) {
				return String(item);
			}
		})
		.map((s) => s.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim())
		.filter(Boolean);
}

/* A 403 behind an open tab means the session expired. Sending the operator to
 * login beats rendering a permission error they can't action. */
function bounceIfLoggedOut(status) {
	if (status !== 403) return false;
	const target = encodeURIComponent(location.pathname + location.hash);
	location.href = `/login?redirect-to=${target}`;
	return true;
}

async function unwrap(response) {
	let payload = null;
	try {
		payload = await response.json();
	} catch (e) {
		/* Frappe renders an HTML error page for some failures. */
	}

	if (!response.ok) {
		if (bounceIfLoggedOut(response.status)) {
			throw new ApiError("Session expired — redirecting to login.", {
				status: response.status,
			});
		}
		const serverMessages = parseServerMessages(payload);
		const message =
			serverMessages[0] ||
			(payload && (payload.exception || payload.message)) ||
			`Request failed (HTTP ${response.status})`;
		throw new ApiError(String(message).replace(/^\w+Error:\s*/, ""), {
			status: response.status,
			serverMessages,
			excType: (payload && payload.exc_type) || "",
		});
	}

	return {
		data: payload ? payload.message : null,
		/* msgprint output on a successful call — e.g. the pan-evaporation
		 * anomaly warning. Surfaced, not swallowed. */
		serverMessages: parseServerMessages(payload),
	};
}

export async function get(method, params) {
	const qs = new URLSearchParams();
	Object.entries(params || {}).forEach(([k, v]) => {
		if (v !== undefined && v !== null && v !== "") qs.append(k, v);
	});
	const url = BASE + method + (qs.toString() ? `?${qs}` : "");
	const r = await fetch(url, { method: "GET", credentials: "same-origin", headers: headers() });
	return unwrap(r);
}

export async function post(method, params) {
	const body = new URLSearchParams();
	Object.entries(params || {}).forEach(([k, v]) => {
		if (v === undefined || v === null) return;
		body.append(k, typeof v === "object" ? JSON.stringify(v) : v);
	});
	const r = await fetch(BASE + method, {
		method: "POST",
		credentials: "same-origin",
		headers: headers({ "Content-Type": "application/x-www-form-urlencoded" }),
		body: body.toString(),
	});
	return unwrap(r);
}

/* Insert a doc through the standard endpoint so its controller hooks run. */
export function insertDoc(doc) {
	return post("frappe.client.insert", { doc });
}

export function getList(doctype, { filters, fields, orderBy, limit } = {}) {
	return get("frappe.client.get_list", {
		doctype,
		filters: filters ? JSON.stringify(filters) : undefined,
		fields: fields ? JSON.stringify(fields) : undefined,
		order_by: orderBy,
		limit_page_length: limit === undefined ? 0 : limit,
	});
}
