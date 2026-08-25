"""Natural-language intent extraction.

Two parsers, tried in order:

1. **LLM (Groq / llama-3.3-70b)** — handles the messy phrasings people
   actually use: code-switched Hinglish, implicit origins, "as cheap as
   possible", relative deadlines.

2. **Deterministic regex parser** — used when no GROQ_API_KEY is configured,
   or when the LLM errors or returns unusable output.

The fallback is not decoration. Without it the entire product is unusable
without a paid API key, and a network blip during planning would leave the
traveller with nothing. It handles the dominant phrasing
("<A> to <B> under ₹<N> by <mode>") correctly, which covers most real input.

A note on the prompt: the system message is passed as a `SystemMessage`
instance rather than a `("system", ...)` tuple. Tuples are treated as f-string
templates by LangChain, and the JSON schema embedded in the prompt contains
braces that would be parsed as template variables and raise KeyError.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from app.config import settings
from app.schemas.intent import ParsedIntent, TransitMode

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "intent_parser.txt"

_chain: Optional[Any] = None
_chain_failed = False


def _load_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        log.error("Could not read %s: %s", PROMPT_PATH, exc)
        return (
            "Extract travel intent as JSON with keys source_raw, destination_raw, "
            "budget_ceiling, time_deadline, preferred_modes, safety_priority, "
            "night_travel, city, confidence. Reply with JSON only."
        )


def _build_chain():
    """Construct the Groq chain lazily. Returns None when unavailable."""
    global _chain, _chain_failed

    if _chain is not None:
        return _chain
    if _chain_failed or not settings.llm_configured:
        return None

    try:
        from langchain_core.messages import SystemMessage
        from langchain_core.output_parsers import JsonOutputParser
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_groq import ChatGroq

        llm = ChatGroq(
            model=settings.GROQ_MODEL,
            api_key=settings.GROQ_API_KEY,
            temperature=settings.LLM_TEMPERATURE,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            max_retries=settings.LLM_MAX_RETRIES,
            model_kwargs={"response_format": {"type": "json_object"}},
        )

        system_text = _load_prompt()
        now_ist = datetime.now(IST).isoformat(timespec="minutes")

        prompt = ChatPromptTemplate.from_messages([
            # Literal content — not a template, so braces in the prompt are safe
            SystemMessage(content=f"{system_text}\n\nCurrent time (IST): {now_ist}"),
            ("human", "{user_input}"),
        ])

        _chain = prompt | llm | JsonOutputParser()
        log.info("Groq intent parser ready (model=%s)", settings.GROQ_MODEL)
        return _chain
    except Exception as exc:  # noqa: BLE001
        log.error("Could not build the LLM chain, using the fallback parser: %s", exc)
        _chain_failed = True
        return None


# ============================================================
# Deterministic fallback parser
# ============================================================
_MODE_KEYWORDS: dict[str, TransitMode] = {
    "metro": TransitMode.METRO, "subway": TransitMode.METRO,
    "underground": TransitMode.METRO, "dmrc": TransitMode.METRO,
    "bus": TransitMode.BUS, "buses": TransitMode.BUS,
    "dtc": TransitMode.BUS, "best": TransitMode.BUS,
    "train": TransitMode.TRAIN, "local": TransitMode.TRAIN,
    "suburban": TransitMode.TRAIN, "railway": TransitMode.TRAIN,
    "auto": TransitMode.AUTO, "rickshaw": TransitMode.AUTO,
    "tuktuk": TransitMode.AUTO, "tuk tuk": TransitMode.AUTO,
    "cab": TransitMode.CAB, "taxi": TransitMode.CAB,
    "uber": TransitMode.CAB, "ola": TransitMode.CAB, "car": TransitMode.CAB,
    "rapido": TransitMode.RAPIDO, "bike": TransitMode.RAPIDO,
    "scooter": TransitMode.RAPIDO, "two wheeler": TransitMode.RAPIDO,
    "walk": TransitMode.WALK, "walking": TransitMode.WALK, "on foot": TransitMode.WALK,
}

_NIGHT_WORDS = (
    "night", "late", "after dark", "midnight", "3am", "2am", "1am",
    "early morning", "dawn", "evening late",
)
_SAFETY_WORDS = (
    "alone", "safe", "safety", "unsafe", "scared", "afraid", "female",
    "woman", "women", "girl", "solo", "harass",
)
_CHEAP_WORDS = ("cheap", "cheapest", "budget", "low cost", "minimum", "least expensive")

# Terminators that end a place name. "from" is included so that
# "go to Cyber Hub from Noida Sector 18" does not swallow the origin into the
# destination.
_STOP = r"(?=\s*(?:under|below|within|for|by|in|at|with|before|from|budget|₹|rs\b|$))"

# Order matters. Explicit "from A to B" wins, then arrows, then the reversed
# "to B from A", then the verb-and-destination form, and only then the bare
# "A to B". Putting the bare form earlier makes "go to Koramangala" parse its
# own verb as the origin.
_OD_PATTERNS = (
    re.compile(
        r"\bfrom\s+(?P<src>.+?)\s+(?:to|till|until|upto|up to|->|→)\s+(?P<dst>.+?)" + _STOP,
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<src>.+?)\s*(?:->|→|--?>)\s*(?P<dst>.+?)" + _STOP,
        re.IGNORECASE,
    ),
    # Reversed: "go to Cyber Hub from Noida Sector 18", "to CP from Saket"
    re.compile(
        r"\bto\s+(?P<dst>.+?)\s+from\s+(?P<src>.+?)"
        r"(?=\s*(?:under|below|within|for|by|at|with|before|budget|₹|rs\b|$))",
        re.IGNORECASE,
    ),
    # Destination only: "go to Saket", "take me to Bandra", "reach CP"
    re.compile(
        r"\b(?:go|get|reach|travel|head|take|drop|bring)\s+(?:me\s+)?"
        r"(?:to|towards)\s+(?P<dst>.+?)" + _STOP,
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<src>.+?)\s+to\s+(?P<dst>.+?)" + _STOP,
        re.IGNORECASE,
    ),
)

# Verb phrases that are never a real origin. If cleaning leaves only one of
# these, the origin was never stated and should default to the device.
_FILLER_SOURCE = re.compile(
    r"^(?:i|go|get|take|reach|travel|head|drop|bring|me|we|us|"
    r"go\s+me|get\s+me|take\s+me|need|want|how|whats?|what\s+is)$",
    re.IGNORECASE,
)

_TRAILING_NOISE = re.compile(
    r"\b(?:please|asap|quickly|fast|now|today|tonight|tomorrow|somehow|"
    r"i\s+need\s+to\s+go|i\s+want\s+to\s+go|need\s+to\s+get|take\s+me)\b",
    re.IGNORECASE,
)


def _clean_place(text: str) -> str:
    """Trim filler and punctuation from an extracted place name."""
    text = _TRAILING_NOISE.sub(" ", text or "")
    text = re.sub(r"^\W+|\W+$", "", text.strip())
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,.;:-")


def _extract_budget(text: str) -> Optional[float]:
    """Pull a rupee amount out of the text.

    Amounts qualified by under/below/within/budget win over bare numbers, so
    "from Sector 18 to CP under 200" is not read as a budget of 18.
    """
    qualified = re.search(
        r"(?:under|below|within|less than|max|maximum|budget(?:\s+of)?|upto|up to|for)"
        r"\s*(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?)",
        text, re.IGNORECASE,
    )
    if qualified:
        return _to_float(qualified.group(1))

    symbol = re.search(r"(?:₹|rs\.?|inr)\s*(\d[\d,]*(?:\.\d+)?)", text, re.IGNORECASE)
    if symbol:
        return _to_float(symbol.group(1))

    suffixed = re.search(
        r"(\d[\d,]*(?:\.\d+)?)\s*(?:rupees|rs\.?|inr|bucks|/-)", text, re.IGNORECASE
    )
    if suffixed:
        return _to_float(suffixed.group(1))

    return None


def _to_float(raw: str) -> Optional[float]:
    try:
        value = float(raw.replace(",", ""))
        return value if value > 0 else None
    except ValueError:
        return None


def _extract_deadline(text: str) -> Optional[str]:
    """Resolve "by 9pm" / "before 21:30" to an ISO timestamp in IST."""
    match = re.search(
        r"(?:by|before|until|till)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
        text, re.IGNORECASE,
    )
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").lower()

    if not (0 <= hour <= 24 and 0 <= minute < 60):
        return None

    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    elif not meridiem and hour < 7:
        # "by 6" almost certainly means 18:00, not 06:00
        hour += 12

    now = datetime.now(IST)
    deadline = now.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
    if deadline <= now:
        deadline += timedelta(days=1)
    return deadline.isoformat()


def _detect_night_time(text: str) -> bool:
    """Spot a clock time in the 22:00-06:00 window anywhere in the request.

    "at 11pm" and "leaving 23:30" both imply night travel without using any of
    the keyword forms. Night detection matters because it selects the zone's
    night_risk_score rather than its daytime one.
    """
    for match in re.finditer(
        r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text, re.IGNORECASE
    ):
        hour = int(match.group(1))
        meridiem = match.group(3).lower()
        if hour == 12:
            hour = 0 if meridiem == "am" else 12
        elif meridiem == "pm":
            hour += 12
        if hour < 6 or hour >= 22:
            return True

    for match in re.finditer(r"\b(\d{1,2}):(\d{2})\b", text):
        hour = int(match.group(1))
        if hour <= 23 and (hour < 6 or hour >= 22):
            return True

    return False


def _extract_modes(text: str) -> list[TransitMode]:
    lowered = f" {text.lower()} "
    found: list[TransitMode] = []

    for keyword, mode in _MODE_KEYWORDS.items():
        if re.search(rf"\b{re.escape(keyword)}\b", lowered) and mode not in found:
            # Honour exclusions: "no cabs", "avoid taxi", "without auto"
            if re.search(rf"\b(?:no|not|avoid|without|except)\s+\w*\s*{re.escape(keyword)}",
                         lowered):
                continue
            found.append(mode)

    if re.search(r"public\s+transport|public\s+transit", lowered):
        for mode in (TransitMode.METRO, TransitMode.BUS):
            if mode not in found:
                found.append(mode)

    return found or [TransitMode.METRO, TransitMode.BUS, TransitMode.AUTO]


def heuristic_parse(
    user_input: str,
    device_lat: Optional[float] = None,
    device_lon: Optional[float] = None,
    city: Optional[str] = None,
) -> ParsedIntent:
    """Rule-based intent extraction. Always returns something usable."""
    text = (user_input or "").strip()
    lowered = text.lower()

    source_raw = "here"
    destination_raw = ""

    for pattern in _OD_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        groups = match.groupdict()
        candidate_dst = _clean_place(groups.get("dst") or "")
        candidate_src = _clean_place(groups.get("src") or "") if "src" in groups else ""
        if candidate_dst:
            destination_raw = candidate_dst
            # A bare verb is not an origin
            if candidate_src and not _FILLER_SOURCE.match(candidate_src):
                source_raw = candidate_src
            break

    if not destination_raw:
        # Last resort: treat the longest capitalised or trailing phrase as the
        # destination so the geocoder at least gets a chance.
        tail = _clean_place(re.split(r"\b(?:under|below|within|budget|₹|rs\b)", text)[0])
        destination_raw = tail or text[:60]
        log.info("Heuristic parser could not find an O/D pair in %r", text[:80])

    budget = _extract_budget(text)
    if budget is None:
        budget = 150.0 if any(w in lowered for w in _CHEAP_WORDS) else settings.DEFAULT_BUDGET

    deadline = _extract_deadline(text)
    night = any(w in lowered for w in _NIGHT_WORDS) or _detect_night_time(text)

    # A deadline inside the 22:00-06:00 window implies night travel even when
    # the request never uses the word — "airport by 5am" is a night journey.
    if not night and deadline:
        try:
            hour = datetime.fromisoformat(deadline).astimezone(IST).hour
            night = hour < 6 or hour >= 22
        except ValueError:
            pass

    safety = night or any(w in lowered for w in _SAFETY_WORDS)

    # Confidence reflects how much had to be guessed
    confidence = 0.75
    if source_raw == "here" and device_lat is None:
        confidence -= 0.2
    if _extract_budget(text) is None:
        confidence -= 0.1
    if len(destination_raw) > 45:
        confidence -= 0.15

    intent = ParsedIntent(
        source_raw=source_raw,
        destination_raw=destination_raw,
        budget_ceiling=budget,
        time_deadline=deadline,
        preferred_modes=_extract_modes(text),
        safety_priority=safety,
        night_travel=night,
        city=city,
        confidence=max(0.1, round(confidence, 2)),
    )

    if device_lat is not None and device_lon is not None and source_raw == "here":
        intent.source_lat = device_lat
        intent.source_lon = device_lon

    return intent


# ============================================================
# Public entry point
# ============================================================
async def parse_intent(
    user_input: str,
    device_lat: Optional[float] = None,
    device_lon: Optional[float] = None,
    city: Optional[str] = None,
) -> tuple[ParsedIntent, bool]:
    """Extract travel intent from free text.

    Returns (intent, used_fallback). `used_fallback` is surfaced to the client
    so the UI can hint that a rephrased request might parse better.
    """
    if not user_input or not user_input.strip():
        raise ValueError("user_input must not be empty")

    chain = _build_chain()

    if chain is not None:
        try:
            raw = await chain.ainvoke({"user_input": user_input})
            if not isinstance(raw, dict):
                raise TypeError(f"expected a JSON object, got {type(raw).__name__}")

            # Discard any coordinates the model volunteered — the prompt
            # forbids them, and geocoding is authoritative.
            for key in ("source_lat", "source_lon", "dest_lat", "dest_lon"):
                raw.pop(key, None)

            raw.setdefault("city", city)
            intent = ParsedIntent(**raw)

            if device_lat is not None and device_lon is not None:
                if intent.source_raw.strip().lower() in {"here", "current location", "my location"}:
                    intent.source_lat = device_lat
                    intent.source_lon = device_lon

            log.info(
                "LLM parsed intent: %r -> %r (budget=%.0f modes=%s confidence=%.2f)",
                intent.source_raw, intent.destination_raw, intent.budget_ceiling,
                [m.value for m in intent.preferred_modes], intent.confidence,
            )
            return intent, False

        except Exception as exc:  # noqa: BLE001
            log.warning("LLM parse failed (%s), falling back to heuristics", exc)

    intent = heuristic_parse(user_input, device_lat, device_lon, city)
    log.info(
        "Heuristic parsed intent: %r -> %r (budget=%.0f confidence=%.2f)",
        intent.source_raw, intent.destination_raw, intent.budget_ceiling, intent.confidence,
    )
    return intent, True
