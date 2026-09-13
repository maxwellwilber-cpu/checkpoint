"""checkpoint: validate AI output against rules you declare, before it reaches anyone."""

from .types import Severity, Finding, Report
from .rules import Rule, rule, RuleSet
from .runner import validate, validate_many
from . import checks

__version__ = "1.0.0"
__all__ = ["Severity", "Finding", "Report", "Rule", "rule", "RuleSet", "validate", "validate_many", "checks"]
