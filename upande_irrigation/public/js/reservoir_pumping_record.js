// Reservoir Pumping Record — form behavior
//   - On new record, fetch previous meter readings
//   - Auto-compute total hours, electricity used, water volume
//   - Validate meter monotonicity
//
// Was previously the "Reservoir Pumping Record Updater" Client Script.

const WATER_METER_UNITS_TO_M3 = 1;

frappe.ui.form.on('Reservoir Pumping Record', {
    refresh: function(frm) {
        if (frm.is_new()) {
            fetch_previous_readings(frm);
        }
    },
    pump_start_time: function(frm) {
        calculate_total_hours(frm);
    },
    pump_stop_time: function(frm) {
        calculate_total_hours(frm);
    },
    new_electricity_meter_reading: function(frm) {
        calculate_electricity(frm);
    },
    new_water_meter_reading: function(frm) {
        calculate_water(frm);
    },
    previous_electricity_meter_reading: function(frm) {
        calculate_electricity(frm);
    },
    previous_water_meter_reading: function(frm) {
        calculate_water(frm);
    }
});

function calculate_total_hours(frm) {
    const start = frm.doc.pump_start_time;
    const stop  = frm.doc.pump_stop_time;
    if (!start || !stop) {
        frm.set_value('total_hours', 0);
        return;
    }
    let start_min = _time_to_minutes(start);
    let stop_min  = _time_to_minutes(stop);
    if (stop_min < start_min) {
        stop_min += 24 * 60;  // crosses midnight
    }
    const hours = (stop_min - start_min) / 60;
    frm.set_value('total_hours', +hours.toFixed(2));
}

function calculate_electricity(frm) {
    const prev = parseFloat(frm.doc.previous_electricity_meter_reading);
    const next = parseFloat(frm.doc.new_electricity_meter_reading);
    if (isNaN(prev) || isNaN(next)) return;

    if (next < prev) {
        frappe.msgprint(__('New electricity reading ({0}) cannot be less than previous ({1}).', [next, prev]));
        frappe.validated = false;
        return;
    }
    frm.set_value('electricity_used_units', +(next - prev).toFixed(2));
}

function calculate_water(frm) {
    const prev = parseFloat(frm.doc.previous_water_meter_reading);
    const next = parseFloat(frm.doc.new_water_meter_reading);
    if (isNaN(prev) || isNaN(next)) return;

    if (next < prev) {
        frappe.msgprint(__('New water reading ({0}) cannot be less than previous ({1}).', [next, prev]));
        frappe.validated = false;
        return;
    }
    const volume = (next - prev) * WATER_METER_UNITS_TO_M3;
    frm.set_value('volume_of_water_used_m3', +volume.toFixed(2));
}

function fetch_previous_readings(frm) {
    frappe.call({
        method: 'frappe.client.get_list',
        args: {
            doctype: 'Reservoir Pumping Record',
            filters: {
                docstatus: ['<', 2],
                name: ['!=', frm.doc.name || '']
            },
            fields: ['new_electricity_meter_reading', 'new_water_meter_reading', 'date', 'modified'],
            order_by: 'date desc, modified desc',
            limit_page_length: 1
        },
        callback: function(res) {
            const last = res.message && res.message.length ? res.message[0] : null;
            const prev_elec  = last ? (last.new_electricity_meter_reading || 0) : 0;
            const prev_water = last ? (last.new_water_meter_reading || 0)       : 0;
            frm.set_value('previous_electricity_meter_reading', prev_elec);
            frm.set_value('previous_water_meter_reading',       prev_water);

            if (last) {
                frappe.show_alert({
                    message: __('Previous readings set from {0}', [frappe.datetime.str_to_user(last.date)]),
                    indicator: 'blue'
                }, 4);
            }

            if (frm.doc.new_electricity_meter_reading) calculate_electricity(frm);
            if (frm.doc.new_water_meter_reading)       calculate_water(frm);
        }
    });
}

function _time_to_minutes(t) {
    if (!t) return 0;
    const parts = String(t).split(':').map(Number);
    return (parts[0] || 0) * 60 + (parts[1] || 0);
}
