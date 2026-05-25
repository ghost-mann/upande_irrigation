// Irrigation Scheduler — form behavior
//   - Status headline reflecting last_run_status
//   - "Run Now" primary button calling upande_irrigation.api.scheduler.run
//   - "View Last Run" / "View All Runs" shortcuts when total_runs > 0
//
// Was previously the "Irrigation Scheduler - Run Now Button" Client Script.

frappe.ui.form.on('Irrigation Scheduler', {
    refresh: function(frm) {
        // ── Status headline ──────────────────────────────────────
        if (frm.doc.last_run_status === 'Success') {
            frm.dashboard.set_headline(
                '<div style="padding:8px 0">' +
                '<span class="indicator-pill green">Last run: Success</span> ' +
                '<span style="color:#666;margin-left:8px">' +
                (frm.doc.last_run_summary || '') + '</span>' +
                '</div>'
            );
        } else if (frm.doc.last_run_status === 'Partial') {
            frm.dashboard.set_headline(
                '<div style="padding:8px 0">' +
                '<span class="indicator-pill orange">Last run: Partial</span> ' +
                '<span style="color:#666;margin-left:8px">' +
                (frm.doc.last_run_summary || '') + '</span>' +
                '</div>'
            );
        } else if (frm.doc.last_run_status === 'Failed' || frm.doc.last_run_status === 'Aborted') {
            frm.dashboard.set_headline(
                '<div style="padding:8px 0">' +
                '<span class="indicator-pill red">Last run: ' + frm.doc.last_run_status + '</span> ' +
                '<span style="color:#666;margin-left:8px">' +
                (frm.doc.last_run_summary || '') + '</span>' +
                '</div>'
            );
        }

        // ── Run Now button ───────────────────────────────────────
        frm.add_custom_button(__('Run Now'), function() {
            frappe.confirm(
                'Run the irrigation scheduler now?<br><br>' +
                'This will create planners for the configured planning window ' +
                '(<b>' + (frm.doc.plan_for || 'Current Week') + '</b>). ' +
                'Existing planners will be skipped.',
                function() {
                    frappe.call({
                        method: 'upande_irrigation.api.scheduler.run',
                        args: { triggered_by: 'Manual' },
                        freeze: true,
                        freeze_message: __('Running scheduler — this may take 30-60 seconds...'),
                        callback: function(r) {
                            if (r.message && r.message.ok) {
                                const m = r.message;
                                let color = 'green';
                                if (m.status === 'Partial') color = 'orange';
                                if (m.status === 'Failed' || m.status === 'Aborted') color = 'red';

                                frappe.msgprint({
                                    title: __('Scheduler Run Complete'),
                                    message:
                                        '<div style="font-size:14px;line-height:1.8">' +
                                        '<b>Status:</b> <span class="indicator-pill ' + color + '">' +
                                            m.status + '</span><br>' +
                                        '<b>Run ID:</b> <a href="/app/irrigation-scheduler-run/' +
                                            m.run + '">' + m.run + '</a><br>' +
                                        '<b>Created:</b> ' + m.created + ' planners<br>' +
                                        '<b>Skipped:</b> ' + m.skipped + ' (already existed)<br>' +
                                        '<b>Failed:</b> ' + m.failed + '<br>' +
                                        '<br><i>' + m.summary + '</i>' +
                                        '</div>',
                                    indicator: color,
                                    primary_action: {
                                        label: __('View Run Log'),
                                        action: function() {
                                            frappe.set_route('Form', 'Irrigation Scheduler Run', m.run);
                                        }
                                    }
                                });
                                frm.reload_doc();
                            } else {
                                frappe.msgprint({
                                    title: __('Scheduler Failed'),
                                    message: (r.message && r.message.reason) ||
                                             (r.message && r.message.error) ||
                                             'Unknown error. Check Error Log.',
                                    indicator: 'red'
                                });
                            }
                        },
                        error: function() {
                            frappe.msgprint({
                                title: __('Scheduler Error'),
                                message: 'The scheduler endpoint returned an error. ' +
                                         'Check browser console and Error Log for details.',
                                indicator: 'red'
                            });
                        }
                    });
                }
            );
        }, null, 'primary');

        // ── Run-log shortcuts ────────────────────────────────────
        if (frm.doc.total_runs && frm.doc.total_runs > 0) {
            frm.add_custom_button(__('View Last Run'), function() {
                frappe.db.get_list('Irrigation Scheduler Run', {
                    fields: ['name'],
                    order_by: 'creation desc',
                    limit: 1
                }).then(rs => {
                    if (rs.length > 0) {
                        frappe.set_route('Form', 'Irrigation Scheduler Run', rs[0].name);
                    } else {
                        frappe.msgprint('No run logs found.');
                    }
                });
            }, __('Run Log'));

            frm.add_custom_button(__('View All Runs'), function() {
                frappe.set_route('List', 'Irrigation Scheduler Run');
            }, __('Run Log'));
        }
    }
});
