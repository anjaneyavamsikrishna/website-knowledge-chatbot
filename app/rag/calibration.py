"""Pick SIMILARITY_THRESHOLD from data instead of guessing.
Feed it the top similarity of in-scope questions and of clearly out-of-scope questions."""
from __future__ import annotations

from statistics import median


def suggest_threshold(in_scope: list[float], out_of_scope: list[float]) -> dict:
    lo_in, hi_out = min(in_scope), max(out_of_scope)
    gap = lo_in - hi_out
    result = {
        "in_scope_min": round(lo_in, 3), "in_scope_median": round(median(in_scope), 3),
        "out_of_scope_max": round(hi_out, 3), "gap": round(gap, 3),
    }
    if gap > 0:
        result["suggested"] = round(hi_out + gap / 2, 3)
        result["verdict"] = "clean separation: the midpoint is a good threshold"
    else:
        # overlap: favour answering real questions; the analyzer + 'answerable:false' + price check still protect
        result["suggested"] = round((lo_in + hi_out) / 2, 3)
        result["verdict"] = ("OVERLAP: embeddings alone cannot separate every case. Use the suggested value as a "
                             "compromise; the LLM intent check and grounded prompt handle the rest")
    return result
