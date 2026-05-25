// Irrigation Planner — form behavior
//   - Auto-fills To Date when From Date is set (7-day window)
//   - Surfaces no-irrigation banner
//   - Live-recomputes child row hrsweek / hrscycle on edit
//
// Was previously the "Irrigation Planner - Client Script".

frappe.ui.form.on('Irrigation Planner', {
    from_date: function(frm) {
        if (frm.doc.from_date) {
            const to_date = frappe.datetime.add_days(frm.doc.from_date, 6);
            frm.set_value('to_date', to_date);
        }
    },

    refresh: function(frm) {
        if (frm.doc.no_irrigation_reason) {
            frm.dashboard.set_headline_alert(
                '<b>ℹ️ ' + frm.doc.no_irrigation_reason + '</b>', 'blue'
            );
        }
    }
});

frappe.ui.form.on('Irrigation Planner Section', {
    irrigation_coverage_pct: function(frm, cdt, cdn) {
        recalc_row(frm, cdt, cdn);
    },
    number_of_cycles: function(frm, cdt, cdn) {
        recalc_cycles_only(cdt, cdn);
    }
});

function recalc_row(frm, cdt, cdn) {
    const row     = locals[cdt][cdn];
    const deficit = flt(frm.doc.irrigation_deficit_mm);
    const mm_hr   = flt(row.mm_hr);
    const cov_pct = flt(row.irrigation_coverage_pct);

    if (!mm_hr || !deficit) {
        frappe.model.set_value(cdt, cdn, 'hrsweek', 0);
        frappe.model.set_value(cdt, cdn, 'hrscycle', 0);
        return;
    }

    const hrs = Math.round((deficit / mm_hr) * (cov_pct / 100) * 10000) / 10000;
    frappe.model.set_value(cdt, cdn, 'hrsweek', hrs);

    const cycles = row.number_of_cycles || (hrs > 5 ? 2 : (hrs > 0 ? 1 : 0));
    frappe.model.set_value(cdt, cdn, 'number_of_cycles', cycles);

    const hrs_cycle = cycles > 0 ? Math.round((hrs / cycles) * 10000) / 10000 : 0;
    frappe.model.set_value(cdt, cdn, 'hrscycle', hrs_cycle);
}

function recalc_cycles_only(cdt, cdn) {
    const row    = locals[cdt][cdn];
    const cycles = row.number_of_cycles || 0;
    const hrs    = row.hrsweek || 0;
    frappe.model.set_value(
        cdt, cdn, 'hrscycle',
        cycles > 0 ? Math.round((hrs / cycles) * 10000) / 10000 : 0
    );
}
