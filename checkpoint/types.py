"""
Core types.

One idea runs through this whole library: a validation result is DATA, not a print
statement and not an exception. A check returns findings; something else decides what
to do with them. That separation is what lets the same rules run in a test suite, in a
CI job, and inline in production without being rewritten each time.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Severity(str, Enum):
    """
    How bad a finding is.

    BLOCKER stops the output from being used. WARNING lets it through but records the
    problem. INFO is an observation worth keeping.

    Three levels, not five. Every extra level is another judgment call at the moment
    someone writes a rule, and in practice teams only ever act on two: "stop" and
    "log it." INFO exists for things you want to measure over time.
    """

    BLOCKER = "BLOCKER"
    WARNING = "WARNING"
    INFO = "INFO"


def _coerce_severity(value):
    """
    Accept a Severity or the string form of one.

    Severity subclasses str, so `severity="BLOCKER"` looks correct, type-checks nowhere,
    and used to produce a finding that compared unequal to Severity.BLOCKER. The result
    was a recorded BLOCKER on a report that said PASS, which is the exact failure this
    library exists to prevent. Coerce instead of trusting.
    """
    if isinstance(value, Severity):
        return value
    try:
        return Severity(str(value).upper())
    except ValueError:
        raise ValueError(
            f"severity must be one of {[s.value for s in Severity]}, got {value!r}"
        )


@dataclass(frozen=True)
class Finding:
    """
    One thing that is wrong, or worth noting, about one piece of output.

    Frozen because a finding is a historical record: it describes what was true at the
    moment a check ran. Nothing downstream should be able to quietly edit it.

    Fields:
        check_id: stable identifier for the rule that produced this, e.g. "no_empty_fields".
                  Stable is the operative word — you will grep logs for it in six months.
        severity: BLOCKER, WARNING or INFO.
        message:  human-readable, specific. "price is -5, must be >= 0" beats "invalid price".
        path:     where in the output the problem is, e.g. "items[2].price". Empty for
                  whole-document findings.
        value:    the offending value, when showing it helps.
    """

    check_id: str
    severity: Severity
    message: str
    path: str = ""
    value: Any = None

    def __post_init__(self):
        object.__setattr__(self, "severity", _coerce_severity(self.severity))

    def __str__(self):
        location = f" at {self.path}" if self.path else ""
        return f"[{self.severity.value}] {self.check_id}{location}: {self.message}"


@dataclass
class Report:
    """
    The result of validating one piece of output.

    `passed` means no BLOCKERs. Warnings do not fail a run — if a warning should stop
    the pipeline, it was never a warning, it was a blocker, and the rule should say so.
    Keeping that decision in the rule rather than in the runner means the same report
    can be read the same way everywhere.
    """

    findings: list = field(default_factory=list)
    checks_run: int = 0
    subject: Optional[str] = None

    @property
    def blockers(self):
        return [f for f in self.findings if f.severity is Severity.BLOCKER]

    @property
    def warnings(self):
        return [f for f in self.findings if f.severity is Severity.WARNING]

    @property
    def infos(self):
        return [f for f in self.findings if f.severity is Severity.INFO]

    @property
    def passed(self):
        """True when nothing blocking was found."""
        return not self.blockers

    def __bool__(self):
        # Lets you write: if not validate(data, rules): ...
        return self.passed

    def raise_for_blockers(self):
        """
        Turn blockers into an exception, for callers that want fail-fast behaviour.

        Opt-in on purpose. A library that raises by default forces every caller into
        try/except even when they only wanted to look at the findings.
        """
        if self.blockers:
            lines = "\n".join(f"  {f}" for f in self.blockers)
            raise ValidationError(f"{len(self.blockers)} blocker(s):\n{lines}", self)

    def summary(self):
        """One line, for logs."""
        state = "PASS" if self.passed else "FAIL"
        subject = f" [{self.subject}]" if self.subject else ""
        return (f"{state}{subject}: {self.checks_run} checks, "
                f"{len(self.blockers)} blockers, {len(self.warnings)} warnings, "
                f"{len(self.infos)} info")

    def to_dict(self):
        """Serializable form, for writing reports to disk or shipping to a log system."""
        return {
            "subject": self.subject,
            "passed": self.passed,
            "checks_run": self.checks_run,
            "counts": {
                "blocker": len(self.blockers),
                "warning": len(self.warnings),
                "info": len(self.infos),
            },
            "findings": [
                {
                    "check_id": f.check_id,
                    "severity": f.severity.value,
                    "message": f.message,
                    "path": f.path,
                    "value": f.value,
                }
                for f in self.findings
            ],
        }


class ValidationError(Exception):
    """Raised by Report.raise_for_blockers(). Carries the full report on `.report`."""

    def __init__(self, message, report):
        super().__init__(message)
        self.report = report
