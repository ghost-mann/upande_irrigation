"""Server-side context for /upande-irrigation.

The single operator dashboard for the app: weather, IoT telemetry, water and
energy, year-on-year comparison, the 3D field map, live shift status and valve
control — one sidebar, one theme (the Upande house style shared with Task Work
Hub).

Replaces the former /meniscus, /irrigation-now and /irrigation-control pages,
which now 301 here (see website_redirects in hooks.py).

The page shell is static HTML; every view fetches its own data from
upande_irrigation.api.* through public/js/dashboard/api.js.
"""

import os

import frappe

no_cache = 1  # data is live; don't cache the shell

# The dashboard's CSS and JS are served straight from public/ (symlinked into
# sites/assets), and Frappe sends those with Cache-Control: max-age=43200.
# Without a version token in the URL, a deploy would not reach an operator's
# browser for another twelve hours. These are the files to fingerprint.
_ASSET_DIRS = (("css",), ("js", "dashboard"))


def get_context(context):
    # AJAX calls require an authenticated session — redirect Guests so we never
    # render a page that's going to 403 its own data fetches.
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/upande-irrigation"
        raise frappe.Redirect

    context.title = "Upande Irrigation"
    context.full_width = True
    # frappe.local.session.get("csrf_token") — what the old pages used — reads the
    # session dict, where the token is not stored, and so always came back None.
    # Those pages happened to work because their JS fell back to the csrf_token
    # cookie. Ask the sessions module for the real token instead, so writes
    # (valve overrides, logging a Weather Reading) don't depend on that fallback.
    context.csrf_token = frappe.sessions.get_csrf_token()
    context.current_user = frappe.session.user
    context.today = frappe.utils.nowdate()
    context.site_label = _site_label()
    context.farms = _irrigation_farms()
    context.asset_version = _asset_version()

    return context


def _asset_version():
    """Fingerprint the dashboard's assets so a deploy busts the browser cache.

    Newest mtime across the CSS and view modules, hex-encoded. It changes when
    and only when one of those files changes, which is the behaviour a cache
    token needs — a build timestamp would churn on every unrelated build, and a
    static version string would go stale the moment someone forgot to bump it.
    """
    newest = 0
    base = frappe.get_app_path("upande_irrigation", "public")
    for parts in _ASSET_DIRS:
        d = os.path.join(base, *parts)
        try:
            for name in os.listdir(d):
                if name.endswith((".css", ".js")):
                    newest = max(newest, int(os.path.getmtime(os.path.join(d, name))))
        except OSError:
            continue
    return format(newest, "x") if newest else "0"


def _site_label():
    """Company name for the sidebar sub-label, falling back to the site host."""
    label = frappe.db.get_single_value("Global Defaults", "default_company")
    return label or frappe.local.site


def _irrigation_farms():
    """Farms the dashboard should offer in its filter.

    Mirrors api.scheduler.run: farms flagged is_irrigation_farm, falling back to
    every farm so a site that hasn't set the flag yet still gets a usable picker.
    """
    try:
        farms = frappe.get_all(
            "Farm",
            filters={"is_irrigation_farm": 1},
            fields=["name"],
            order_by="name asc",
        )
        if not farms:
            farms = frappe.get_all("Farm", fields=["name"], order_by="name asc")
        return [f["name"] for f in farms]
    except Exception:
        # Farm lives in upande_kaitet; don't take the whole page down if the
        # doctype is missing on this site.
        frappe.log_error(title="Upande Irrigation dashboard — farm lookup failed")
        return []
