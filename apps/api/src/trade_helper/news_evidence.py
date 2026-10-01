"""Conservative, as-of news event pack for a strategy analysis.

Only a verified official FOMC publication fact is strategy-eligible here.
Jev labels and SEC RSS excerpts remain review candidates, not trading signals.
"""

import hashlib
import re
from collections import Counter
from datetime import UTC, datetime, timedelta

from .db import connect
from .news import SOURCE as FED_SOURCE
from .news import official_release_url as fed_release_url
from .sec_news import SOURCE as SEC_SOURCE
from .sec_news import official_release_url as sec_release_url
from .storage_codec import decode_row, utc_text

VERSION = "news_evidence_pack_v2"
SOURCE_TTL = {FED_SOURCE: timedelta(minutes=20), SEC_SOURCE: timedelta(minutes=30)}


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold()))


def _event_id(anchor: dict) -> str:
    stable_url = f"{anchor['source']}:{anchor['source_url']}"
    return "event_" + hashlib.sha256(stable_url.encode()).hexdigest()[:24]


def _trusted_publication(row: dict) -> bool:
    meta = row["metadata"]
    return (row["source"] == FED_SOURCE and fed_release_url(row["source_url"]) and
            meta.get("kind") == "fomc_statement" and
            meta.get("origin") == "official_release" and
            meta.get("content_quality") == "body" and
            meta.get("model_use_allowed") is True)


def _reason(row: dict) -> str:
    if row["source"] == FED_SOURCE and not fed_release_url(row["source_url"]):
        return "invalid_source_url"
    if row["source"] == SEC_SOURCE and not sec_release_url(row["source_url"]):
        return "invalid_source_url"
    if row["source"] == SEC_SOURCE:
        return "sec_excerpt_not_full_release"
    if row["classification_id"] is None:
        return "pending_classification"
    return "not_promoted_for_strategy"


def _class_status(row: dict) -> str:
    return "classified_unreviewed" if row["classification_id"] else "pending"


def build_news_evidence_pack(cutoff: datetime, market_id: str,
                             lookback_hours: int = 24 * 30) -> dict:
    if cutoff.tzinfo is None or not market_id or not 1 <= lookback_hours <= 24 * 30:
        raise ValueError("Invalid news evidence cutoff, market or lookback")
    cutoff = cutoff.astimezone(UTC)
    with connect(readonly=True) as db:
        checks = db.execute(
            "WITH ranked AS (SELECT source,status,checked_at,ROW_NUMBER() OVER "
            "(PARTITION BY source ORDER BY checked_at DESC,id DESC) AS rank "
            "FROM news_source_checks WHERE checked_at<=? AND source IN (?,?)) "
            "SELECT source,status,checked_at FROM ranked WHERE rank=1 ORDER BY source",
            (utc_text(cutoff), FED_SOURCE, SEC_SOURCE),
        ).fetchall()
        rows = db.execute(
            "WITH visible AS (SELECT id,source,source_url,title,body,published_at,ingested_at,"
            "market_ids,metadata,content_hash,"
            "MIN(ingested_at) OVER (PARTITION BY source,source_url) AS first_seen_at,"
            "ROW_NUMBER() OVER (PARTITION BY source,source_url "
            "ORDER BY ingested_at DESC,id DESC) AS rank "
            "FROM news_documents WHERE ingested_at<=? AND published_at<=?), "
            "classes AS (SELECT id,document_id,classified_at,model,answers,"
            "ROW_NUMBER() OVER (PARTITION BY document_id ORDER BY classified_at DESC,id DESC) "
            "AS rank FROM news_classifications WHERE classified_at<=?) "
            "SELECT n.*,c.id AS classification_id,c.classified_at,c.model,c.answers "
            "FROM visible n LEFT JOIN classes c ON c.document_id=n.id AND c.rank=1 "
            "WHERE n.rank=1 AND n.published_at>=? AND "
            "(EXISTS (SELECT 1 FROM json_each(n.market_ids) WHERE value=?) OR n.source IN (?,?)) "
            "ORDER BY n.ingested_at DESC,n.id DESC LIMIT 501",
            (utc_text(cutoff), utc_text(cutoff), utc_text(cutoff),
             utc_text(cutoff - timedelta(hours=lookback_hours)), market_id, FED_SOURCE, SEC_SOURCE),
        ).fetchall()
    checks = [decode_row(row, time_fields=("checked_at",)) for row in checks]
    rows = [decode_row(row, json_fields=("market_ids", "metadata", "answers"),
                       time_fields=("published_at", "ingested_at", "first_seen_at", "classified_at"))
            for row in rows]
    source_checks = {row["source"]: row for row in checks}
    source_status = {}
    for source, ttl in SOURCE_TTL.items():
        check = source_checks.get(source)
        source_status[source] = ("ok" if check and check["status"] == "ok" and
                                 cutoff - check["checked_at"] <= ttl else "offline")
    overflow = len(rows) > 500
    groups: list[list[dict]] = []
    fingerprints: dict[tuple[str, str], list[int]] = {}
    for row in rows[:500]:
        row = dict(row)
        fingerprint = (_normalized(row["title"]), _normalized(row["body"]))
        matches = fingerprints.get(fingerprint, [])
        matching = next((index for index in matches if abs(
            (row["published_at"] - groups[index][0]["published_at"]).total_seconds()
        ) <= 24 * 3600), None)
        if matching is None:
            fingerprints.setdefault(fingerprint, []).append(len(groups))
            groups.append([row])
        else:
            groups[matching].append(row)
    included = []
    excluded = []
    for group in groups:
        anchor = min(group, key=lambda row: (row["first_seen_at"], row["source_url"]))
        event_id = _event_id(anchor)
        verified = [row for row in group if _trusted_publication(row)]
        if verified:
            chosen = min(verified, key=lambda row: (row["published_at"], row["source_url"]))
            included.append({
                "event_id": event_id, "verification": "official_publication_fact",
                "event_type": "fomc_statement_published",
                "published_at": chosen["published_at"].isoformat(),
                "first_seen_at": anchor["first_seen_at"].isoformat(),
                "citation": {"document_id": chosen["id"], "source": chosen["source"],
                             "source_url": chosen["source_url"], "title": chosen["title"],
                             "content_hash": chosen["content_hash"],
                             "ingested_at": chosen["ingested_at"].isoformat()},
                "allowed_claim": "Federal Reserve published an FOMC statement",
                "claim_limit": "publication_timing_only_no_policy_direction_or_numeric_value",
                "duplicate_document_count": len(group),
            })
        else:
            reason = _reason(group[0])
            excluded.append({"event_id": event_id, "reason_code": reason,
                             "classification_status": "classified_unreviewed" if any(
                                 row["classification_id"] for row in group) else "pending",
                             "document_count": len(group),
                             "document_versions": [{
                                 "document_id": row["id"], "source": row["source"],
                                 "source_url": row["source_url"],
                                 "content_hash": row["content_hash"],
                                 "published_at": row["published_at"].isoformat(),
                                 "ingested_at": row["ingested_at"].isoformat(),
                                 "classification_id": row["classification_id"],
                                 "classified_at": row["classified_at"].isoformat() if
                                 row["classified_at"] else None,
                             } for row in group[:2]]})
    included.sort(key=lambda item: item["published_at"], reverse=True)
    reasons = dict(sorted(Counter(item["reason_code"] for item in excluded).items()))
    return {"version": VERSION, "cutoff": cutoff.isoformat(), "market_id": market_id,
            "lookback_hours": lookback_hours,
            "coverage_status": "partial" if all(value == "ok" for value in source_status.values())
            and not overflow else "unknown",
            "source_status": source_status, "grouping_rule": "exact_title_body_within_24h",
            "review_status": "jev_not_calibrated",
            "events": included[:8], "eligible_event_count": len(included),
            "omitted_eligible_count": max(0, len(included) - 8),
            "excluded_count": len(excluded), "exclusion_reasons": reasons,
            "excluded_audit": excluded[:100], "audit_omitted_count": max(0, len(excluded) - 100),
            "query_truncated": overflow}


def validate_news_evidence_pack(pack: dict, *, cutoff: datetime, market_id: str) -> None:
    """Reject future knowledge and unsupported citations in saved reports."""
    if (pack.get("version") != VERSION or pack.get("market_id") != market_id or
            datetime.fromisoformat(pack["cutoff"]) != cutoff or
            pack.get("grouping_rule") != "exact_title_body_within_24h" or
            pack.get("review_status") != "jev_not_calibrated" or
            pack.get("coverage_status") not in {"partial", "unknown"} or
            not 1 <= pack.get("lookback_hours", 0) <= 24 * 30 or
            len(pack.get("events", [])) > 8 or pack.get("eligible_event_count", 0) <
            len(pack.get("events", [])) or
            pack.get("excluded_count", -1) < len(pack.get("excluded_audit", [])) or
            sum(pack.get("exclusion_reasons", {}).values()) != pack.get("excluded_count")):
        raise ValueError("Invalid news evidence pack identity or limit")
    seen = set()
    for event in pack["events"]:
        citation = event["citation"]
        published = datetime.fromisoformat(event["published_at"])
        seen_at = datetime.fromisoformat(event["first_seen_at"])
        ingested = datetime.fromisoformat(citation["ingested_at"])
        if (event["event_id"] in seen or published > cutoff or seen_at > cutoff or
                ingested > cutoff or ingested < seen_at or
                cutoff - published > timedelta(hours=pack["lookback_hours"]) or
                not re.fullmatch(r"event_[0-9a-f]{24}", event["event_id"]) or
                not citation.get("document_id") or not citation.get("title") or
                event.get("allowed_claim") !=
                "Federal Reserve published an FOMC statement" or
                event.get("verification") != "official_publication_fact" or
                event.get("event_type") != "fomc_statement_published" or
                event.get("claim_limit") !=
                "publication_timing_only_no_policy_direction_or_numeric_value" or
                citation.get("source") != FED_SOURCE or
                not fed_release_url(citation.get("source_url", "")) or
                not re.fullmatch(r"[0-9a-f]{64}", citation.get("content_hash", ""))):
            raise ValueError("News evidence contains an unsupported or future citation")
        seen.add(event["event_id"])
    for excluded in pack.get("excluded_audit", []):
        if (excluded.get("reason_code") not in {
                "invalid_source_url", "sec_excerpt_not_full_release",
                "pending_classification", "not_promoted_for_strategy"} or
                excluded.get("event_id") in seen or
                not 1 <= len(excluded.get("document_versions", [])) <= 2 or
                excluded.get("document_count", 0) < len(excluded["document_versions"])):
            raise ValueError("Invalid excluded news audit record")
        seen.add(excluded["event_id"])
        for document in excluded["document_versions"]:
            published = datetime.fromisoformat(document["published_at"])
            ingested = datetime.fromisoformat(document["ingested_at"])
            classified = (datetime.fromisoformat(document["classified_at"]) if
                          document.get("classified_at") else None)
            valid_url = (fed_release_url(document["source_url"]) if
                         document["source"] == FED_SOURCE else
                         sec_release_url(document["source_url"]) if
                         document["source"] == SEC_SOURCE else False)
            if (published > cutoff or ingested > cutoff or
                    (classified and classified > cutoff) or
                    (not valid_url and excluded["reason_code"] != "invalid_source_url") or
                    not re.fullmatch(r"[0-9a-f]{64}", document["content_hash"])):
                raise ValueError("Excluded news audit cites future or unapproved content")
