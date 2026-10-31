"""Bounded fixed-phrase diagnostics shared by production and verification."""

import httpx


def safe_denial_evidence(response: httpx.Response) -> dict[str, str]:
    """Recognize bounded phrases; never copy arbitrary provider diagnostics."""
    text = response.content[:8192].decode("utf-8", errors="replace").lower()
    for phrase, category in (
        ("restricted location", "geographic_restriction"),
        ("block access from your country", "geographic_restriction"),
        ("too many requests", "rate_limit"),
        ("web application firewall", "waf"),
    ):
        if phrase in text:
            return {"denial_category": category, "safe_evidence": phrase}
    return {"denial_category": "unknown", "safe_evidence": "unclassified denial"}
