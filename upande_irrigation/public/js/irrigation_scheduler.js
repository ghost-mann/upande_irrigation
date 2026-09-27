// Irrigation Scheduler — form behaviour.
//
// Since 2026-09-28 the scheduler generates a daily Irrigation Run Sheet per farm
// at `run_hour` (from the daily water balance); the weekly planner fields are
// hidden. "Generate today's run sheet" runs it now.

frappe.ui.form.on('Irrigation Scheduler', {
    refresh(frm) {
        ['week_starts_on', 'plan_for', 'run_day_of_week', 'skip_existing', 'commit_on_partial_failure',
         'max_errors_before_abort', 'last_run_status', 'last_run_at', 'last_run_summary', 'total_runs']
            .forEach((f) => frm.set_df_property(f, 'hidden', 1));
        frm.set_df_property('run_hour', 'description', __('Hour of the day (0–23) the morning run sheet is generated.'));

        frm.add_custom_button(__("Generate today's run sheet"), () => {
            frappe.call({
                method: 'upande_irrigation.api.runsheet.generate',
                freeze: true,
                freeze_message: __('Updating the water balance and planning today…'),
                callback(r) {
                    const rows = (r.message || []).map((x) =>
                        `<li><a href="/app/irrigation-run-sheet/${encodeURIComponent(x.run_sheet)}">${frappe.utils.escape_html(x.farm)}</a>: ${frappe.utils.escape_html(x.summary)}</li>`).join('');
                    frappe.msgprint({ title: __('Run sheet ready'), message: `<ul>${rows || '<li>No farm has block profiles yet.</li>'}</ul>`, indicator: 'green' });
                },
            });
        }).addClass('btn-primary');
        frm.add_custom_button(__('Open the irrigation plan'), () => { window.location.href = '/upande-irrigation#planner'; });
    },
});
