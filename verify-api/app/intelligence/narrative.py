"""Narrative template engine. No Ollama. Templates inject library fields."""

_TEMPLATES: dict[str, dict[str, str]] = {
    "digital_asset": {
        "red": (
            "Lexi reads a digital asset transaction to '{merchant}'. "
            "{library_detail}"
            "This represents undisclosed cryptocurrency exposure. "
            "Obtain a full exchange account history via {recovery_action}."
        ),
        "amber": (
            "Lexi reads a possible digital asset transaction to '{merchant}'. "
            "Keyword pattern match — manual verification required. "
            "If confirmed, obtain account records and assess off-balance-sheet exposure."
        ),
    },
    "intercompany": {
        "red": (
            "Lexi reads recurring transfers totalling ${total:,.0f} to '{merchant}', "
            "an entity absent from the disclosed corporate structure. "
            "Pattern is consistent with undisclosed related-party transactions or earnings manipulation. "
            "Require a full intercompany reconciliation and entity registry search."
        ),
        "amber": (
            "Lexi reads recurring transfers totalling ${total:,.0f} to '{merchant}'. "
            "Verify intercompany agreement, transfer pricing policy, and eliminate from consolidated financials."
        ),
    },
    "vendor": {
        "red": (
            "Lexi reads a payment of ${total:,.0f} to '{merchant}', absent from the disclosed vendor register. "
            "The corporate structure of the payee warrants investigation — verify registration, directors, "
            "and relationship to the target's principals."
        ),
        "amber": (
            "Lexi reads a payment of ${total:,.0f} to '{merchant}', not in the disclosed vendor register. "
            "Obtain invoice and verify business purpose."
        ),
    },
    "cash_flow": {
        "amber": (
            "Lexi reads an anomalous cash flow pattern: {description} "
            "This warrants review of customer contracts, revenue recognition policy, "
            "and working capital management."
        ),
    },
    "liability": {
        "amber": (
            "Lexi reads recurring payments to '{merchant}' totalling ${total:,.0f}, "
            "consistent with an undisclosed financing obligation. "
            "Cross-reference against the disclosed debt schedule and request all loan agreements."
        ),
    },
    "behavioural": {
        "red": (
            "Lexi reads a material single outflow of ${total:,.0f} to '{merchant}'. "
            "Obtain supporting documentation, verify recipient identity, and assess counterparty risk."
        ),
        "amber": (
            "Lexi reads a high-velocity payment pattern to '{merchant}'. "
            "{description} "
            "This pattern warrants review for structuring intent."
        ),
    },
}


def generate_narrative(signal: dict) -> str:
    signal_type = signal.get("signal_type", "")
    severity = signal.get("severity", "amber")
    merchant = signal.get("merchant", "UNKNOWN")
    amount = abs(signal.get("amount", 0))
    description = signal.get("description", "")

    library_detail = ""
    recovery_action = "voluntary disclosure or court order"
    if signal.get("library_match") == "crypto_exchange_library":
        library_detail = "Exchange identified via Lexi intelligence libraries. "
        recovery_action = "subpoena or voluntary exchange disclosure"

    templates = _TEMPLATES.get(signal_type, {})
    template = templates.get(severity, templates.get("amber", "{description}"))

    try:
        return template.format(
            total=amount,
            merchant=merchant,
            library_detail=library_detail,
            recovery_action=recovery_action,
            description=description,
        )
    except KeyError:
        return description
