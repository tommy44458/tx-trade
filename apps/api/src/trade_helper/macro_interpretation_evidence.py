"""Stable, as-of macro facts for an AI interpretation cache.

The fingerprint describes substantive published evidence, not polling timestamps,
calendar countdowns or an algorithmic market direction. Evidence keeps provenance
for the AI, while refresh noise is excluded from the cache identity.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from .db import connect
from .fomc_actual import (
    EASTERN,
    PARSER_VERSION,
    parse_target_range,
    release_day,
    verified_fomc_result,
)
from .macro_actuals import validate_snapshot_actual
from .news import SOURCE as FED_SOURCE
from .news import official_release_url
from .storage_codec import decode_row, load_json, to_datetime, utc_text

VERSION = "macro_interpretation_evidence_v1"
ACTUAL_KINDS = ("cpi", "employment", "pce", "gdp")


def _numeric(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("Statistic must be numeric")
    try:
        number = Decimal(str(value).replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError("Statistic must be numeric") from exc
    if not number.is_finite():
        raise ValueError("Statistic must be finite")
    # Numerically equal formatting must not invalidate an interpretation.
    return "0" if number == 0 else format(number.normalize(), "f")


def _visible_time(value: object, cutoff: datetime, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    observed = to_datetime(value)
    if observed > cutoff:
        raise ValueError("Macro fact was published or observed after the cutoff")
    return utc_text(observed)


def _statement_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("Official statement must be text")
    # The release scraper already removes HTML. Preserve every word of the
    # official body while excluding paragraph/spacing-only rendering changes.
    return " ".join(value.split()) or None


def _official_actual(record: dict, cutoff: datetime) -> tuple[dict, dict]:
    validate_snapshot_actual(record, cutoff)
    observed = _visible_time(record["ingested_at"], cutoff)
    published = _visible_time(record.get("published_at"), cutoff, required=False)
    semantic = {
        "type": "official_actual", "source": record["source"], "kind": record["kind"],
        "metric": record["metric"], "period": record["period"], "unit": record["unit"],
        "value": _numeric(record["value"]), "previous_value": _numeric(record.get("previous_value")),
        "source_url": record["source_url"], "published_at": published,
        "method": record["method"], "release_stage": record.get("release_stage"),
    }
    evidence = semantic | {
        "id": "actual:" + record["id"], "source_id": record["id"],
        "label": record["label"], "observed_at": observed,
        "version": record["version"], "content_hash": record["content_hash"],
        "time_basis": "official_release" if published else "first_observed",
        "evidence": record["evidence"],
    }
    return evidence, semantic


def _fomc_result(result: dict, cutoff: datetime) -> tuple[dict, dict]:
    if result.get("parser_version") != PARSER_VERSION or not result.get("document_id"):
        raise ValueError("FOMC result is not a verified official publication")
    url = result["source_url"]
    published = _visible_time(result["published_at"], cutoff)
    observed = _visible_time(result["ingested_at"], cutoff)
    if (release_day(url) is None or release_day(url) !=
            to_datetime(published).astimezone(EASTERN).date().isoformat()):
        raise ValueError("FOMC release date does not match its publication")
    parsed = parse_target_range(_statement_text(result.get("evidence_sentence")) or "")
    if parsed is None or any(parsed[field] != result.get(field)
                             for field in ("decision", "lower_pct", "upper_pct")):
        raise ValueError("FOMC numeric result differs from its official evidence")
    semantic = {
        "type": "fomc_target_range", "source": FED_SOURCE, "kind": "fomc",
        "decision": result["decision"], "lower_pct": _numeric(result["lower_pct"]),
        "upper_pct": _numeric(result["upper_pct"]), "unit": "target_range_percent",
        "source_url": url, "published_at": published, "release_date": release_day(url),
    }
    statement = _statement_text(result.get("statement_text"))
    if statement:
        semantic["statement_text"] = statement
    evidence = semantic | {
        "id": "fomc:" + release_day(url), "document_id": result["document_id"],
        "observed_at": observed, "content_hash": result.get("content_hash"),
        "parser_version": PARSER_VERSION, "evidence": result["evidence_sentence"],
    }
    return evidence, semantic


def _publication(item: dict, cutoff: datetime) -> tuple[dict, dict]:
    citation = item["citation"]
    url = citation["source_url"]
    if (item.get("verification") != "official_publication_fact" or
            item.get("event_type") != "fomc_statement_published" or
            item.get("claim_limit") !=
            "publication_timing_only_no_policy_direction_or_numeric_value" or
            citation.get("source") != FED_SOURCE or not official_release_url(url) or
            not citation.get("document_id")):
        raise ValueError("News event is not a verified official FOMC publication")
    published = _visible_time(item["published_at"], cutoff)
    observed = _visible_time(citation["ingested_at"], cutoff)
    _visible_time(item["first_seen_at"], cutoff)
    semantic = {
        "type": "official_publication", "source": FED_SOURCE, "kind": "fomc",
        "source_url": url, "published_at": published,
        "claim_limit": "publication_timing_only_no_policy_direction_or_numeric_value",
    }
    statement = _statement_text(item.get("statement_text"))
    if statement:
        semantic["statement_text"] = statement
    evidence = semantic | {
        "id": item["event_id"], "document_id": citation["document_id"],
        "title": citation["title"], "observed_at": observed,
        "content_hash": citation.get("content_hash"),
    }
    return evidence, semantic


def _consensus_fact(record: dict, cutoff: datetime) -> tuple[dict, dict]:
    """Accept only explicitly strategy-eligible, provenance-preserving records."""
    if record.get("usable_for_strategy") is not True:
        raise ValueError("Consensus is not verified for strategy use")
    url = urlsplit(record.get("source_url", ""))
    if (url.scheme != "https" or not url.hostname or url.username or url.password or
            not record.get("metric") or not record.get("period") or not record.get("unit") or
            not record.get("id") or not record.get("source")):
        raise ValueError("Consensus provenance, metric, period and unit are required")
    observed = _visible_time(record.get("observed_at") or record.get("ingested_at"), cutoff)
    published = _visible_time(record.get("published_at"), cutoff, required=False)
    # A released actual needs a published release time, not a future calendar date.
    actual = _numeric(record.get("actual", record.get("actual_value")))
    if actual is not None and published is None:
        raise ValueError("Consensus actual lacks a published release time")
    forecast = _numeric(record.get("forecast", record.get("expected_value")))
    previous = _numeric(record.get("previous", record.get("previous_value")))
    if actual is None and forecast is None:
        raise ValueError("Consensus has no usable actual or forecast")
    semantic = {
        "type": "market_consensus", "source": record["source"], "metric": record["metric"],
        "period": record["period"], "unit": record["unit"], "actual": actual,
        "forecast": forecast, "previous": previous, "published_at": published,
        "release_stage": record.get("release_stage"), "source_url": record["source_url"],
        "usable_for_strategy": True,
    }
    evidence = semantic | {
        "id": "consensus:" + record["id"], "source_id": record["id"],
        "observed_at": observed, "label": record.get("label"),
        "version": record.get("version"),
    }
    return evidence, semantic


def build_macro_interpretation_evidence(cutoff: datetime, events: dict | None = None,
                                        news: dict | None = None,
                                        *, consensus: dict | None = None) -> dict:
    """Use public snapshots or caller-provided fixtures; never calls an LLM or vault."""
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("Macro interpretation cutoff must include a timezone")
    cutoff = cutoff.astimezone(UTC)
    events, news = events or {}, news or {}
    consensus = consensus or events.get("market_consensus") or {}
    candidates = [(_official_actual, item) for item in events.get("official_actuals", [])]
    candidates += [(_fomc_result, item["official_result"]) for item in events.get("events", [])
                   if item.get("source") == "fed" and item.get("kind") == "fomc"
                   and item.get("official_result")]
    pack = news.get("evidence_pack") or {}
    candidates += [(_publication, item) for item in pack.get("events", [])]
    candidates += [(_consensus_fact, item) for item in consensus.get("items", [])]
    facts = {}
    excluded_count = 0
    for build, record in candidates:
        try:
            evidence, semantic = build(record, cutoff)
            canonical = json.dumps(semantic, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except (KeyError, TypeError, ValueError, InvalidOperation):
            excluded_count += 1
            continue
        prior = facts.get(canonical)
        # Duplicate re-fetches retain the earliest known observation provenance.
        if prior is None or evidence["observed_at"] < prior["observed_at"]:
            facts[canonical] = evidence
    evidence = sorted(facts.values(), key=lambda item: (item.get("published_at") or "", item["id"]))
    numeric_fomc_urls = {item["source_url"] for item in evidence if item["type"] == "fomc_target_range"}
    for canonical_fact in list(facts):
        item = facts[canonical_fact]
        if item["type"] == "official_publication" and item["source_url"] in numeric_fomc_urls:
            del facts[canonical_fact]
    semantic_facts = sorted(facts)
    evidence = sorted(facts.values(), key=lambda item: (item.get("published_at") or "", item["id"]))
    actual_kinds = sorted({item["kind"] for item in evidence if item["type"] == "official_actual"})
    usable_consensus = any(item["type"] == "market_consensus" for item in evidence)
    consensus_status = "available" if usable_consensus else "not_available"
    # Only substantive usability enters identity. Fresh check times and transient
    # supplier outages do not invalidate otherwise identical retained evidence.
    canonical = json.dumps({"version": VERSION, "evidence": semantic_facts,
                            "consensus_status": consensus_status},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        "version": VERSION, "fingerprint": hashlib.sha256(canonical.encode()).hexdigest(),
        "as_of": utc_text(cutoff), "evidence_count": len(evidence), "evidence": evidence,
        "coverage": {
            "official_actual_kinds": actual_kinds,
            "missing_actuals": [kind for kind in ACTUAL_KINDS if kind not in actual_kinds],
            "consensus_status": consensus_status, "excluded_count": excluded_count,
            "freshness": [
                {"id": item["id"], "reference_at": item.get("published_at") or item["observed_at"],
                 "within_last_month": to_datetime(item.get("published_at") or item["observed_at"])
                 >= cutoff - timedelta(days=30)}
                for item in evidence
            ],
            "source_status": {"events": events.get("source_status", {}),
                              "actuals": events.get("actual_source_status", {}),
                              "news": news.get("source_status", {})},
            "interpretation_limit": "AI decides macro direction; calendar dates alone are not results.",
        },
    }


def load_macro_interpretation_evidence(cutoff: datetime) -> dict:
    """Load a fixed scope of known releases; rolling calendar windows never evict it.

    Keep the latest three periods of each actual metric and the latest two
    verified FOMC publications. Old releases remain dated background evidence
    until a newer release or a substantive revision replaces them.
    """
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("Macro interpretation cutoff must include a timezone")
    cutoff = cutoff.astimezone(UTC)
    at = utc_text(cutoff)
    with connect(readonly=True) as db:
        actual_rows = db.execute(
            "WITH revisions AS (SELECT source,metric,period,version,ingested_at,content_hash,payload,"
            "ROW_NUMBER() OVER (PARTITION BY source,metric,period ORDER BY version DESC) AS revision "
            "FROM macro_actual_versions WHERE ingested_at<=?), periods AS ("
            "SELECT *,ROW_NUMBER() OVER (PARTITION BY source,metric ORDER BY period DESC) AS period_rank "
            "FROM revisions WHERE revision=1) SELECT * FROM periods WHERE period_rank<=3",
            (at,),
        ).fetchall()
        news_rows = db.execute(
            "WITH ranked AS (SELECT id,source,source_url,title,body,published_at,ingested_at,metadata,"
            "content_hash,ROW_NUMBER() OVER (PARTITION BY source,source_url "
            "ORDER BY ingested_at DESC,id DESC) AS rank FROM news_documents "
            "WHERE source=? AND published_at<=? AND ingested_at<=?) SELECT * FROM ranked "
            "WHERE rank=1 ORDER BY published_at DESC,source_url",
            (FED_SOURCE, at, at),
        ).fetchall()
        consensus_row = db.execute(
            "SELECT result FROM consensus_provider_checks WHERE reserved_at<=? "
            "ORDER BY reserved_at DESC,id DESC LIMIT 1", (at,),
        ).fetchone()
    actuals = []
    for row in actual_rows:
        payload = load_json(row["payload"])
        actuals.append(payload | {
            "id": f"{row['source']}:{row['metric']}:{row['period']}", "version": row["version"],
            "ingested_at": row["ingested_at"], "content_hash": row["content_hash"],
        })
    publications, fomc_events = [], []
    verified_count = 0
    for raw in news_rows:
        row = decode_row(raw, json_fields=("metadata",), time_fields=("published_at", "ingested_at"))
        meta = row["metadata"]
        if (not official_release_url(row["source_url"]) or
                meta.get("kind") != "fomc_statement" or meta.get("origin") != "official_release" or
                meta.get("content_quality") != "body" or
                row["title"].casefold() != "federal reserve issues fomc statement" or
                release_day(row["source_url"]) != row["published_at"].astimezone(EASTERN).date().isoformat()):
            continue
        verified_count += 1
        statement = _statement_text(row["body"])
        result = verified_fomc_result(row | {"body": statement or ""}, cutoff)
        if result:
            fomc_events.append({"source": "fed", "kind": "fomc",
                                "official_result": result | {"statement_text": statement}})
        else:
            publications.append({
                "event_id": "publication:" + hashlib.sha256(row["source_url"].encode()).hexdigest()[:24],
                "verification": "official_publication_fact", "event_type": "fomc_statement_published",
                "claim_limit": "publication_timing_only_no_policy_direction_or_numeric_value",
                "published_at": utc_text(row["published_at"]),
                "first_seen_at": utc_text(row["ingested_at"]),
                "statement_text": statement,
                "citation": {"document_id": row["id"], "source": FED_SOURCE,
                             "source_url": row["source_url"], "title": row["title"],
                             "ingested_at": utc_text(row["ingested_at"]),
                             "content_hash": row["content_hash"]},
            })
        if verified_count == 2:
            break
    consensus = {}
    if consensus_row:
        candidate = load_json(consensus_row["result"])
        try:
            _visible_time(candidate.get("checked_at") or candidate.get("observed_at"), cutoff)
            if candidate.get("usable_for_strategy") is True:
                consensus = candidate
        except (TypeError, ValueError):
            pass
    snapshot = build_macro_interpretation_evidence(
        cutoff, {"official_actuals": actuals, "events": fomc_events},
        {"evidence_pack": {"events": publications}}, consensus=consensus,
    )
    snapshot["coverage"]["scope"] = {"actual_periods_per_metric": 3, "latest_fomc_publications": 2,
                                     "calendar_window_eviction": False}
    return snapshot
