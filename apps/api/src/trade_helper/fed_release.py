"""Extract the primary text of a Federal Reserve monetary-policy press release."""

from html.parser import HTMLParser

from .news import official_release_url


class _ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.article_depth: int | None = None
        self.body_depth: int | None = None
        self.title_depth: int | None = None
        self.paragraph_depth: int | None = None
        self.title_parts: list[str] = []
        self.paragraph_parts: list[str] = []
        self.paragraphs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
                   "meta", "param", "source", "track", "wbr"}:
            return
        self.tags.append(tag)
        attributes = dict(attrs)
        depth = len(self.tags)
        if tag == "div" and attributes.get("id") == "article":
            self.article_depth = depth
        elif self.article_depth and tag == "div" and self.body_depth is None:
            classes = set(attributes.get("class", "").split())
            if {"col-xs-12", "col-sm-8", "col-md-8"} <= classes and "heading" not in classes:
                self.body_depth = depth
        if self.article_depth and tag == "h3" and "title" in attributes.get("class", "").split():
            self.title_depth = depth
        if self.body_depth and tag == "p" and self.paragraph_depth is None:
            self.paragraph_depth = depth
            self.paragraph_parts = []

    def handle_endtag(self, tag):
        depth = len(self.tags)
        if tag == "p" and self.paragraph_depth == depth:
            paragraph = " ".join("".join(self.paragraph_parts).split())
            if paragraph:
                self.paragraphs.append(paragraph)
            self.paragraph_depth = None
        if tag == "h3" and self.title_depth == depth:
            self.title_depth = None
        if tag == "div" and self.body_depth == depth:
            self.body_depth = None
        if tag == "div" and self.article_depth == depth:
            self.article_depth = None
        if self.tags and self.tags[-1] == tag:
            self.tags.pop()

    def handle_data(self, data):
        if self.paragraph_depth:
            self.paragraph_parts.append(data)
        if self.title_depth:
            self.title_parts.append(data)


def extract_release_body(html: str, *, url: str, expected_title: str) -> str:
    """Fail closed when the URL, primary article, title or body cannot be verified."""
    if not official_release_url(url) or len(html.encode("utf-8")) > 1_000_000:
        raise ValueError("Invalid official release page")
    parser = _ArticleParser()
    parser.feed(html)
    title = " ".join("".join(parser.title_parts).split())
    if title.casefold() != " ".join(expected_title.split()).casefold():
        raise ValueError("Official release title differs from RSS")
    paragraphs = []
    for paragraph in parser.paragraphs:
        if paragraph.casefold().startswith("for media inquiries"):
            break
        paragraphs.append(paragraph)
    body = "\n\n".join(paragraphs)
    if len(body) < 80 or len(body) > 6000:
        raise ValueError("Official release body is missing or oversized")
    return body


def enrich_fed_item(item: dict, html: str) -> dict:
    body = extract_release_body(html, url=item["source_url"], expected_title=item["title"])
    return item | {"body": body, "metadata": item["metadata"] | {
        "origin": "official_release", "interpretation": "official_release_body",
        "content_quality": "body", "model_use_allowed": True,
        "rights_basis": "federal_reserve_board_public_domain",
    }}
