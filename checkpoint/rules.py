"""
Rules: a check function plus the metadata that makes its output useful.

A bare function that returns True or False is not enough to build a report from. When
it fails you need to know what it was called, how serious it is, and where the problem
was. A Rule carries that, so the runner can stay dumb and the report can stay rich.
"""

import re

from .types import Finding, Severity

_PATHISH = re.compile(r"^[\w.\[\]-]*$")


def _looks_like_path(value):
    """A path has no spaces. A message almost always does."""
    return isinstance(value, str) and bool(_PATHISH.match(value))


class Rule:
    """
    One named validation rule.

    The wrapped function receives the whole document and returns findings. It may return:
        - None or []           nothing wrong
        - a string             one problem, described
        - (path, message)      one problem, located
        - a list of either     several problems

    Accepting all four shapes is deliberate. The most common rule anyone writes is one
    line long, and forcing it to construct Finding objects is the kind of friction that
    stops people writing rules at all. The runner normalizes whatever comes back.
    """

    def __init__(self, func, check_id=None, severity=Severity.BLOCKER, description=None):
        self.func = func
        self.check_id = check_id or func.__name__
        self.severity = severity
        self.description = description or (func.__doc__ or "").strip().split("\n")[0]

    def run(self, data):
        """Execute and normalize the result into a list of Finding objects."""
        try:
            raw = self.func(data)
        except Exception as exc:
            # A rule that crashes is itself a finding. Swallowing the exception would
            # silently skip the check and report a clean pass, which is the single most
            # dangerous thing a validation library can do.
            #
            # Built-in checks handle a malformed item per item rather than letting it
            # abort the loop, so reaching here means the rule itself is broken, not the
            # data. See _safe_item in checks.py.
            return [Finding(
                check_id=self.check_id,
                severity=Severity.BLOCKER,
                message=f"rule raised {type(exc).__name__}: {exc}",
            )]
        return self._normalize(raw)

    def _normalize(self, raw):
        if raw is None:
            return []
        if isinstance(raw, Finding):
            return [raw]
        if isinstance(raw, str):
            return [Finding(self.check_id, self.severity, raw)]
        if isinstance(raw, tuple) and len(raw) == 2:
            path, message = raw
            # A 2-tuple is (path, message). If the first element does not look like a
            # path, this is two messages and collapsing them would silently lose one.
            if _looks_like_path(path):
                return [Finding(self.check_id, self.severity, message, path=path)]
            return [Finding(self.check_id, self.severity, str(m)) for m in raw]
        if isinstance(raw, (list, tuple)):
            findings = []
            for item in raw:
                findings.extend(self._normalize(item))
            return findings
        return [Finding(self.check_id, self.severity, str(raw))]

    def __repr__(self):
        return f"<Rule {self.check_id} [{self.severity.value}]>"


def rule(check_id=None, severity=Severity.BLOCKER, description=None):
    """
    Decorator for defining a rule.

        @rule(severity=Severity.WARNING)
        def summary_is_not_empty(data):
            "Summary must contain text."
            if not data.get("summary", "").strip():
                return "summary is empty"

    The decorated function still works as a plain function, so it stays unit-testable
    on its own without going through the runner.
    """
    def decorator(func):
        return Rule(func, check_id=check_id, severity=severity, description=description)
    return decorator


class RuleSet:
    """
    An ordered, named collection of rules.

    Named because reports get read by people who did not write the rules, and
    "invoice_output_v2 failed" is a more useful thing to see in a log than "3 failures".
    """

    def __init__(self, name="ruleset", rules=None):
        self.name = name
        self.rules = list(rules or [])

    def add(self, *rules):
        """Add rules. Returns self so calls can chain."""
        for item in rules:
            if not isinstance(item, Rule):
                raise TypeError(f"expected Rule, got {type(item).__name__}")
            self.rules.append(item)
        return self

    def extend(self, other):
        """Merge another RuleSet in."""
        self.rules.extend(other.rules)
        return self

    def ids(self):
        return [r.check_id for r in self.rules]

    def __len__(self):
        return len(self.rules)

    def __iter__(self):
        return iter(self.rules)

    def __repr__(self):
        return f"<RuleSet {self.name!r} with {len(self.rules)} rules>"
