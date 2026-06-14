"""Pure coaching core: parsing, journal, resilience, recovery.

Everything here is ported near-verbatim from the legacy LXC reference (the
behavioral spec under ../../garmin/opt/garmin-coach/) and pinned with fixture
tests, because these are not architecture — they are specific, debugged
workarounds for a flaky upstream that a clean rewrite would otherwise silently
drop. See the architecture doc's "methodology scar-tissue" note.
"""
