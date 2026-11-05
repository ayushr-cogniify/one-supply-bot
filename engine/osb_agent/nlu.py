"""Scope gate and slot extraction (Engineering appendix A.2, A.3).

This build uses deterministic rules. The pipeline calls `parse()` through `osb_agent.models`, so a
Claude Haiku implementation can replace it without touching the scenarios. Rules never fill a slot
from general knowledge: a value is either in the message or it is missing."""
import re
from dataclasses import dataclass, field

LCS = {
    "USQ4": r"\b(?:north\s+)?las\s+vegas\b|\bvegas\b|\bnlv\b|\busq4\b|\busl6\b",
    "USP6": r"\bharrisburg\b|\busp6\b|\busd4\b",
    "USP8": r"\batlanta\b|\busp8\b|\busf2\b",
    "USQ2": r"\bst\.?\s*peters\b|\bsaint\s+peters\b|\busq2\b|\busf6\b",
}
FACTORIES = {"USR2": r"\bst\.?\s*peters\b|\bsaint\s+peters\b|\bstp\b|\busr2\b",
             "USA2": r"\bbelle\s*mead\b|\busa2\b"}
NETWORK = r"\bnetwork\b|\bnational(?:ly)?\b|\bwhole\s+us\b|\ball\s+(?:four\s+|4\s+)?lcs\b|\bacross\s+the\s+us\b"

# Out of scope (appendix A.2 list). Checked before any scenario keyword.
OOS = [
    (r"\b(upload|load|push|send)\b.*\bforecast", "forecast upload"),
    (r"\b(change|edit|adjust|update|raise|lower|increase|override)\b.*\bforecast\b", "forecast edit"),
    (r"\bforecast\b.*\bby\s+-?\d+\s*%", "forecast edit"),
    (r"\b(create|raise|book|make|set\s+up|schedule)\b.*\b(shuttle|sto|stock\s+transport|transfer\s+order)\b", "shuttle or STO"),
    (r"\brebalanc", "rebalancing order"),
    (r"\b(emo|cmo)\b|\bwork\s+order\b|\bpurchase\s+order\b|\braise\s+(?:a|an)\s+po\b", "EMO/CMO or purchase order"),
    (r"\b(write|post|enter|push|update)\b.*\b(to|in|into)\s+(sap|kinaxis)\b", "write to SAP or Kinaxis"),
    (r"\b(e-?mail|send\s+(?:a\s+)?(?:note|message|mail))\b", "email"),
    (r"\bcarrier\b", "carrier selection"),
    (r"\bcanada\b|\bcac\d\b|\bmexico\b|\buk\b|\beurope\b", "non-US site"),
    (r"\b(results|earnings|revenue|share\s+price|stock\s+price)\b", "general business question"),
    (r"\bhow\s+should\b|\bbest\s+practice|\bin\s+general\b|\bthink\s+about\b", "general advice"),
    (r"\b(dettol|finish|air\s*wick|vanish|mucinex|durex|enfamil|nurofen|strepsils|veet|calgon|woolite)\b",
     "brand outside Lysol LDS and Lysol Sanitizers"),
]

SCENARIO_WORDS = {
    "S4": r"\bcuts?\b|\bcutting\b|\breason\s+codes?\b|\bcode\s+the\b|\bpfr\b|\bfill\s+rate\b|\bservice\s+(?:miss|failure|fail)",
    "S2": r"\battainment\b|\badherence\b|\bfactory\b.*\b(?:at|drops?|running)\b|\b(?:pl|sl)\d{2}\b|\bba\d\b|\bwork\s*cent",
    "S3": r"\bgrowth\b|\bgrow\b|\bcapacity\b|\d+\s*%\s*more\b|\bmore\b.*\d+\s*%|\bsegment\b",
    "S5": r"\bwhat\s+changed\b|\bchanged\b|\bsince\s+last\s+week\b|\breview\s+my\s+pir\b|\bmy\s+pir\s+items\b|\bweek\s+over\s+week\b|\bmoved\b",
    "PIR": r"\bpir\b|\bprojected\s+inventory\b|\binventory\s+report\b",
    "S1": r"\bcases?\b|\bcs\b|\bdemand\b|\bspike\b|\bextra\b|\bcan\s+we\s+(?:take|support|ship|cover)\b|\bcover\b|\bwoc\b|\bweeks?\s+of\s+cover\b|\d+\s*k\b|\bstock\b|\bon\s+hand\b",
}
PRIORITY = ["S4", "S5", "S2", "S3", "PIR", "S1"]

MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
           "ten": 10, "eleven": 11, "twelve": 12}

ITEM_STOP = r"(?=\s+(?:at|in|from|to|on|for|next|this|week|by|vs|versus|with|starting|over|across)\b|[?.,!;]|$)"


@dataclass
class Parse:
    label: str
    confidence: float
    reason: str
    slots: dict = field(default_factory=dict)

    def to_dict(self):
        return {"label": self.label, "confidence": self.confidence, "reason": self.reason, "slots": self.slots}


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def find_lcs(t: str) -> list:
    return [lc for lc, rx in LCS.items() if re.search(rx, t)]


def find_factory(t: str):
    for f, rx in FACTORIES.items():
        if re.search(rx, t):
            return f
    return None


def find_work_center(t: str):
    m = re.search(r"\b((?:pl|sl)\d{2}|ba\d)\b", t)
    return m.group(1).upper() if m else None


def find_delta(t: str):
    """Signed cases: '+5,000 cases', '5k more', '-2,000 cs', 'drop 3000 cases'."""
    m = re.search(r"([+-]?)\s*(\d[\d,]*(?:\.\d+)?)\s*(k\b)?\s*(?:more\s+|extra\s+|additional\s+)?(?:cases?|cs)\b", t) \
        or re.search(r"([+-]?)\s*(\d[\d,]*(?:\.\d+)?)\s*(k)\b", t)
    if not m:
        return None
    v = _num(m.group(2)) * (1000 if m.group(3) else 1)
    neg = m.group(1) == "-" or re.search(r"\b(fewer|less|reduce|reduction|cancel|lower|drop)\b", t)
    return int(-v if neg else v)


def find_pct(t: str):
    m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*(?:%|percent\b|pct\b)", t)
    return float(m.group(1)) if m else None


def find_size(t: str):
    m = re.search(r"\b(\d+(?:\.\d+)?)\s*-?\s*(?:oz|fz|fl\.?\s*oz|ounce)\b", t)
    return float(m.group(1)) if m else None


def find_months(t: str):
    m = re.search(r"\b(\d+|" + "|".join(WORDNUM) + r")\s+months?\b", t)
    if not m:
        return None
    g = m.group(1)
    return int(g) if g.isdigit() else WORDNUM[g]


def find_date(t: str):
    """(month, day) or (year, month, day) from '10/15', '2026-10-15', 'oct 15', 'october 15th'."""
    m = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", t)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t)
    if m:
        y = m.group(3)
        y = None if y is None else int(y) + (2000 if len(y) == 2 else 0)
        return y, int(m.group(1)), int(m.group(2))
    m = re.search(r"\b(" + "|".join(MONTHS) + r")[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", t)
    if m:
        return None, MONTHS[m.group(1)], int(m.group(2))
    return None


def find_month(t: str):
    """A month named on its own ('in September', 'sept LLS')."""
    m = re.search(r"\b(" + "|".join(MONTHS) + r")[a-z]*\b(?!\s+\d)", t)
    if not m:
        return None
    y = re.search(r"\b(20\d{2})\b", t)
    return (int(y.group(1)) if y else None, MONTHS[m.group(1)])


def find_week(t: str):
    """ISO-like Kinaxis week '202643', 'week of 10/19', 'next week', 'this week'."""
    m = re.search(r"\b(20\d{2})(\d{2})\b", t)
    if m and 1 <= int(m.group(2)) <= 53:
        return {"calweek": int(m.group(0))}
    m = re.search(r"\bweek\s+of\s+(.+?)(?=$|[?.,!]|\s+at\b|\s+in\b)", t)
    if m:
        d = find_date(m.group(1))
        if d:
            return {"date": d}
    if re.search(r"\bthis\s+week\b|\bcurrent\s+week\b", t):
        return {"relative": 0}
    if re.search(r"\bnext\s+week\b", t):
        return {"relative": 1}
    m = re.search(r"\bin\s+(\d+)\s+weeks\b", t)
    if m:
        return {"relative": int(m.group(1))}
    return None


def find_item_text(t: str):
    """Item words or code. Codes are only ever turned into materials by resolve_item."""
    m = re.search(r"\b(\d{12,14})\b", t)                         # UPC / GTIN
    if m:
        return m.group(1)
    m = re.search(r"(?<![\d/.-])(\d{7})(?![\d/.-])", t)          # GIC / material
    if m:
        return m.group(1)
    for rx in [r"\bcut\s+of\s+(.+?)" + ITEM_STOP,
               r"\b(?:cases?|cs)\s+of\s+(.+?)" + ITEM_STOP,
               r"\bk\s+(?:more\s+|extra\s+)?(?:of\s+)?(.+?)" + ITEM_STOP,
               r"\b(?:more|extra|additional)\s+(?!cases?\b)(.+?)" + ITEM_STOP,
               r"\bcover\s+(?:on|for|of)\s+(.+?)" + ITEM_STOP,
               r"\bwoc\s+(?:on|for|of)\s+(.+?)" + ITEM_STOP,
               r"\b(?:stock|on\s+hand)\s+(?:on|for|of)\s+(.+?)" + ITEM_STOP,
               r"\bfor\s+(.+?)" + ITEM_STOP]:
        m = re.search(rx, t)
        if m:
            s = re.sub(r"^(?:the|our|my|some)\s+", "", m.group(1).strip())
            if s and not re.fullmatch(r"[\d,.\s%+-]*|week.*|lcs?|network|cases?", s):
                return s
    return None


def parse(text: str) -> Parse:
    t = " ".join(str(text).lower().split())
    for rx, why in OOS:
        if re.search(rx, t):
            return Parse("OUT_OF_SCOPE", 0.95, why)
    hits = [s for s in PRIORITY if re.search(SCENARIO_WORDS[s], t)]
    # A cut on its own day is S4 even when the message also says 'cases'
    label = hits[0] if hits else None
    if label == "S3" and "S2" in hits and not re.search(r"\bgrowth\b|\bgrow\b|\bmore\b", t):
        label = "S2"
    if label is None:
        return Parse("OUT_OF_SCOPE", 0.4, "no scenario keyword")
    slots = {
        "item_text": find_item_text(t),
        "delta_cases": find_delta(t),
        "pct": find_pct(t),
        "week": find_week(t),
        "date": find_date(t),
        "month": find_month(t),
        "lcs": find_lcs(t),
        "network": bool(re.search(NETWORK, t)),
        "factory": find_factory(t),
        "work_center": find_work_center(t),
        "size_oz": find_size(t),
        "months": find_months(t),
        "brand": ("Lysol Sanitizers" if re.search(r"\blls\b|\bsanitizer|\blaundry\b", t)
                  else "Lysol LDS" if re.search(r"\blds\b|\bdisinfect|\bspray\b|\bwipes?\b", t) else None),
        "pir_items": bool(re.search(r"\bmy\s+pir\b|\bpir\s+items\b|\bmy\s+items\b", t)),
        "cover_query": bool(re.search(r"\bcover\b|\bwoc\b|\bstock\b|\bon\s+hand\b", t)) and find_delta(t) is None,
        "code_cut": bool(re.search(r"\bcode\b|\breason\s+code\b|\bwhy\b.*\bcut\b", t)),
    }
    slots = {k: v for k, v in slots.items() if v not in (None, [], False)}
    return Parse(label, 0.9 if len(hits) == 1 else 0.8, f"keywords: {', '.join(hits)}", slots)
