# Phase 5 — role-based company filters

ROLE_DESKS = {
    "consumer": ["consumer"],
    "health_industrials": ["health_industrials"],
    "tech_telecom": ["tech_telecom"],
    "lead": [
        "consumer",
        "health_industrials",
        "tech_telecom",
    ],
}


def desks_for(role):
    key = (role or "").strip().lower()
    if key not in ROLE_DESKS:
        raise ValueError(f"unknown role: {role}")
    return list(ROLE_DESKS[key])
