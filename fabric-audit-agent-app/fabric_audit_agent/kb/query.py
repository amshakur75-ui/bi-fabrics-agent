QUERY_PLAYBOOKS = {
    "query.mdx-crossjoin": {
        "rootCause": "A heavy nested Hierarchize/CrossJoin (or GrandTotal/CrossJoin) MDX matrix "
                     "shape is recurring — a heavy matrix-visual pattern in the report/model.",
        "fixes": [
            "Reduce the matrix visual's row/column cardinality or remove nested hierarchies.",
            "Disable subtotals/grand totals on the matrix where they aren't needed.",
            "Consider a flatter table visual or a pre-aggregated measure instead.",
        ],
        "owner": "Report author",
    },
    "query.dax-antipattern": {
        "rootCause": "A recurring DAX anti-pattern (nested iterators, whole-table FILTER, etc.) "
                     "is running repeatedly — likely a shared measure/report design issue.",
        "fixes": [
            "Rewrite the measure to avoid nested row-context iterators over large tables.",
            "Replace whole-table FILTER() with a filtered CALCULATE() context where possible.",
            "Profile with DAX Studio to confirm the fix reduces storage-engine/formula-engine cost.",
        ],
        "owner": "Report author + Power BI team",
    },
}
