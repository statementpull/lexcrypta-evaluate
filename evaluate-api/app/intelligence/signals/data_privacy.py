"""Signal 39: Data Privacy & Cybersecurity Breach Risk.

Data privacy obligations follow the business entity, not the owner. An
acquirer inherits all pre-closing data breaches, privacy violations, and
regulatory non-compliance — including class action exposure.

Key regulations (successor liability):
  GDPR (EU): €20M or 4% global turnover fines — applies to any business
    processing EU resident data. Acquirer must conduct GDPR transfer impact
    assessment.
  CCPA/CPRA (California): $2,500–$7,500 per intentional violation. Private
    right of action for data breaches.
  HIPAA: Business associate agreements (BAAs) must be novated. Prior breaches
    may have unreported OCR investigations.
  PCI-DSS: Payment card data non-compliance creates ongoing liability.
    Card brands can fine acquirers for prior non-compliance.
  State breach notification laws: 50 states have varying requirements —
    a past breach may have triggered notification obligations that weren't met.

What we detect from financial data:
  Cyber forensics payments: Indicates a past breach investigation.
  Breach notification costs: Legal, credit monitoring, notification services.
  Privacy compliance consultants: Indicates active compliance gaps.
  Data protection officer (DPO) costs: GDPR compliance activity.
  OCR/FTC settlements: Regulatory enforcement history.

Language principle: We surface anomalies for the deal team to verify.
We do not conclude — every flag identifies a pattern that warrants investigation.
"""
from collections import defaultdict

BREACH_RESPONSE_KW = [
    "CYBER FORENSICS", "BREACH INVESTIGATION", "INCIDENT RESPONSE",
    "CYBER INCIDENT", "DATA BREACH", "FORENSICS FIRM",
    "MANDIANT", "CROWDSTRIKE SERVICES", "KROLL CYBER", "PALO ALTO RESPONSE",
    "CREDIT MONITORING", "IDENTITY THEFT PROTECTION", "EXPERIAN BREACH",
    "TRANSUNION BREACH", "EQUIFAX BREACH",
    "BREACH NOTIFICATION", "NOTIFICATION SERVICE", "BREACH COUNSEL",
]

PRIVACY_COMPLIANCE_KW = [
    "GDPR COMPLIANCE", "CCPA COMPLIANCE", "HIPAA COMPLIANCE",
    "DATA PROTECTION", "PRIVACY COMPLIANCE", "PRIVACY AUDIT",
    "DATA PRIVACY COUNSEL", "DPO ", "DATA PROTECTION OFFICER",
    "PRIVACY SHIELD", "SCCs ", "TRANSFER IMPACT",
    "ONETRUST", "TRUSTARC", "IUBENDA", "COOKIEBOT",
]

HIPAA_KW = [
    "HIPAA ", "OCR SETTLEMENT", "OFFICE FOR CIVIL RIGHTS",
    "BUSINESS ASSOCIATE", "BAA ", "PHI ", "COVERED ENTITY",
]

PCI_KW = [
    "PCI ", "PCI-DSS", "PCI COMPLIANCE", "QSA ", "QUALIFIED SECURITY",
    "PCI SCAN", "ASV SCAN",
]

RANSOM_KW = [
    "RANSOMWARE", "RANSOM PAYMENT", "BITCOIN RANSOM",
    "EXTORTION PAYMENT", "CYBER RANSOM",
]


def run(transactions: list[dict], pl_rows: list[dict] | None = None, loader=None) -> list[dict]:
    if not transactions:
        return []

    results = []
    breach_txns, privacy_txns, hipaa_txns, pci_txns, ransom_txns = [], [], [], [], []

    for t in transactions:
        merchant = t["merchant"].upper()
        if any(kw in merchant for kw in BREACH_RESPONSE_KW):
            breach_txns.append(t)
        if any(kw in merchant for kw in PRIVACY_COMPLIANCE_KW):
            privacy_txns.append(t)
        if any(kw in merchant for kw in HIPAA_KW):
            hipaa_txns.append(t)
        if any(kw in merchant for kw in PCI_KW):
            pci_txns.append(t)
        if any(kw in merchant for kw in RANSOM_KW):
            ransom_txns.append(t)

    # ── Breach response (past incident confirmed) ─────────────────────────────
    if breach_txns:
        total = sum(abs(t["amount"]) for t in breach_txns)
        results.append({
            "signal_type": "data_privacy",
            "severity": "red",
            "merchant": f"CYBERSECURITY BREACH RESPONSE: ${total:,.0f} — past incident confirmed",
            "amount": -total,
            "transaction_date": breach_txns[0].get("transaction_date", ""),
            "description": (
                f"Cyber breach response payments: {len(breach_txns)} transactions totalling ${total:,.0f}. "
                "These payments confirm a past cybersecurity incident. "
                "Successor liability: (1) unresolved regulatory investigations from the breach "
                "transfer to the acquirer, (2) class action claims from affected data subjects "
                "survive the acquisition, (3) PCI card brand fines may still be outstanding, "
                "(4) state AG investigations may be ongoing. "
                "Mandatory pre-close actions: (1) full cyber forensics report and root cause analysis, "
                "(2) confirmation all breach notifications were completed per state law, "
                "(3) confirmation no open regulatory investigations exist, "
                "(4) rep and warranty insurance exclusion for known breaches — negotiate specific "
                "indemnification from seller for pre-closing breach liability."
            ),
            "library_match": "CYBER_BREACH_CONFIRMED",
            "confidence_weight": 0.90,
        })

    # ── Ransom payments ───────────────────────────────────────────────────────
    if ransom_txns:
        total = sum(abs(t["amount"]) for t in ransom_txns)
        results.append({
            "signal_type": "data_privacy",
            "severity": "red",
            "merchant": f"RANSOM / EXTORTION PAYMENT: ${total:,.0f}",
            "amount": -total,
            "transaction_date": ransom_txns[0].get("transaction_date", ""),
            "description": (
                f"Potential ransomware or extortion payment: ${total:,.0f}. "
                "CRITICAL: Ransom payments to sanctioned threat actors (e.g., REvil, LockBit, "
                "entities on OFAC SDN List) are federal violations — the business may have "
                "committed an OFAC violation. Acquirer must assess this before proceeding. "
                "Additionally: (1) system may not be fully remediated — residual access exists, "
                "(2) data exfiltrated before encryption may be on dark web, "
                "(3) cyber insurer may have denied coverage — outstanding claim. "
                "Engage cybersecurity counsel and OFAC counsel immediately."
            ),
            "library_match": "CYBER_RANSOM",
            "confidence_weight": 0.90,
        })

    # ── HIPAA ─────────────────────────────────────────────────────────────────
    if hipaa_txns:
        total = sum(abs(t["amount"]) for t in hipaa_txns)
        results.append({
            "signal_type": "data_privacy",
            "severity": "red",
            "merchant": f"HIPAA / HEALTHCARE PRIVACY: ${total:,.0f}",
            "amount": -total,
            "transaction_date": hipaa_txns[0].get("transaction_date", ""),
            "description": (
                f"HIPAA-related payments: ${total:,.0f}. "
                "Healthcare businesses with PHI (Protected Health Information) obligations: "
                "(1) all Business Associate Agreements (BAAs) must be novated to new owner, "
                "(2) HIPAA Security Rule requires risk analysis — obtain current assessment, "
                "(3) Breach Notification Rule: any prior breaches of >500 individuals are on "
                "the OCR breach portal (publicly searchable at HHS.gov), "
                "(4) OCR investigation history must be disclosed and assessed, "
                "(5) minimum necessary standard and data retention policies must continue under new ownership. "
                "HIPAA violations carry fines up to $1.9M per violation category per year."
            ),
            "library_match": "HIPAA_COMPLIANCE",
            "confidence_weight": 0.80,
        })

    # ── PCI compliance ────────────────────────────────────────────────────────
    if pci_txns:
        total = sum(abs(t["amount"]) for t in pci_txns)
        results.append({
            "signal_type": "data_privacy",
            "severity": "amber",
            "merchant": f"PCI-DSS COMPLIANCE ACTIVITY: ${total:,.0f}",
            "amount": -total,
            "transaction_date": pci_txns[0].get("transaction_date", ""),
            "description": (
                f"PCI-DSS compliance payments: ${total:,.0f}. "
                "Payment card industry compliance is a contractual obligation with card brands — "
                "non-compliance creates fines of $5,000–$100,000/month plus liability for fraud losses. "
                "At acquisition: (1) confirm current SAQ (Self-Assessment Questionnaire) or QSA report, "
                "(2) verify no open card brand investigations or fines, "
                "(3) confirm new payment processing agreement is in place post-close "
                "(merchant agreements terminate on ownership change with most processors)."
            ),
            "library_match": "PCI_COMPLIANCE",
            "confidence_weight": 0.65,
        })

    # ── Privacy compliance activity ───────────────────────────────────────────
    if privacy_txns and not breach_txns:
        total = sum(abs(t["amount"]) for t in privacy_txns)
        results.append({
            "signal_type": "data_privacy",
            "severity": "amber",
            "merchant": f"DATA PRIVACY COMPLIANCE SPEND: ${total:,.0f}",
            "amount": -total,
            "transaction_date": privacy_txns[0].get("transaction_date", ""),
            "description": (
                f"Data privacy compliance spend: ${total:,.0f}. "
                "Active privacy compliance work indicates the business processes significant "
                "personal data with regulatory obligations. "
                "At acquisition: (1) obtain privacy impact assessment and data mapping inventory, "
                "(2) confirm GDPR transfer mechanisms (SCCs) are in place if EU data is processed, "
                "(3) verify CCPA/CPRA opt-out mechanisms are functioning, "
                "(4) confirm data retention and deletion policies are implemented, "
                "(5) update privacy notices to reflect new ownership post-close."
            ),
            "library_match": "DATA_PRIVACY_COMPLIANCE",
            "confidence_weight": 0.55,
        })

    return results
