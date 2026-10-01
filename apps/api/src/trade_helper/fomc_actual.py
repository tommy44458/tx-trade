"""Conservative FOMC target-range extraction from verified official release bodies."""

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .news import SOURCE, official_release_url
from .storage_codec import decode_row, utc_text

PARSER_VERSION = "fomc_target_range_v1"
EASTERN = ZoneInfo("America/New_York")
_NUMBER = r"(?:\d+(?:\.\d+)?(?:[ -]\d+/\d+)?|\d+/\d+)"
_DECISION = re.compile(
    rf"\bThe Committee decided to (raise|lower|maintain) the target range "
    rf"for the federal funds rate(?:\s+by\s+[^.;]{{1,55}}?)?\s+(to|at)\s+"
    rf"({_NUMBER})\s+to\s+({_NUMBER})\s+percent\b", re.IGNORECASE,
)
_RELEASE = re.compile(r"/newsevents/pressreleases/monetary(\d{8})[a-z]\.htm$")


def release_day(url: str) -> str | None:
    if not official_release_url(url):
        return None
    match = _RELEASE.fullmatch(urlsplit(url).path)
    if match is None:
        return None
    raw = match.group(1)
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"


def _percent(raw: str) -> Decimal:
    raw = raw.replace("-", " ")
    parts = raw.split()
    if len(parts) == 2:
        return Decimal(parts[0]) + Decimal(Fraction(parts[1]).numerator) / Decimal(Fraction(parts[1]).denominator)
    if "/" in raw:
        fraction = Fraction(raw)
        return Decimal(fraction.numerator) / Decimal(fraction.denominator)
    return Decimal(raw)


def parse_target_range(body: str) -> dict | None:
    """Return only one explicit, internally consistent decision sentence."""
    normalized = body.replace("\u2011", "-").replace("\u2010", "-").replace("\u00a0", " ")
    if len(re.findall(r"\bThe Committee decided to\b[^.;]*\btarget range\b", normalized,
                      re.IGNORECASE)) != 1:
        return None
    hits = []
    for paragraph in normalized.split("\n\n"):
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            matches = list(_DECISION.finditer(sentence))
            if len(matches) != 1:
                continue
            match = matches[0]
            action, preposition = match.group(1).lower(), match.group(2).lower()
            if (action == "maintain") != (preposition == "at"):
                continue
            try:
                lower, upper = _percent(match.group(3)), _percent(match.group(4))
            except (ValueError, ZeroDivisionError):
                continue
            if not (Decimal(0) <= lower < upper <= Decimal(20) and upper - lower <= Decimal(1)):
                continue
            hits.append({"decision": action, "lower_pct": str(lower), "upper_pct": str(upper),
                         "evidence_sentence": " ".join(sentence.strip().split())})
    return hits[0] if len(hits) == 1 else None


def verified_fomc_result(row: dict, cutoff: datetime) -> dict | None:
    """Require a same-day, as-of official FOMC statement body and RSS timestamp."""
    metadata = row["metadata"]
    url = row["source_url"]
    if (row["source"] != SOURCE or not official_release_url(url) or
            metadata.get("kind") != "fomc_statement" or
            metadata.get("origin") != "official_release" or
            metadata.get("content_quality") != "body" or
            row["title"].casefold() != "federal reserve issues fomc statement"):
        return None
    release_date = release_day(url)
    published = row["published_at"]
    ingested = row["ingested_at"]
    if (release_date is None or published.tzinfo is None or ingested.tzinfo is None or
            published > cutoff or ingested > cutoff or
            release_date != published.astimezone(EASTERN).date().isoformat()):
        return None
    result = parse_target_range(row["body"])
    if result is None:
        return None
    return result | {"parser_version": PARSER_VERSION, "document_id": row["id"],
                     "source_url": url, "content_hash": row["content_hash"],
                     "published_at": published.astimezone(UTC).isoformat(),
                     "ingested_at": ingested.astimezone(UTC).isoformat()}


def fomc_results_as_of(db, cutoff: datetime) -> dict[str, dict]:
    rows = db.execute(
        "WITH ranked AS (SELECT id,source,source_url,title,body,published_at,ingested_at,metadata,"
        "content_hash,ROW_NUMBER() OVER (PARTITION BY source_url ORDER BY ingested_at DESC,id DESC) "
        "AS rank FROM news_documents WHERE source=? AND published_at<=? AND ingested_at<=? "
        "AND published_at>=?) SELECT * FROM ranked WHERE rank=1 ORDER BY source_url",
        (SOURCE, utc_text(cutoff), utc_text(cutoff), utc_text(cutoff - timedelta(days=32))),
    ).fetchall()
    results: dict[str, list[dict]] = {}
    for row in rows:
        row = decode_row(row, json_fields=("metadata",), time_fields=("published_at", "ingested_at"))
        result = verified_fomc_result(row, cutoff)
        if result:
            day = datetime.fromisoformat(result["published_at"]).astimezone(EASTERN).date().isoformat()
            results.setdefault(day, []).append(result)
    return {day: matches[0] for day, matches in results.items() if len(matches) == 1}
