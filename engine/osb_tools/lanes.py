"""lookup_lane: Kinaxis lane (lead time, MOQ, type) + cheapest shuttle cost from the transport file."""
from . import adapter
from .envelope import cite, envelope, table


def lookup_lane(source: str, destination: str, material: str = None) -> dict:
    ln = adapter.lanes()
    ln = ln[(ln.SOURCE_SITE == source) & (ln.DESTINATION_SITE == destination) & ln.active]
    if material:
        ln = ln[ln.PART == str(material)]
    tr = adapter.transport()
    tr = tr[(tr.sap_kinaxis_code_origin == source) & (tr.sap_kinaxis_code_dest == destination)]
    cheapest = tr.sort_values("cost_per_load_lh_usd").head(1)
    if ln.empty and tr.empty:
        return envelope("lookup_lane", "no_data", values={"source": source, "destination": destination})
    first = ln.iloc[0] if not ln.empty else None
    vals = {"source": source, "destination": destination, "material": material,
            "leadtime_days": None if first is None else int(first["LEADTIME DAYS"]),
            "min_qty": None if first is None else float(first.MINIMUM_QTY),
            "multiple_qty": None if first is None else float(first.MULTIPLE_QTY),
            "type": None if first is None else first.TYPE,
            "cost_per_load": None if cheapest.empty else float(cheapest.cost_per_load_lh_usd.iloc[0]),
            "mode": None if cheapest.empty else cheapest.bid_mode_type.iloc[0]}
    warn = [] if not cheapest.empty else ["No shuttle cost for this lane in the transport file"]
    return envelope("lookup_lane", values=vals, table=table(tr[["bid_mode_type", "historical_equipment_type",
                                                                 "cost_per_load_lh_usd"]]) if not tr.empty else None,
                    rows_used=cite(ln) + cite(cheapest), warnings=warn)
