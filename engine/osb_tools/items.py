"""resolve_item: lexical item resolution over Item Master + Kinaxis plan (MVP doc 3.5)."""
import re

import pandas as pd

from . import adapter
from .envelope import cite, envelope, table

STOP = {"lysol", "us", "the", "of", "cases", "case", "cs"}


def _pack(s):
    """'4x90', '4/90FZ', '4 X 90 OZ' -> '4/90'"""
    return [f"{a}/{b}" for a, b in re.findall(r"(\d+)\s*[x/]\s*\(?(\d+(?:\.\d+)?)", s.lower())]


def _tokens(s):
    s = s.lower()
    packs = _pack(s)
    s = re.sub(r"(\d+)\s*[x/]\s*\(?(\d+(?:\.\d+)?)", " ", s)
    words = [w for w in re.split(r"[^a-z0-9.]+", s) if w and w not in STOP]
    return set(words) | set(packs)


def _catalogue(plan=None):
    plan = adapter.kinaxis_plan() if plan is None else plan
    im = adapter.item_master().drop_duplicates("ITEMCODE")
    mats = plan[["MATERIAL", "BSG_GROUP"]].drop_duplicates("MATERIAL")
    cat = mats.merge(im, left_on="MATERIAL", right_on="ITEMCODE", how="left")
    cat["tokens"] = cat["ITEM"].fillna("").map(_tokens)
    return cat


def resolve_item(text: str, scope: str = "both", max_candidates: int = 5, plan=None) -> dict:
    cat = _catalogue(plan)
    if scope != "both":
        cat = cat[cat.BSG_GROUP == scope]
    t = str(text).strip()
    exact = cat[(cat.ITEMCODE == t) | (cat.UPC == t) | (cat.GTIN == t)]
    if len(exact) == 1:
        cand = exact.assign(score=1.0)
    else:
        q = _tokens(t)
        if not q:
            return envelope("resolve_item", "needs_input", missing=[{"key": "item", "template": "CLARIFY_ITEM"}])
        cat = cat.assign(score=cat.tokens.map(lambda tk: len(q & tk) / len(q)))
        cand = cat[cat.score > 0].sort_values(["score", "MATERIAL"], ascending=[False, True]).head(max_candidates)
    if cand.empty:
        return envelope("resolve_item", "no_data", values={"item_text": t},
                        missing=[{"key": "item", "id": t, "template": "NO_DATA_ITEM"}])
    out = cand.rename(columns={"MATERIAL": "material", "ITEM": "description", "BSG_GROUP": "bsg_group"})
    out = out[["material", "description", "bsg_group", "score"]]
    s = list(out.score)
    chosen = out.iloc[0].material if s[0] >= 0.85 and (len(s) == 1 or s[0] - s[1] >= 0.10) else None
    status = "ok" if chosen else "needs_input"
    return envelope("resolve_item", status,
                    values={"candidates": out.to_dict("records"), "chosen": chosen, "item_text": t},
                    table=table(out), rows_used=cite(cand),
                    missing=[] if chosen else [{"key": "item", "template": "CLARIFY_ITEM"}])
