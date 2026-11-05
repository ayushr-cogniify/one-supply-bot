"""Fixed response templates (Engineering appendix A.4). Returned by code, never generated."""

T = {
    "OUT_OF_SCOPE": ("I can only help with the five One Supply Bot scenarios: S1 in-month demand spike, S2 factory "
                     "attainment drop, S3 segment growth vs line capacity, S4 PFR root cause and reason coding, "
                     "S5 what changed this week. Which one would you like to run?"),
    "NO_DATA_ITEM": ("{item_text} is not in the loaded extracts. You can add it in Reference tables > Items, or give "
                     "me a GIC, UPC or GTIN to try again."),
    "NO_DATA_CAPACITY": ("I cannot compute the capacity gap for {work_center} because rated capacity is not in any "
                         "extract. Enter units per month and the unit basis, and I will continue."),
    "NO_DATA_CUTS": ("No service-cut lines are loaded for {period}. Upload a cut file or enter lines in the Cut Lines "
                     "grid (item, LC, cut date, cases ordered, cases cut) and ask again."),
    "NO_DATA_PACING": ("To decide between B2, B3 and the stock branch for {item} in {period} I need ordered % of "
                       "lag-1 forecast. Was {scope} ordering above 130% of forecast?"),
    "NO_SECOND_SNAPSHOT": ("Only the {snapshot_week} Kinaxis snapshot is loaded. I can compare SAP stock on hand "
                           "between two dates instead, with demand drivers marked unavailable, or you can upload a "
                           "second Kinaxis extract. Which do you want?"),
    "VALIDATOR_FAIL": ("I could not produce a narrative whose numbers all trace to the tool results. Here are the "
                       "computed tables; every value in them carries its source row."),
    "CLARIFY_ITEM": "I found more than one match for {item_text}. Which one?",
    "CLARIFY_WEEK": "Which week should I apply the change to? Give an ISO week, a date, or “week of mm/dd”.",
    "CLARIFY_SCOPE": ("Should I run this for the whole US network or one LC (Harrisburg USP6, Atlanta USP8, "
                      "St. Peters USQ2, North Las Vegas USQ4)?"),
    "CLARIFY_LC": ("A demand change has to land on one LC. Which LC takes the extra demand (Harrisburg USP6, "
                   "Atlanta USP8, St. Peters USQ2, North Las Vegas USQ4)?"),
    "CLARIFY_QTY": "How many cases? Give a signed number, for example +5,000 or -2,000.",
    "CLARIFY_LINE": "{site} runs more than one line in the attainment history. Which line should take the attainment change?",
    "CLARIFY_PCT": "What attainment should I apply to {work_center}? Give a percentage, for example 80%.",
    "CLARIFY_GROWTH": "What growth should I apply? Give a percentage, for example +35%.",
    "CLARIFY_SEGMENT": "Which segment or size? For example 90 oz laundry sanitizer.",
    "CLARIFY_CUT": "Which cut should I code? Give the item, the LC and the cut date, for example “10/15 cut of Crisp Linen 6x21 at Vegas”.",
    "CLARIFY_S5": "Do you want the changes for your PIR items, or for every item at the four LCs?",
    "QUESTION_CAP": ("I have asked three questions on this request already. Restate it with the item, quantity, week "
                     "and LC in one message and I will run it."),
    "ACTION_PENDING": "Drafted {action_type} for your review. Nothing is sent or written until you approve it.",
    "APPROVED": "Approved. {action_type} recorded as {record_id} in the Approvals sheet and the audit log.",
    "REJECTED": "Rejected. Nothing was written. Pick an alternate code or leave the cut uncoded.",
    "SAVED_FACT": "Saved to the {sheet} sheet with your name and the time. Marked planner-entered.",
    "WELCOME": ("Ask about any of the five scenarios, or pick a question above. Everything I show comes from your "
                "extracts or from what you enter. If I am missing something, I will ask for it."),
}


def render(key: str, **kw) -> str:
    return T[key].format(**kw)
