// The v15 workspace "Smart Irrigation" is now "Upande Irrigation" (renamed to
// match its sidebar, like upande_travel). Desk routes never reach
// website_redirects, so an old bookmark to /app/smart-irrigation is re-routed
// here by the desk router instead of landing on "Page not found".
frappe.re_route["smart-irrigation"] = "upande-irrigation";
