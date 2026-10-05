"""Derive offsets only from unique exact quotes in visible source segments."""

from typing import Any

from .signal_validation import validate_event


def validate_grounded_event(event: dict[str, Any], item: dict[str, Any]) -> None:
    validate_event(event, item)
    sources = {segment["source"]: segment["text"] for segment in item["segments"]}
    for span in event["evidence_spans"]:
        # Python slicing clips an excessive end; a declared span must not do so.
        if span["end"] > len(sources[span["source"]]):
            raise ValueError("evidence offsets extend past visible source text")


def ground_event(event: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    fields = {"event_type", "direction", "affected_industries", "horizon",
              "intensity", "uncertainty", "evidence_quotes"}
    if not isinstance(event, dict) or set(event) != fields:
        raise ValueError("quote event fields differ from semantic quote schema")
    quotes = event["evidence_quotes"]
    if not isinstance(quotes, list) or not quotes:
        raise ValueError("every quote event needs source-grounded evidence")
    sources = {segment["source"]: segment["text"] for segment in item["segments"]}
    spans, seen = [], set()
    for evidence in quotes:
        if not isinstance(evidence, dict) or set(evidence) != {"source", "quote"}:
            raise ValueError("evidence quote fields are invalid")
        source, quote = evidence["source"], evidence["quote"]
        if not isinstance(source, str) or source not in sources:
            raise ValueError("evidence quote source is not visible")
        if not isinstance(quote, str) or not quote:
            raise ValueError("evidence quote must be nonempty text")
        text = sources[source]
        start = text.find(quote)
        if start < 0:
            raise ValueError("evidence quote is absent from visible source text")
        if text.find(quote, start + 1) >= 0:
            raise ValueError("evidence quote is ambiguous in visible source text")
        identity = (source, start, start + len(quote))
        if identity in seen:
            raise ValueError("duplicate evidence quote")
        seen.add(identity)
        spans.append({"source": source, "start": start, "end": start + len(quote), "quote": quote})
    grounded = {key: value for key, value in event.items() if key != "evidence_quotes"}
    grounded["evidence_spans"] = spans
    validate_grounded_event(grounded, item)
    return grounded
