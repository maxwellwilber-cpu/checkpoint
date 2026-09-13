"""
Running a ruleset over data.

Small on purpose. All the intelligence lives in the rules; the runner's only jobs are
to execute every rule even when one of them fails, and to collect the findings in a
stable order.
"""

from .rules import RuleSet
from .types import Report


def validate(data, rules, subject=None):
    """
    Run rules against data and return a Report.

    Args:
        data:    the output being validated
        rules:   a RuleSet, a list of Rules, or a single Rule
        subject: optional label for the report, e.g. a run id or filename

    Every rule runs even if earlier ones failed. Stopping at the first blocker would
    hide the rest of the problems, which turns fixing a bad output into a slow game of
    one-at-a-time whack-a-mole.
    """
    ruleset = _coerce(rules)
    report = Report(subject=subject)
    for item in ruleset:
        report.findings.extend(item.run(data))
        report.checks_run += 1
    return report


def validate_many(items, rules, key=None):
    """
    Validate a batch, returning {subject: Report}.

    `key` extracts a label from each item; by default the list index is used.
    """
    reports = {}
    for i, item in enumerate(items):
        subject = key(item) if key else str(i)
        reports[subject] = validate(item, rules, subject=subject)
    return reports


def _coerce(rules):
    if isinstance(rules, RuleSet):
        return rules
    if isinstance(rules, (list, tuple)):
        return RuleSet("adhoc", rules)
    return RuleSet("adhoc", [rules])
