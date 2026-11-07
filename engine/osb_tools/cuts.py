"""S4 cut lines from the PFR extract (Summary ZSD Ship = official PFR on shipment date).
PFR = 1 - cases cut / cases ordered. The file is 180 MB; first load takes several minutes,
then it is cached next to the other caches."""
import pandas as pd

from . import adapter, config
from .envelope import envelope, table

PFR_COLS = ["Material", "Description", "Cut Date Ship.", "OrderQuantity", "Cases Cut Total",
            "Cases Cut Pre OBD", "Cases Cut Post OBD", "Cut Classification", "Final Local RC", "Final RC Desc",
            "Plant", "LC Desc", "Customer L2", "Customer L4", "BSG Group", "Inc. PFR Ship (Y/N)",
            "SalesDocument", "SalesDocumentItem", "ItemGTIN"]


def pfr_lines() -> pd.DataFrame:
    path = adapter.find_file(adapter.schema()["pfr"]["file_keyword"])
    fhash = adapter._hash(path)
    cache = config.CACHE_DIR / f"pfr.ship.{fhash}.pkl"
    if cache.exists():
        return pd.read_pickle(cache)
    # Stream with openpyxl read_only: pd.read_excel on this file needs > 4 GB RAM
    from openpyxl import load_workbook
    ws = load_workbook(path, read_only=True)["Summary ZSD Ship"]
    it = ws.iter_rows(values_only=True)
    hdr = list(next(it))
    if len(hdr) != adapter.schema()["pfr"]["sheets"]["ship"]["header_count"]:
        raise adapter.SchemaError(f"{path.name}: expected 86 headers, got {len(hdr)}")
    idx = [hdr.index(c) for c in PFR_COLS]
    df = pd.DataFrame(([None if r[j] is None else str(r[j]) for j in idx] for r in it), columns=PFR_COLS)
    df["_row"] = df.index + 2
    df["_file"], df["_sheet"], df["_hash"] = path.name, "Summary ZSD Ship", fhash
    for c in ["OrderQuantity", "Cases Cut Total", "Cases Cut Pre OBD", "Cases Cut Post OBD"]:
        df[c] = pd.to_numeric(df[c].str.replace(",", ""), errors="coerce").fillna(0)
    df["cut_date"] = pd.to_datetime(df["Cut Date Ship."], format="%m/%d/%Y", errors="coerce")
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_pickle(cache)
    return df


def cuts_pareto(period_from, period_to, scope="both", top_n=10, pfr_only=True, df=None) -> dict:
    """Pareto of cut cases by item for a period; PFR = 1 - PFR cuts / ordered."""
    df = pfr_lines() if df is None else df
    x = df[(df.cut_date >= pd.Timestamp(period_from)) & (df.cut_date <= pd.Timestamp(period_to))]
    if scope != "both":
        x = x[x["BSG Group"] == scope]
    if x.empty:
        return envelope("cuts_pareto", "no_data", missing=[{"key": "cut_lines", "template": "NO_DATA_CUTS"}])
    # Official PFR: NonPFR cuts (e.g. order rectification) do not count against service
    x = x.assign(pfr_cut=x["Cases Cut Total"].where(x["Cut Classification"] != "NonPFR", 0) if pfr_only
                 else x["Cases Cut Total"])
    total_cut, total_ord = x["pfr_cut"].sum(), x["OrderQuantity"].sum()
    g = (x.groupby(["Material", "Description"])[["pfr_cut", "OrderQuantity"]].sum()
         .sort_values("pfr_cut", ascending=False).reset_index())
    g.columns = ["material", "description", "cases_cut", "cases_ordered"]
    rc = (x[x.pfr_cut > 0].groupby(["Material", "Final Local RC"]).pfr_cut.sum().reset_index()
          .sort_values("pfr_cut", ascending=False).drop_duplicates("Material").set_index("Material")["Final Local RC"])
    g["top_reason_code"] = g.material.map(rc)
    g["share"] = (g.cases_cut / total_cut * 100).round(1)
    g["cumulative_share"] = g.share.cumsum().round(1)
    g = g.head(top_n)
    return envelope("cuts_pareto",
                    values={"period_from": str(period_from), "period_to": str(period_to), "scope": scope,
                            "total_cut": int(total_cut), "total_ordered": int(total_ord),
                            "pfr_pct": round(float(1 - total_cut / total_ord) * 100, 1) if total_ord else None,
                            "pfr_basis": "NonPFR cuts excluded" if pfr_only else "all cuts"},
                    table=table(g), rows_used=[{"source_file": x["_file"].iloc[0], "source_sheet": x["_sheet"].iloc[0],
                                                "rows": int(len(x)), "hash": x["_hash"].iloc[0]}])


def _plants(lc):
    return {lc, config.SITES.get(lc, {}).get("sap_plant", lc)}


def _cut_rows(lc=None, cut_date=None, material=None, df=None):
    df = pfr_lines() if df is None else df
    x = df[df["Cases Cut Total"] > 0]
    if lc:
        x = x[x["Plant"].astype(str).isin(_plants(lc))]
    if cut_date is not None:
        x = x[x.cut_date.dt.normalize() == pd.Timestamp(cut_date).normalize()]
    if material:
        x = x[x["Material"].astype(str) == str(material)]
    return x


def list_cut_lines(lc: str, cut_date: str, top_n: int = 5, df=None) -> dict:
    """S4: cut lines at one LC on one day, largest first (summed per item)."""
    x = _cut_rows(lc, cut_date, df=df)
    if x.empty:
        return envelope("list_cut_lines", "no_data", values={"lc": lc, "cut_date": str(cut_date)},
                        missing=[{"key": "cut_lines", "template": "NO_DATA_CUTS"}])
    g = (x.groupby(["Material", "Description"])[["Cases Cut Total", "OrderQuantity"]].sum()
         .sort_values("Cases Cut Total", ascending=False).reset_index().head(top_n))
    g.columns = ["material", "description", "cases_cut", "cases_ordered"]
    return envelope("list_cut_lines", values={"lc": lc, "cut_date": str(cut_date), "lines": int(len(x))},
                    table=table(g), rows_used=[{"source_file": r["_file"], "source_sheet": r["_sheet"],
                                                "source_row": int(r["_row"]), "hash": r["_hash"]}
                                               for r in x.head(200).to_dict("records")])


def get_cut_line(material: str, lc: str, cut_date: str, df=None) -> dict:
    """S4: one item's cut at one LC on one day from the PFR extract (order lines summed)."""
    x = _cut_rows(lc, cut_date, material, df=df)
    if x.empty:
        return envelope("get_cut_line", "no_data", values={"material": str(material), "lc": lc, "cut_date": str(cut_date)},
                        missing=[{"key": "cut_lines", "template": "NO_DATA_CUTS"}])
    return envelope("get_cut_line",
                    values={"cut_id": f"pfr:{material}:{lc}:{cut_date}", "material": str(material), "lc": lc,
                            "cut_date": str(cut_date), "description": str(x["Description"].iloc[0]),
                            "cases_cut": int(x["Cases Cut Total"].sum()), "cases_ordered": int(x["OrderQuantity"].sum()),
                            "order_lines": int(len(x)), "recorded_rc": x["Final Local RC"].dropna().astype(str).mode().tolist()[:1]},
                    rows_used=[{"source_file": r["_file"], "source_sheet": r["_sheet"], "source_row": int(r["_row"]),
                                "hash": r["_hash"]} for r in x.to_dict("records")])


LC_OF_PLANT = {v["sap_plant"]: k for k, v in config.SITES.items()} | {k: k for k in config.SITES}


def pfr_drilldown(period_from, period_to, scope="both", top_n=5, df=None) -> dict:
    """S4, workflow doc steps 2-6 for the top cutting items: cover now (>= 2 weeks: stock exists, LC
    or order issue; < 2: supply), orders against the lag-1 forecast (demand spike vs supply
    shortfall), the customer with most cuts, the recovery week in the plan, the largest cut line, and
    the reason code the tree's rules give (R03 pacing above 130% -> B2; R06 attainment under 90% -> C0)."""
    from .attainment import lines_for_sku, monthly
    from .woc import add_woc, series
    df = pfr_lines() if df is None else df
    base = cuts_pareto(period_from, period_to, scope, top_n=top_n, df=df)
    if base["status"] != "ok":
        return base
    plan = adapter.kinaxis_plan()
    try:
        lag = adapter.lag_forecast()
    except FileNotFoundError:
        lag = pd.DataFrame(columns=["material", "month", "lag1_forecast_cases"])
    att = adapter.attainment()
    p0, p1 = pd.Timestamp(period_from), pd.Timestamp(period_to)
    x = df[(df.cut_date >= p0) & (df.cut_date <= p1)]
    if scope != "both":
        x = x[x["BSG Group"] == scope]
    months = sorted({str(d.to_period("M")) for d in pd.date_range(p0, p1, freq="D")})
    ym = [int(m.replace("-", "")) for m in months]
    items = []
    for r in base["table"]["rows"]:
        cols = base["table"]["columns"]
        rec = dict(zip(cols, r))
        m = str(rec["material"])
        xi = x[x["Material"].astype(str) == m]
        ordered = float(xi["OrderQuantity"].sum())
        fc = float(lag[(lag.material == m) & lag.month.isin(months)].lag1_forecast_cases.sum()) if not lag.empty else 0.0
        pacing = round(ordered / fc * 100) if fc else None
        net = series(plan, m, "NETWORK")
        w = add_woc(net) if not net.empty else net
        woc_now = None if net.empty or w.woc.iloc[0] is None else round(float(w.woc.iloc[0]), 1)
        lm = lag[(lag.material == m) & lag.month.isin(months)] if not lag.empty else lag
        woc_start = None if lm.empty or "woc_start" not in lm or pd.isna(lm.woc_start.iloc[0]) else float(lm.woc_start.iloc[0])
        rec_week = None
        if not net.empty:
            ok = w[[v is not None and round(v, 1) >= 2.0 for v in w.woc]]
            rec_week = int(ok.CALWEEK.iloc[0]) if not ok.empty else None
        cust = (xi[xi["Cases Cut Total"] > 0].groupby("Customer L2")["Cases Cut Total"].sum().sort_values(ascending=False))
        top_c = None if cust.empty else {"customer": str(cust.index[0]), "cases_cut": int(cust.iloc[0]),
                                          "share_pct": round(float(cust.iloc[0] / cust.sum() * 100))}
        lines = lines_for_sku(m, att)
        low_att = []
        for wc in lines:
            g = monthly(wc, att)
            g = g[g.year_month.isin(ym) & (g.fix_cases > 0)]
            low_att += [{"work_center": wc, "year_month": int(rr.year_month), "attainment_pct": round(rr.attainment_pct, 1)}
                        for rr in g.itertuples() if rr.attainment_pct < 90]
        # Workflow doc S4: cover at the start of the month first; under 2 weeks, orders vs lag-1 forecast
        if woc_start is None and net.empty:
            branch, code, rule = "not in the current Kinaxis plan or forecast (possibly discontinued)", None, "check R02b"
        elif woc_start is not None and woc_start >= 2:
            branch, code, rule = "stock existed at the start of the month: points to an LC or order issue", None, "walk each cut"
        elif pacing is not None and pacing > 130:
            branch, code, rule = "demand spike: orders above 130% of the lag-1 forecast", "B2", "R03"
        elif low_att:
            branch, code, rule = "supply shortfall: factory attainment under 90%", "C0", "R06"
        else:
            branch, code, rule = "supply shortfall", None, "walk each cut"
        big = xi.sort_values("Cases Cut Total", ascending=False).head(1)
        biggest = None if big.empty else {"lc": LC_OF_PLANT.get(str(big["Plant"].iloc[0]), str(big["Plant"].iloc[0])),
                                           "cut_date": str(big.cut_date.iloc[0].date()), "cases_cut": int(big["Cases Cut Total"].iloc[0])}
        items.append({"material": m, "description": rec["description"], "cases_cut": int(rec["cases_cut"]),
                      "share": rec["share"], "woc_now": woc_now, "woc_start": woc_start, "ordered_cases": int(ordered),
                      "lag1_forecast_cases": int(round(fc)) if fc else None, "orders_pct_of_forecast": pacing,
                      "branch": branch, "suggested_code": code, "rule": rule, "recovery_week": rec_week,
                      "top_customer": top_c, "attainment_below_90": low_att[:3], "largest_cut": biggest})
    v = base["values"]
    top_share = round(sum(i["share"] for i in items), 1)
    return envelope("pfr_drilldown", values={**v, "top_n": len(items), "top_share_pct": top_share, "months": months,
                                             "items": items}, table=base["table"], rows_used=base["rows_used"])
