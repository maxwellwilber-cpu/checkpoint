#!/usr/bin/env python3
"""
A worked example: validating an LLM-generated business analysis.

Run it:

    python examples/validate_llm_output.py

Three outputs are checked against the same ruleset. All three would pass ordinary schema
validation — they have the right fields, the right types, the right shape. Two of them
are wrong anyway, in the specific ways models are wrong:

    clean     correct
    subtle    every number is plausible, one is invented
    sloppy    template text left in, invented enum value, citation pointing nowhere

The point of the example is that the difference between these is invisible to a reader
skimming the output, and invisible to a JSON schema. It is not invisible to a rule that
asks where the numbers came from.
"""

import json
import os
import sys

# Run from anywhere without installing the package first.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import checkpoint as cp
from checkpoint import checks as C, rule, RuleSet
from checkpoint.types import Severity


# The source data the model was given. Everything it says should trace back here.
SOURCE = {
    "company": "Northline Catering",
    "monthly_revenue": [82000, 91000, 78000, 104000],
    "monthly_costs": [61000, 67000, 63000, 71000],
    "headcount": 14,
    "sources": [
        {"id": "src-pl", "name": "P&L export 2026"},
        {"id": "src-payroll", "name": "Payroll summary"},
    ],
}

SOURCE_IDS = [s["id"] for s in SOURCE["sources"]]


# --- A custom rule, to show the escape hatch -------------------------------------
# The built-in checks cover structure and grounding. Anything domain-specific is a
# five-line function.

@rule(severity=Severity.BLOCKER)
def recommendations_are_actionable(data):
    """Every recommendation must state an action, not an observation."""
    weak_openers = ("consider", "think about", "it may be worth", "perhaps")
    problems = []
    for i, rec in enumerate(data.get("recommendations", [])):
        text = rec.get("action", "")
        if text.lower().startswith(weak_openers):
            problems.append((f"recommendations[{i}].action",
                             f"hedged rather than actionable: {text[:60]!r}"))
    return problems


def build_ruleset():
    """The rules an analysis must satisfy before anyone is allowed to read it."""
    return RuleSet("business_analysis").add(
        # Structure
        C.required("company", "summary", "findings", "recommendations"),
        C.not_empty("summary", "findings"),
        C.of_type("findings", list),

        # Content sanity
        C.word_count("summary", minimum=20, maximum=150),
        C.one_of("confidence", ["high", "medium", "low"]),
        C.numeric_range("findings[].impact_usd", minimum=0),

        # The three that catch what schemas cannot
        C.no_placeholders(),
        C.citations_resolve("findings[]", "source_id", SOURCE_IDS),

        # Grounding is SCOPED, and the scope is the whole design decision.
        #
        # It applies to claims ABOUT the data: the summary, the findings, the impact
        # figures. It must NOT apply to recommendations, because a recommendation
        # proposes something that has not happened yet — "move 3 of the 14 roles to
        # variable scheduling" introduces a 3 that is supposed to be new.
        #
        # Running this unscoped flags that 3 as a hallucination. It is not; it is a
        # proposal. Grounding answers "is this claim supported?", which is only a
        # meaningful question about statements of fact.
        C.grounded_numbers(
            SOURCE,
            paths=["summary", "findings[].text", "findings[].impact_usd"],
            tolerance=0.01,
        ),

        # Domain judgment
        recommendations_are_actionable,
    )


CLEAN = {
    "company": "Northline Catering",
    "summary": ("Revenue across the four months ranged from 78000 to 104000 while costs "
                "stayed between 61000 and 71000. The business is profitable every month, "
                "but margin swings with revenue because costs barely move."),
    "confidence": "high",
    "findings": [
        {"text": "Costs are close to fixed month to month.",
         "impact_usd": 71000, "source_id": "src-pl"},
        {"text": "Headcount of 14 does not flex with demand.",
         "impact_usd": 0, "source_id": "src-payroll"},
    ],
    "recommendations": [
        {"action": "Move 3 of the 14 roles to variable scheduling before the next slow month."},
    ],
}

SUBTLE = {
    "company": "Northline Catering",
    "summary": ("Revenue across the four months ranged from 78000 to 104000 while costs "
                "stayed between 61000 and 71000. Average monthly profit was 27400, and "
                "the business is profitable every month."),
    "confidence": "high",
    "findings": [
        {"text": "Costs are close to fixed month to month.",
         "impact_usd": 71000, "source_id": "src-pl"},
    ],
    "recommendations": [
        {"action": "Move 3 of the 14 roles to variable scheduling before the next slow month."},
    ],
}

SLOPPY = {
    "company": "Northline Catering",
    "summary": ("Revenue for [INSERT PERIOD] ranged from 78000 to 104000. Costs stayed "
                "between 61000 and 71000 across the period under review here."),
    "confidence": "fairly high",
    "findings": [
        {"text": "Margins are compressing.", "impact_usd": -4000, "source_id": "src-forecast"},
    ],
    "recommendations": [
        {"action": "Consider looking at staffing levels."},
    ],
}


def show(label, data, ruleset):
    report = cp.validate(data, ruleset, subject=label)
    print("=" * 70)
    print(f"  {report.summary()}")
    print("=" * 70)
    if not report.findings:
        print("  nothing to report\n")
        return report
    for finding in report.findings:
        print(f"  {finding}")
    print()
    return report


def main():
    ruleset = build_ruleset()
    print(f"\nRuleset: {ruleset.name} — {len(ruleset)} rules\n")

    clean = show("clean", CLEAN, ruleset)
    subtle = show("subtle", SUBTLE, ruleset)
    sloppy = show("sloppy", SLOPPY, ruleset)

    print("=" * 70)
    print("  WHAT THIS DEMONSTRATES")
    print("=" * 70)
    print("""
  All three outputs are well-formed JSON with the right fields and types.
  A JSON-schema validator passes all three.

  'subtle' differs from 'clean' by one sentence: an average monthly profit of
  27400. It is the right magnitude, correctly formatted, and reads as a
  conclusion the analysis would reasonably reach. It is also not in the source
  data and not derivable by any stated method. That is the failure mode that
  reaches customers, because nothing about it looks wrong.

  'sloppy' fails loudly and is therefore the safer of the two.

  Note how grounded_numbers is scoped. Pointed at the recommendations it would
  flag "move 3 of the 14 roles" as invented, because the 3 is invented -- that
  is what a proposal is. Grounding is a question about claims of fact, so the
  scope has to say which fields are claims.

  Machine-readable output for CI:
""")
    print("  " + json.dumps(subtle.to_dict()["counts"]))
    return 0 if clean.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
