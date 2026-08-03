// Irrigation Planner — form behavior
//   - Auto-fills To Date when From Date is set (7-day window)
//   - Surfaces the no-irrigation reason, the capacity warning, and the gap
//     between what the shift needed and what the pump granted
//
// Was previously the "Irrigation Planner - Client Script".
//
// The per-row handlers that used to live here are gone with the
// irrigation_calculations child table. They computed
//     hrs = deficit / mm_hr * (coverage / 100)
// while the server divides by coverage, so a row's hours never matched the
// header's — and the row's mm_hr never reached the server calculation at all.

frappe.ui.form.on('Irrigation Planner', {
    from_date: function(frm) {
        if (frm.doc.from_date) {
            frm.set_value('to_date', frappe.datetime.add_days(frm.doc.from_date, 6));
        }
    },

    refresh: function(frm) {
        frm.dashboard.clear_headline();

        if (frm.doc.capacity_warning) {
            const critical = /CHRONIC|PERSISTENT|Not scheduled/.test(frm.doc.capacity_warning);
            frm.dashboard.set_headline_alert(
                '<b>' + frappe.utils.escape_html(frm.doc.capacity_warning) + '</b>',
                critical ? 'red' : 'orange'
            );
            return;
        }

        if (frm.doc.no_irrigation_reason) {
            frm.dashboard.set_headline_alert(
                '<b>ℹ️ ' + frappe.utils.escape_html(frm.doc.no_irrigation_reason) + '</b>',
                'blue'
            );
            return;
        }

        // Demand and allocation are separate fields for a reason: the scheduler can
        // grant less than the week needs. Say so plainly instead of showing one
        // number and letting it read as both.
        const required = flt(frm.doc.required_hours);
        const granted = flt(frm.doc.shift_hours);
        if (required > 0 && !granted) {
            frm.dashboard.set_headline_alert(
                '<b>Needs ' + required.toFixed(2) + ' hr — not yet scheduled. ' +
                'Run the Irrigation Scheduler to allocate pump time.</b>',
                'orange'
            );
        } else if (required > 0) {
            const pct = Math.round((granted / required) * 100);
            frm.dashboard.set_headline_alert(
                '<b>' + granted.toFixed(2) + ' of ' + required.toFixed(2) +
                ' hr granted (' + pct + '%) over ' + (frm.doc.cycles_count || 0) + ' cycle(s).</b>',
                pct >= 100 ? 'green' : 'orange'
            );
        }
    }
});
