"""Descriptive checks for a frozen, stratified sample of recorded readings."""

from collections import Counter


def sensitivity(study, summary):
    by_year = Counter(doc["published_at"][:4] for doc in study["documents"])
    result = {}
    for criterion, rubric in summary["criteria"].items():
        states = rubric["states"]
        flagged = states["flagged"]
        unresolved = states["uncertain"] + states["incomplete"]
        year_flags = Counter(
            doc["published_at"][:4]
            for doc in study["documents"]
            if summary["article_states"][doc["id"]][criterion] == "flagged"
        )
        exclusions = [
            {
                "excluded_year": year,
                "flagged": flagged - year_flags[year],
                "articles": summary["articles"] - count,
            }
            for year, count in sorted(by_year.items())
            if count < summary["articles"]
        ]
        exclusions.sort(key=lambda row: row["flagged"] / row["articles"])
        result[criterion] = {
            "articles": summary["articles"],
            "observed_flags": flagged,
            "uncertain_without_flag": states["uncertain"],
            "incomplete_without_flag": states["incomplete"],
            "if_all_unresolved_flagged": flagged + unresolved,
            "year_omissions": exclusions,
        }
    return result
