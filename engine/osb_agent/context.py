"""Lookups the dialogue needs to turn words into tool arguments. Nothing here produces a number
that reaches the planner: weeks, lines and cut lines found here are passed to tools, and the
tools' results are what the card shows."""
from datetime import date, timedelta
from functools import lru_cache

import pandas as pd

from osb_tools import adapter, config, cuts
from osb_tools.woc import calendar

SITE_NAMES = {lc: v["name"] for lc, v in config.SITES.items()}
FACTORY_NAMES = {"USR2": "St. Peters", "USA2": "Belle Mead"}
WC_NAMES = {"PL": "pour line", "SL": "line", "BA": "line"}


@lru_cache
def cal() -> pd.DataFrame:
    return calendar(adapter.kinaxis_plan())


def snapshot_date() -> date:
    return pd.to_datetime(str(adapter.kinaxis_plan()["SNAPSHOT_WEEK"].iloc[0])).date()


def snapshot_label() -> str:
    d = snapshot_date()
    return f"{d.month}/{d.day}/{d.year}"


@lru_cache
def inventory_dates() -> list:
    return sorted(pd.Timestamp(x).date() for x in adapter.inventory().snapshot_date.unique())


def stock_label() -> str:
    d = inventory_dates()[-1]
    return f"{d.month}/{d.day}/{d.year}"


def week_start(calweek: int) -> date:
    c = cal()
    row = c[c.CALWEEK == int(calweek)]
    return None if row.empty else row.week_start.iloc[0]


def week_label(calweek: int) -> str:
    d = week_start(calweek)
    return f"week of {d.month}/{d.day}" if d else str(calweek)


def resolve_year(y, m, d) -> date:
    """A date without a year is the latest such date on or before the newest data in the files."""
    if y:
        return date(y, m, d)
    ref = max(snapshot_date() + timedelta(days=7 * 26), inventory_dates()[-1])
    for yr in (ref.year, ref.year - 1):
        try:
            cand = date(yr, m, d)
        except ValueError:
            continue
        if cand <= ref:
            return cand
    return date(ref.year - 1, m, d)


def calweek_for(week: dict):
    """Slot value from nlu.find_week -> Kinaxis CALWEEK in the plan horizon, or None."""
    c = cal()
    if not week:
        return None
    if "calweek" in week:
        return week["calweek"] if week["calweek"] in set(c.CALWEEK) else None
    if "week_number" in week:
        # 'week 43' as the planner said it: the first plan week with that Kinaxis week number
        n = int(week["week_number"])
        hit = [w for w in c.CALWEEK if int(w) % 100 == n]
        return int(hit[0]) if hit else None
    if "relative" in week:
        i = int(week["relative"])
        return int(c.CALWEEK.iloc[i]) if i < len(c) else None
    if "date" in week:
        y, m, d = week["date"]
        dt = resolve_year(y, m, d)
        hit = c[[ws <= dt <= ws + timedelta(days=6) for ws in c.week_start]]
        return int(hit.CALWEEK.iloc[0]) if not hit.empty else None
    return None


def week_options(n=2) -> list:
    c = cal().head(n)
    return [{"label": f"Week of {d.month}/{d.day}", "value": int(w)} for w, d in zip(c.CALWEEK, c.week_start)]


def item_desc(material: str) -> str:
    im = adapter.item_master()
    hit = im[im.ITEMCODE == str(material)]
    return None if hit.empty else str(hit.ITEM.iloc[0])


def item_group(material: str):
    p = adapter.kinaxis_plan()
    hit = p[p.MATERIAL == str(material)]
    return None if hit.empty else hit.BSG_GROUP.iloc[0]


def wc_label(wc: str) -> str:
    site = {"BA": "USA2", "SL": "USR2", "PL": "USR2"}.get(wc[:2])
    return f"{wc} · {FACTORY_NAMES.get(site, site)} {WC_NAMES.get(wc[:2], 'line')} ({site})"


def active_lines(factory: str = None, months: int = 3) -> list:
    """Work centres with fixed volume in the latest `months` months, optionally at one factory."""
    att = adapter.attainment()
    latest = int(att.year_month.max())
    y, m = divmod(latest, 100)
    window = []
    for _ in range(months):
        window.append(y * 100 + m)
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    x = att[att.year_month.isin(window)].groupby("work_center").fix.sum()
    lines = sorted(x[x > 0].index)
    if factory:
        pref = {"USR2": ("SL", "PL"), "USA2": ("BA",)}[factory]
        lines = [w for w in lines if w.startswith(pref)]
    return lines


def line_for_materials(materials: list):
    """The work centre that produced most of these materials in the latest 3 months."""
    att = adapter.attainment()
    lines = active_lines()
    x = att[att.material.isin([str(m) for m in materials]) & att.work_center.isin(lines)]
    if x.empty:
        return None
    return x.groupby("work_center").actual.sum().sort_values(ascending=False).index[0]


def lane_sources(material: str, destination: str) -> list:
    ln = adapter.lanes()
    x = ln[(ln.PART == str(material)) & (ln.DESTINATION_SITE == destination) & ln.active]
    x = x.sort_values("LEADTIME DAYS")
    return list(dict.fromkeys(x.SOURCE_SITE.astype(str)))[:2]


def make_buy_lanes(materials: list) -> pd.DataFrame:
    ln = adapter.lanes()
    return ln[ln.PART.isin([str(m) for m in materials]) & ln.TYPE.isin(["Make", "Buy"]) & ln.active]


def pfr_period_bounds():
    d = cuts.pfr_lines().cut_date.dropna()
    return d.min().date(), d.max().date()


def snapshot_dates() -> list:
    """Dates of the weekly Kinaxis loads available for S5 (empty when none were extracted)."""
    try:
        df = adapter.kinaxis_snapshots()
    except FileNotFoundError:
        return []
    return sorted(pd.Timestamp(d).date() for d in df.snapshot_date.unique())
