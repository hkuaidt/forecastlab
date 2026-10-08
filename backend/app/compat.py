"""Read compatibility for identifiers stored by early development releases."""

DEMO_CASE_ID = "question-evidence-demo"
LEGACY_DEMO_CASE_ID = "agent12-release"
LEGACY_EXAMPLE_KEY = "agent12_demo"


def canonical_demo_case_id(value: str | None) -> str | None:
    return DEMO_CASE_ID if value == LEGACY_DEMO_CASE_ID else value
