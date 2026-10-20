"""Lazy boundary to the existing device provider; no capture implementation here."""

def legacy_provider():
    from ... import roglife_appcheck
    return roglife_appcheck
