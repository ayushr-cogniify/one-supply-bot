"""Numeric validator (appendix B.1): every number in the narrative must match a number in a tool result.
Integers/codes exact; one-decimal WoC within 0.05; whole percentages within 0.5."""
import re

NUM = re.compile(r"(?<![\w:.-])-?\d[\d,]*(?:\.\d+)?(?![\w])")
IGNORE = {"1", "2", "3", "4", "5", "6"}          # list markers, 'S1..S5', '6 weeks' window wording


def _leaves(obj, out):
    if isinstance(obj, dict):
        for v in obj.values():
            _leaves(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _leaves(v, out)
    elif isinstance(obj, bool):
        return
    elif isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, str):
        for m in NUM.findall(obj):
            try:
                out.add(float(m.replace(",", "")))
            except ValueError:
                pass
    return out


def allowed_numbers(results):
    out = set()
    for r in results:
        _leaves({k: r.get(k) for k in ("values", "table")}, out)
    return out


def validate(text, results):
    text = re.sub(r"\[r:[^\]]+\]", "", text)                 # citations are not numbers
    allowed = allowed_numbers(results)
    bad = []
    for raw in NUM.findall(text):
        if raw in IGNORE:
            continue
        v = float(raw.replace(",", ""))
        tol = 0.05 if "." in raw else 0.5
        if not any(abs(v - a) <= tol for a in allowed):
            bad.append(raw)
    return bad
