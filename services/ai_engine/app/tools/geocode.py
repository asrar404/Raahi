"""Place-name geocoding.

The LLM extracts place *names* ("Paharganj", "Saket"), but route generation
needs coordinates. Asking the model for lat/lon directly produces confident,
wrong numbers — it will happily place Paharganj in the Arabian Sea — so
resolution is done here instead.

Resolution order:

  1. **Built-in gazetteer** — ~200 Indian transit landmarks, metro stations
     and neighbourhoods. Offline, instant, and covers the overwhelming
     majority of real queries in the supported cities.
  2. **Fuzzy match** against the same gazetteer, so "pahar ganj",
     "Pahargunj" and "PAHARGANJ" all land correctly.
  3. **Nominatim** (opt-in) for anything else. Off by default: the public
     endpoint is rate-limited to roughly 1 req/s and its usage policy
     forbids bulk querying.

Coordinates are approximate landmark centroids, adequate for transit planning
and safety-zone lookup, and are not a substitute for a real geocoding
provider in production.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Optional

import httpx

from app.config import settings
from app.schemas.route import ResolvedPlace

log = logging.getLogger(__name__)

# ============================================================
# Gazetteer: canonical name -> (lat, lon, city, aliases)
# ============================================================
_RAW_GAZETTEER: dict[str, tuple[float, float, str, tuple[str, ...]]] = {
    # ── Delhi: central ──────────────────────────────────────
    "Connaught Place": (28.6315, 77.2167, "Delhi", ("cp", "rajiv chowk", "connaught")),
    "New Delhi Railway Station": (28.6425, 77.2215, "Delhi", ("ndls", "new delhi station")),
    "Paharganj": (28.6428, 77.2120, "Delhi", ("pahar ganj", "main bazar", "paharganj main bazar")),
    "Chandni Chowk": (28.6506, 77.2303, "Delhi", ("old delhi", "chandani chowk")),
    "Red Fort": (28.6562, 77.2410, "Delhi", ("lal qila",)),
    "India Gate": (28.6129, 77.2295, "Delhi", ("rajpath", "kartavya path")),
    "Kashmere Gate": (28.6675, 77.2281, "Delhi", ("isbt", "kashmiri gate", "kashmere gate isbt")),
    "Karol Bagh": (28.6519, 77.1909, "Delhi", ("karolbagh",)),
    "Civil Lines": (28.6770, 77.2250, "Delhi", ()),
    "Chawri Bazar": (28.6490, 77.2260, "Delhi", ("chawri",)),
    "Patel Chowk": (28.6230, 77.2140, "Delhi", ()),
    "Central Secretariat": (28.6150, 77.2120, "Delhi", ()),
    "Udyog Bhawan": (28.6110, 77.2120, "Delhi", ()),
    "Lok Kalyan Marg": (28.5970, 77.2110, "Delhi", ("race course",)),
    "Jor Bagh": (28.5860, 77.2160, "Delhi", ()),
    "Khan Market": (28.5985, 77.2270, "Delhi", ()),
    "Barakhamba Road": (28.6300, 77.2250, "Delhi", ("barakhamba",)),
    "ITO": (28.6280, 77.2410, "Delhi", ()),
    "Mandi House": (28.6255, 77.2340, "Delhi", ()),

    # ── Delhi: south ────────────────────────────────────────
    "Saket": (28.5245, 77.2066, "Delhi", ("saket district centre", "select citywalk")),
    "Hauz Khas": (28.5535, 77.1945, "Delhi", ("hauz khas village", "hkv")),
    "Green Park": (28.5590, 77.2070, "Delhi", ()),
    "AIIMS": (28.5672, 77.2100, "Delhi", ("all india institute of medical sciences",)),
    "Safdarjung": (28.5730, 77.2070, "Delhi", ("safdarjang",)),
    "Lajpat Nagar": (28.5677, 77.2433, "Delhi", ("lajpat",)),
    "Nehru Place": (28.5494, 77.2510, "Delhi", ()),
    "Kalkaji": (28.5486, 77.2588, "Delhi", ("kalkaji mandir",)),
    "Govindpuri": (28.5390, 77.2630, "Delhi", ()),
    "Malviya Nagar": (28.5290, 77.2060, "Delhi", ()),
    "Chirag Delhi": (28.5400, 77.2260, "Delhi", ()),
    "Qutub Minar": (28.5245, 77.1855, "Delhi", ("qutb minar",)),
    "Mehrauli": (28.5200, 77.1800, "Delhi", ()),
    "Chattarpur": (28.5060, 77.1780, "Delhi", ("chhatarpur",)),
    "Vasant Kunj": (28.5200, 77.1590, "Delhi", ("vasant vihar",)),
    "Munirka": (28.5560, 77.1720, "Delhi", ()),
    "JNU": (28.5400, 77.1660, "Delhi", ("jawaharlal nehru university",)),
    "Sarojini Nagar": (28.5760, 77.1980, "Delhi", ("ina market", "dilli haat")),
    "Moolchand": (28.5680, 77.2370, "Delhi", ()),
    "Ashram": (28.5720, 77.2590, "Delhi", ()),

    # ── Delhi: east / trans-Yamuna ──────────────────────────
    "Hazrat Nizamuddin": (28.5883, 77.2506, "Delhi", ("nizamuddin", "nizamuddin railway station")),
    "Sarai Kale Khan": (28.5900, 77.2560, "Delhi", ()),
    "Akshardham": (28.6127, 77.2773, "Delhi", ()),
    "Laxmi Nagar": (28.6300, 77.2770, "Delhi", ()),
    "Mayur Vihar": (28.6090, 77.2950, "Delhi", ()),
    "Anand Vihar": (28.6510, 77.3160, "Delhi", ("anand vihar isbt",)),
    "Shahdara": (28.6730, 77.2890, "Delhi", ()),
    "Dilshad Garden": (28.6757, 77.3218, "Delhi", ()),
    "Yamuna Bank": (28.6230, 77.2790, "Delhi", ()),
    "Okhla": (28.5355, 77.2740, "Delhi", ("okhla industrial area",)),
    "Jamia Millia Islamia": (28.5610, 77.2800, "Delhi", ("jamia", "jamia nagar")),

    # ── Delhi: west / north ─────────────────────────────────
    "Dwarka": (28.5921, 77.0460, "Delhi", ("dwarka sector 21",)),
    "Janakpuri": (28.6219, 77.0878, "Delhi", ("janak puri",)),
    "Rajouri Garden": (28.6490, 77.1210, "Delhi", ("rajourigarden",)),
    "Tilak Nagar": (28.6370, 77.0960, "Delhi", ()),
    "Rohini": (28.7495, 77.0565, "Delhi", ()),
    "Rithala": (28.7210, 77.1070, "Delhi", ()),
    "Pitampura": (28.7030, 77.1320, "Delhi", ()),
    "Netaji Subhash Place": (28.6940, 77.1520, "Delhi", ("nsp",)),
    "Azadpur": (28.7070, 77.1780, "Delhi", ()),
    "Model Town": (28.7020, 77.1930, "Delhi", ()),
    "Delhi University": (28.6890, 77.2090, "Delhi", ("north campus", "vishwavidyalaya", "du")),
    "Punjabi Bagh": (28.6690, 77.1310, "Delhi", ()),
    "Inderlok": (28.6730, 77.1700, "Delhi", ()),

    # ── Delhi NCR ───────────────────────────────────────────
    "IGI Airport": (28.5562, 77.1000, "Delhi", ("delhi airport", "terminal 3", "t3", "igi")),
    "Aerocity": (28.5486, 77.1215, "Delhi", ()),
    "Gurugram": (28.4595, 77.0266, "Gurugram", ("gurgaon",)),
    "Cyber Hub": (28.4949, 77.0886, "Gurugram", ("cyber city", "dlf cyber city", "cyberhub")),
    "MG Road Gurugram": (28.4795, 77.0800, "Gurugram", ("mg road gurgaon",)),
    "HUDA City Centre": (28.4593, 77.0724, "Gurugram", ("huda city center",)),
    "Sikanderpur": (28.4815, 77.0940, "Gurugram", ("sikandarpur",)),
    "Ghitorni": (28.4940, 77.1490, "Delhi", ()),
    "Sultanpur": (28.4990, 77.1620, "Delhi", ()),
    "Arjan Garh": (28.4800, 77.1260, "Gurugram", ()),
    "Noida Sector 18": (28.5708, 77.3260, "Noida", ("noida sector18", "atta market")),
    "Noida City Centre": (28.5745, 77.3560, "Noida", ("noida city center",)),
    "Botanical Garden": (28.5640, 77.3340, "Noida", ()),
    "Noida Electronic City": (28.6270, 77.3750, "Noida", ()),
    "Vaishali": (28.6500, 77.3400, "Ghaziabad", ()),
    "Ghaziabad": (28.6692, 77.4538, "Ghaziabad", ()),
    "Faridabad": (28.4089, 77.3178, "Faridabad", ()),

    # ── Mumbai: south ───────────────────────────────────────
    "CSMT": (18.9398, 72.8355, "Mumbai", ("cst", "vt", "victoria terminus",
                                          "chhatrapati shivaji terminus")),
    "Churchgate": (18.9322, 72.8264, "Mumbai", ()),
    "Colaba": (18.9067, 72.8147, "Mumbai", ("colaba causeway",)),
    "Gateway of India": (18.9220, 72.8347, "Mumbai", ()),
    "Marine Drive": (18.9430, 72.8230, "Mumbai", ("queens necklace",)),
    "Mumbai Central": (18.9690, 72.8190, "Mumbai", ("bombay central", "mumbai central station")),
    "Grant Road": (18.9630, 72.8150, "Mumbai", ()),
    "Byculla": (18.9760, 72.8330, "Mumbai", ()),
    "Lower Parel": (18.9960, 72.8300, "Mumbai", ("lowerparel",)),
    "Parel": (19.0080, 72.8390, "Mumbai", ()),
    "Worli": (19.0100, 72.8170, "Mumbai", ("worli sea face",)),

    # ── Mumbai: central / western ───────────────────────────
    "Dadar": (19.0183, 72.8443, "Mumbai", ("dadar station",)),
    "Matunga": (19.0270, 72.8500, "Mumbai", ("kings circle",)),
    "Sion": (19.0390, 72.8620, "Mumbai", ()),
    "Mahim": (19.0410, 72.8420, "Mumbai", ()),
    "Dharavi": (19.0400, 72.8500, "Mumbai", ()),
    "Bandra": (19.0544, 72.8402, "Mumbai", ("bandra station",)),
    "Bandra West": (19.0596, 72.8295, "Mumbai", ("linking road", "bandra w")),
    "Bandra Kurla Complex": (19.0656, 72.8680, "Mumbai", ("bkc",)),
    "Khar": (19.0700, 72.8380, "Mumbai", ("khar road",)),
    "Santacruz": (19.0810, 72.8410, "Mumbai", ("santa cruz",)),
    "Vile Parle": (19.0990, 72.8440, "Mumbai", ()),
    "Juhu": (19.1000, 72.8265, "Mumbai", ("juhu beach",)),
    "Andheri": (19.1197, 72.8468, "Mumbai", ("andheri station",)),
    "Andheri West": (19.1350, 72.8260, "Mumbai", ()),
    "Versova": (19.1300, 72.8130, "Mumbai", ()),
    "Goregaon": (19.1550, 72.8490, "Mumbai", ()),
    "Malad": (19.1870, 72.8480, "Mumbai", ()),
    "Borivali": (19.2290, 72.8570, "Mumbai", ()),
    "Mumbai Airport": (19.0900, 72.8656, "Mumbai", ("bom", "terminal 2", "csmia")),

    # ── Mumbai: harbour / central line / MMR ────────────────
    "Kurla": (19.0654, 72.8792, "Mumbai", ("kurla station", "lokmanya tilak terminus")),
    "Ghatkopar": (19.0860, 72.9080, "Mumbai", ()),
    "Chembur": (19.0620, 72.8990, "Mumbai", ()),
    "Wadala": (19.0180, 72.8620, "Mumbai", ()),
    "Powai": (19.1176, 72.9060, "Mumbai", ("iit bombay", "hiranandani")),
    "Mulund": (19.1720, 72.9560, "Mumbai", ()),
    "Thane": (19.1860, 72.9750, "Thane", ("thane station",)),
    "Vashi": (19.0770, 72.9990, "Navi Mumbai", ("navi mumbai",)),
    "Panvel": (18.9894, 73.1175, "Navi Mumbai", ()),
    "Dombivli": (19.2160, 73.0870, "Thane", ()),
    "Kalyan": (19.2437, 73.1355, "Thane", ()),

    # ── Jaipur ──────────────────────────────────────────────
    "Jaipur Railway Station": (26.9200, 75.7880, "Jaipur", ("jaipur junction", "jp")),
    "Sindhi Camp": (26.9210, 75.7960, "Jaipur", ("jaipur bus stand",)),
    "Bani Park": (26.9120, 75.8130, "Jaipur", ()),
    "Hawa Mahal": (26.9239, 75.8267, "Jaipur", ()),
    "Johari Bazaar": (26.9180, 75.8250, "Jaipur", ("johri bazar",)),
    "Chandpole": (26.9270, 75.8100, "Jaipur", ()),
    "Amer Fort": (26.9855, 75.8513, "Jaipur", ("amber fort",)),
    "C Scheme": (26.9080, 75.7960, "Jaipur", ()),
    "Malviya Nagar Jaipur": (26.8560, 75.8060, "Jaipur", ()),
    "Vaishali Nagar": (26.9120, 75.7370, "Jaipur", ()),
    "Mansarovar": (26.8560, 75.7620, "Jaipur", ()),
    "Jaipur Airport": (26.8242, 75.8122, "Jaipur", ()),

    # ── Bengaluru ───────────────────────────────────────────
    "Majestic": (12.9767, 77.5713, "Bengaluru", ("kempegowda bus station",
                                                 "bangalore city railway station", "kbs")),
    "MG Road Bengaluru": (12.9750, 77.6060, "Bengaluru", ("mg road bangalore",)),
    "Indiranagar": (12.9719, 77.6412, "Bengaluru", ("indira nagar",)),
    "Koramangala": (12.9352, 77.6245, "Bengaluru", ()),
    "HSR Layout": (12.9116, 77.6474, "Bengaluru", ("hsr",)),
    "BTM Layout": (12.9160, 77.6100, "Bengaluru", ("btm",)),
    "Jayanagar": (12.9250, 77.5830, "Bengaluru", ()),
    "Banashankari": (12.9250, 77.5670, "Bengaluru", ()),
    "Whitefield": (12.9698, 77.7500, "Bengaluru", ()),
    "Marathahalli": (12.9560, 77.7010, "Bengaluru", ()),
    "Electronic City": (12.8452, 77.6602, "Bengaluru", ("e city",)),
    "Silk Board": (12.9170, 77.6230, "Bengaluru", ("central silk board",)),
    "Hebbal": (13.0350, 77.5910, "Bengaluru", ()),
    "Yeshwantpur": (13.0230, 77.5540, "Bengaluru", ()),
    "Rajajinagar": (12.9910, 77.5520, "Bengaluru", ()),
    "Malleshwaram": (13.0030, 77.5710, "Bengaluru", ("malleswaram",)),
    "KR Market": (12.9630, 77.5770, "Bengaluru", ("city market",)),
    "Bengaluru Airport": (13.1986, 77.7066, "Bengaluru", ("blr", "kempegowda airport")),

    # ── City fallbacks ──────────────────────────────────────
    "Delhi": (28.6139, 77.2090, "Delhi", ("new delhi", "dilli")),
    "Mumbai": (19.0760, 72.8777, "Mumbai", ("bombay",)),
    "Jaipur": (26.9124, 75.7873, "Jaipur", ("pink city",)),
    "Bengaluru": (12.9716, 77.5946, "Bengaluru", ("bangalore",)),
}

# Noise words stripped when building the secondary index, so "Saket Metro
# Station" still matches the "Saket" entry.
_NOISE_WORDS = {
    "metro", "station", "railway", "rly", "junction", "jn", "stop", "stand",
    "terminal", "terminus", "bus", "the", "near", "at", "area", "road", "rd",
}


def _normalise(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_noise(normalised: str) -> str:
    tokens = [t for t in normalised.split() if t not in _NOISE_WORDS]
    return " ".join(tokens) if tokens else normalised


class _Entry:
    __slots__ = ("name", "lat", "lon", "city")

    def __init__(self, name: str, lat: float, lon: float, city: str):
        self.name = name
        self.lat = lat
        self.lon = lon
        self.city = city


def _build_index() -> tuple[dict[str, _Entry], dict[str, _Entry]]:
    """Build exact and noise-stripped lookup tables.

    First writer wins on collisions, so canonical names take precedence over
    aliases (aliases are registered after their canonical entry).
    """
    exact: dict[str, _Entry] = {}
    stripped: dict[str, _Entry] = {}

    for canonical, (lat, lon, city, aliases) in _RAW_GAZETTEER.items():
        entry = _Entry(canonical, lat, lon, city)
        for key in (canonical, *aliases):
            norm = _normalise(key)
            exact.setdefault(norm, entry)
            stripped.setdefault(_strip_noise(norm), entry)

    return exact, stripped


_EXACT_INDEX, _STRIPPED_INDEX = _build_index()


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _fuzzy_lookup(query: str, city_hint: Optional[str]) -> tuple[Optional[_Entry], float]:
    """Best fuzzy match over the gazetteer.

    Substring containment is checked before ratio matching, because
    "near saket district centre" contains the answer outright but scores
    poorly on whole-string similarity.
    """
    norm = _normalise(query)
    stripped = _strip_noise(norm)
    if not norm:
        return None, 0.0

    city_norm = _normalise(city_hint) if city_hint else None
    best: Optional[_Entry] = None
    best_score = 0.0

    for key, entry in _EXACT_INDEX.items():
        if not key:
            continue

        if key in stripped or stripped in key:
            # Longer overlaps are stronger evidence
            score = 0.80 + min(len(key), len(stripped)) / max(len(stripped), len(key), 1) * 0.15
        else:
            score = _similarity(stripped, key)

        # Nudge matches in the expected city ahead of same-named places
        # elsewhere ("MG Road" exists in both Bengaluru and Gurugram).
        if city_norm and _normalise(entry.city) == city_norm:
            score += 0.06

        if score > best_score:
            best, best_score = entry, score

    return best, min(best_score, 1.0)


async def _nominatim_lookup(query: str, city_hint: Optional[str]) -> Optional[ResolvedPlace]:
    """Fall back to OpenStreetMap. Returns None on any failure."""
    search = f"{query}, {city_hint}, India" if city_hint else f"{query}, India"
    try:
        async with httpx.AsyncClient(timeout=settings.GEOCODE_TIMEOUT_SECONDS) as client:
            response = await client.get(
                settings.NOMINATIM_URL,
                params={"q": search, "format": "json", "limit": 1, "countrycodes": "in"},
                headers={"User-Agent": settings.NOMINATIM_USER_AGENT},
            )
            response.raise_for_status()
            results = response.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("Nominatim lookup failed for %r: %s", query, exc)
        return None

    if not results:
        return None

    hit = results[0]
    try:
        return ResolvedPlace(
            query=query,
            name=hit.get("display_name", query).split(",")[0].strip() or query,
            lat=float(hit["lat"]),
            lon=float(hit["lon"]),
            city=city_hint,
            source="nominatim",
            confidence=0.75,
        )
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("Unparseable Nominatim response for %r: %s", query, exc)
        return None


# Below this, a fuzzy hit is treated as a guess rather than a match.
FUZZY_ACCEPT_THRESHOLD = 0.72


async def geocode(
    query: str,
    city_hint: Optional[str] = None,
    device_lat: Optional[float] = None,
    device_lon: Optional[float] = None,
) -> Optional[ResolvedPlace]:
    """Resolve a place name to coordinates.

    "here", "my location" and similar resolve to the device position when one
    was supplied — a very common way to phrase an origin.
    """
    if not query or not query.strip():
        return None

    norm = _normalise(query)

    # ── Device position ─────────────────────────────────────
    here_phrases = {
        "here", "my location", "current location", "my position",
        "where i am", "this location", "current position",
    }
    if norm in here_phrases or norm.startswith("from here"):
        if device_lat is not None and device_lon is not None:
            return ResolvedPlace(
                query=query, name="Current location",
                lat=device_lat, lon=device_lon,
                city=city_hint, source="device", confidence=1.0,
            )
        # Bail out rather than fuzzy-matching. "here" is 0.57 similar to
        # "hsr" (HSR Layout, Bengaluru), and silently planning a journey from
        # the wrong city is far worse than admitting we do not know.
        log.warning("Query %r refers to the device position but none was supplied", query)
        return None

    # ── Exact gazetteer ─────────────────────────────────────
    entry = _EXACT_INDEX.get(norm) or _STRIPPED_INDEX.get(_strip_noise(norm))
    if entry is not None:
        return ResolvedPlace(
            query=query, name=entry.name, lat=entry.lat, lon=entry.lon,
            city=entry.city, source="gazetteer", confidence=1.0,
        )

    # ── Fuzzy gazetteer ─────────────────────────────────────
    entry, score = _fuzzy_lookup(query, city_hint)
    if entry is not None and score >= FUZZY_ACCEPT_THRESHOLD:
        log.info("Fuzzy geocode %r -> %s (score=%.2f)", query, entry.name, score)
        return ResolvedPlace(
            query=query, name=entry.name, lat=entry.lat, lon=entry.lon,
            city=entry.city, source="gazetteer", confidence=round(score, 2),
        )

    # ── Nominatim ───────────────────────────────────────────
    if settings.ENABLE_NOMINATIM:
        resolved = await _nominatim_lookup(query, city_hint or settings.DEFAULT_CITY)
        if resolved is not None:
            return resolved

    # ── Weak fuzzy hit, flagged as low confidence ───────────
    if entry is not None and score >= 0.5:
        log.warning("Low-confidence geocode %r -> %s (score=%.2f)", query, entry.name, score)
        return ResolvedPlace(
            query=query, name=entry.name, lat=entry.lat, lon=entry.lon,
            city=entry.city, source="gazetteer", confidence=round(score, 2),
        )

    log.warning("Could not geocode %r (city_hint=%r)", query, city_hint)
    return None


def infer_city(*places: Optional[ResolvedPlace]) -> Optional[str]:
    """Pick the most likely city from resolved places, preferring confident hits."""
    for place in places:
        if place is not None and place.city and place.confidence >= 0.8:
            return place.city
    for place in places:
        if place is not None and place.city:
            return place.city
    return None


def gazetteer_size() -> int:
    return len(_RAW_GAZETTEER)
