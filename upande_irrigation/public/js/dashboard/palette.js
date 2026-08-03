/* Series colours for the planning charts.
 *
 * SERIES is the upande_crm ramp, unchanged. Reusing it keeps one categorical
 * order across the two apps an operator moves between, and it validates:
 *
 *   node scripts/validate_palette.js \
 *     "#3268c4,#c69210,#03958c,#b5501f,#8d4fa0,#5f8d33" --mode light --pairs all
 *
 *   [PASS] Lightness band        all 6 inside L 0.43-0.77
 *   [PASS] Chroma floor          all 6 >= 0.1
 *   [PASS] CVD separation        worst all-pairs #b5501f<->#03958c dE 13.3 (deutan)
 *   [PASS] Normal-vision floor   worst all-pairs #b5501f<->#c69210 dE 16.8
 *   [WARN] Contrast vs surface   #c69210 at 2.72:1
 *
 * The one WARN obligates relief rather than being dismissable, so every chart
 * that uses these colours also carries a legend, direct labels on the marks it
 * can fit, and a "Table" toggle that renders the same numbers as text.
 *
 * Assigned in fixed order and never cycled: a pump keeps its colour when a
 * filter removes another pump, and colour follows the pump, never its rank. A
 * seventh pump folds into OTHER rather than getting a generated hue.
 *
 * Dark mode is not implemented on this page. Noted rather than glossed: this ramp
 * would need re-stepping first — #c69210 sits above the lightness band against a
 * dark surface, so an automatic flip would be wrong.
 */

export const SERIES = ["#3268c4", "#c69210", "#03958c", "#b5501f", "#8d4fa0", "#5f8d33"];

export const OTHER = "#6b6862";

/* Reserved. Never reused as "series 7" — these mean a state, not an identity,
 * and they always ship beside a label rather than carrying meaning alone. */
export const STATUS = {
	ok: "var(--ui-ok)",
	warn: "var(--ui-warn)",
	hot: "var(--ui-hot)",
	quiet: "rgba(10,10,10,.10)",
};

/* Demand → delivered → carried is a sequence, not a set of peers, so it gets a
 * fixed meaning-bearing triple instead of the categorical ramp. */
export const BALANCE = {
	demand: "#3268c4",
	delivered: "var(--ui-ok)",
	carried: "var(--ui-hot)",
};

/** Stable colour for a named key, in first-seen order. */
export function assign(keys) {
	const out = new Map();
	(keys || []).forEach((k, i) => {
		out.set(k, i < SERIES.length ? SERIES[i] : OTHER);
	});
	return out;
}
