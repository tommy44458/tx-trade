"""As-of, one-month macro evidence assembled from verified official releases."""

from datetime import UTC, datetime, timedelta

VERSION = "macro_context_v2"


def build_macro_context(cutoff: datetime, events: dict | None, news: dict | None) -> dict:
    if cutoff.tzinfo is None:
        raise ValueError("Macro cutoff must include a timezone")
    cutoff = cutoff.astimezone(UTC)
    start = cutoff - timedelta(days=30)
    indicators = []
    if events:
        for event in events.get("events", []):
            result = event.get("official_result")
            if event.get("source") != "fed" or event.get("kind") != "fomc" or not result:
                continue
            published = datetime.fromisoformat(result["published_at"]).astimezone(UTC)
            ingested = datetime.fromisoformat(result["ingested_at"]).astimezone(UTC)
            if start <= published <= cutoff and ingested <= cutoff:
                indicators.append({
                    "id": "fomc:" + event["id"],
                    "kind": "fomc_target_range",
                    "decision": result["decision"],
                    "lower_pct": result["lower_pct"],
                    "upper_pct": result["upper_pct"],
                    "published_at": result["published_at"],
                    "source_url": result["source_url"],
                    "document_id": result["document_id"],
                })
    kinds_with_actuals = set()
    latest_actuals = []
    if events:
        for actual in events.get("official_actuals", []):
            available = datetime.fromisoformat(actual["ingested_at"]).astimezone(UTC)
            published_value = actual.get("published_at")
            published = (datetime.fromisoformat(published_value).astimezone(UTC)
                         if published_value else None)
            reference = published or available
            if not (reference <= cutoff and available <= cutoff):
                continue
            latest_actuals.append({
                "id": "actual:" + actual["id"], "kind": actual["kind"],
                "label": actual["label"], "period": actual["period"],
                "value": actual["value"], "unit": actual["unit"],
                "source_url": actual["source_url"],
                "available_at": actual["ingested_at"],
                "published_at": published_value,
                "within_month": reference >= start,
            })
            if reference < start:
                continue
            kinds_with_actuals.add(actual["kind"])
            indicators.append({
                "id": "actual:" + actual["id"], "kind": actual["kind"],
                "metric": actual["metric"], "label": actual["label"],
                "period": actual["period"], "value": actual["value"],
                "previous_value": actual.get("previous_value"), "unit": actual["unit"],
                "published_at": published_value or actual["ingested_at"],
                "time_basis": "official_release" if published_value else "first_observed",
                "ingested_at": actual["ingested_at"],
                "source_url": actual["source_url"], "source": actual["source"],
                "method": actual["method"], "version": actual["version"],
                "content_hash": actual["content_hash"],
            })
    indicators.sort(key=lambda item: item["published_at"], reverse=True)
    pack = (news or {}).get("evidence_pack") or {}
    publications = [
        {
            "id": item["event_id"], "published_at": item["published_at"],
            "title": item["citation"]["title"],
            "source_url": item["citation"]["source_url"],
            "claim_limit": "publication_timing_only",
        }
        for item in pack.get("events", [])
        if start <= datetime.fromisoformat(item["published_at"]).astimezone(UTC) <= cutoff
    ]
    return {
        "version": VERSION, "from": start.isoformat(), "cutoff": cutoff.isoformat(),
        "coverage": "partial" if indicators else "insufficient",
        "directional_evidence": indicators,
        "latest_actuals": latest_actuals,
        "official_publications": publications,
        "missing_actuals": [kind for kind in ("cpi", "employment", "pce", "gdp")
                            if kind not in kinds_with_actuals],
        "source_status": {
            "events": (events or {}).get("source_status", {}),
            "news": (news or {}).get("source_status", {}),
            "actuals": (events or {}).get("actual_source_status", {}),
        },
        "interpretation_limit": (
            "Verified official releases are macro context, not a crypto price prediction. "
            "BLS index changes are calculated from published series and first known at ingestion; "
            "BEA release values have official publication timestamps. "
            "No market consensus or surprise is available; scheduled releases alone are not results."
        ),
    }
