frappe.query_reports["Weekly Irrigation Summary"] = {
	filters: [
		{ fieldname: "farm", label: __("Farm"), fieldtype: "Link", options: "Farm" },
		{
			fieldname: "section",
			label: __("Section"),
			fieldtype: "Link",
			options: "Warehouse",
			get_query: () => ({ filters: { is_group: 1 } }),
		},
		{ fieldname: "from_date", label: __("From"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -56) },
		{ fieldname: "to_date", label: __("To"), fieldtype: "Date", default: frappe.datetime.get_today() },
	],
};
