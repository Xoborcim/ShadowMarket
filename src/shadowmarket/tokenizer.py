"""Message tokenization for O(1) stock and bounty lookups."""

from __future__ import annotations

import re
import string
import unicodedata

CUSTOM_EMOJI_RE = re.compile(r"<a?:([A-Za-z0-9_]+):\d+>")
SHORTCODE_RE = re.compile(r":([A-Za-z0-9_]+):")
WHITESPACE_RE = re.compile(r"\s+")


def normalize_keyword(raw: str) -> str:
    """Canonical form stored in the database and cache sets."""
    text = raw.strip()
    if not text:
        return ""

    custom = CUSTOM_EMOJI_RE.fullmatch(text)
    if custom:
        return f":{custom.group(1).lower()}:"

    short = SHORTCODE_RE.fullmatch(text)
    if short:
        return f":{short.group(1).lower()}:"

    return WHITESPACE_RE.sub(" ", text.lower())


def tokenize(content: str) -> set[str]:
    """Return unique lowercase tokens: words, unicode emoji, and custom emoji shortcodes.

    Repeated tokens in a single message collapse to one hit, which is the spam debounce
    required by the spec.
    """
    tokens: set[str] = set()

    for match in CUSTOM_EMOJI_RE.finditer(content):
        tokens.add(f":{match.group(1).lower()}:")

    for match in SHORTCODE_RE.finditer(content):
        tokens.add(f":{match.group(1).lower()}:")

    stripped = CUSTOM_EMOJI_RE.sub(" ", content)
    for raw in stripped.split():
        word = raw.strip(string.punctuation + "“”‘’")
        if not word:
            continue
        lowered = word.lower()
        tokens.add(lowered)
        if _is_emoji(word) and word != lowered:
            tokens.add(word)

    return {token for token in tokens if token}


def keyword_hits(content: str, keywords: set[str]) -> set[str]:
    """Match listed stocks/bounties, including multi-word phrases like `hi back`.

    Single-token listings still use the unique-token set (O(1)). Phrases are
    matched as whole words so `high back` does not count as `hi back`.
    """
    if not keywords:
        return set()
    tokens = tokenize(content)
    hits = tokens & keywords
    collapsed = WHITESPACE_RE.sub(" ", content.lower())
    for keyword in keywords:
        if keyword in hits:
            continue
        if keyword not in collapsed:
            continue
        pattern = r"(?<!\w)" + re.escape(keyword) + r"(?!\w)"
        if re.search(pattern, collapsed):
            hits.add(keyword)
    return hits


def _is_emoji(text: str) -> bool:
    if not text:
        return False
    return any(unicodedata.category(ch) == "So" for ch in text)
