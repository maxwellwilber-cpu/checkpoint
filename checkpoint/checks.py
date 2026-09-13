"""
Ready-made checks for the things LLM output actually gets wrong.

Each function here is a factory: call it with your parameters, get a Rule back.

    ruleset = RuleSet("invoice").add(
        required("invoice.total"),
        numeric_range("items[].price", minimum=0),
        grounded_numbers(source=raw_data),
    )

The list is short on purpose. These are the failures that show up over and over in
production LLM output — missing fields, invented values, placeholder text left in,
citations pointing at nothing, numbers that appear nowhere in the source. Generic
schema validation is a solved problem and libraries already do it well; what they do
not do is catch a confident model making something up.
"""

import re

from .paths import resolve, exists
from .rules import Rule
from .types import Finding, Severity

# Phrases that mean the model did not actually do the job.
_PLACEHOLDERS = [
    "lorem ipsum", "todo", "tbd", "fixme", "xxx",
    "[insert", "<insert", "{{", "your name here", "placeholder",
    "as an ai language model", "i cannot", "i'm unable to",
    "n/a - please fill", "example.com", "john doe",
]


def required(*paths, severity=Severity.BLOCKER):
    """Every listed path must exist and not be None."""
    def check(data):
        findings = []
        for path in paths:
            matches = list(resolve(data, path))
            if not matches:
                findings.append((path, "required field is missing"))
            else:
                for concrete, value in matches:
                    if value is None:
                        findings.append((concrete, "required field is null"))
        return findings
    return Rule(check, check_id="required", severity=severity,
                description=f"Required: {', '.join(paths)}")


def not_empty(*paths, severity=Severity.BLOCKER):
    """Strings must contain non-whitespace; lists and dicts must have items."""
    def check(data):
        findings = []
        for path in paths:
            for concrete, value in resolve(data, path):
                if value is None:
                    findings.append((concrete, "is null"))
                elif isinstance(value, str) and not value.strip():
                    findings.append((concrete, "is empty"))
                elif isinstance(value, (list, dict, tuple)) and len(value) == 0:
                    findings.append((concrete, "is empty"))
        return findings
    return Rule(check, check_id="not_empty", severity=severity,
                description=f"Not empty: {', '.join(paths)}")


def of_type(path, expected, severity=Severity.BLOCKER):
    """
    Value at path must be of the given Python type(s).

    Catches the classic LLM-JSON failure: a number returned as the string "1200", which
    then silently breaks every downstream calculation that assumed arithmetic worked.
    """
    types = expected if isinstance(expected, tuple) else (expected,)
    names = "/".join(t.__name__ for t in types)

    def check(data):
        findings = []
        for concrete, value in resolve(data, path):
            # bool is a subclass of int in Python; treat them as distinct here.
            if isinstance(value, bool) and bool not in types:
                findings.append((concrete, f"expected {names}, got bool"))
            elif not isinstance(value, types):
                findings.append((concrete, f"expected {names}, got {type(value).__name__}"))
        return findings
    return Rule(check, check_id=f"of_type[{path}]", severity=severity,
                description=f"{path} must be {names}")


def numeric_range(path, minimum=None, maximum=None, severity=Severity.BLOCKER):
    """Numbers at path must fall within bounds."""
    def check(data):
        findings = []
        for concrete, value in resolve(data, path):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue  # type is of_type()'s job, not this one's
            if minimum is not None and value < minimum:
                findings.append(Finding(f"numeric_range[{path}]", severity,
                                        f"{value} is below minimum {minimum}", concrete, value))
            if maximum is not None and value > maximum:
                findings.append(Finding(f"numeric_range[{path}]", severity,
                                        f"{value} is above maximum {maximum}", concrete, value))
        return findings
    bounds = f"{minimum if minimum is not None else '-inf'}..{maximum if maximum is not None else 'inf'}"
    return Rule(check, check_id=f"numeric_range[{path}]", severity=severity,
                description=f"{path} within {bounds}")


def one_of(path, allowed, severity=Severity.BLOCKER):
    """
    Value must come from a fixed set.

    The check that catches invented enum values — a model asked for "high/medium/low"
    returning "moderate" because it reads better.
    """
    allowed_set = set(allowed)

    def check(data):
        findings = []
        for concrete, value in resolve(data, path):
            if value not in allowed_set:
                preview = ", ".join(sorted(str(a) for a in allowed_set)[:8])
                findings.append(Finding(f"one_of[{path}]", severity,
                                        f"{value!r} is not one of: {preview}", concrete, value))
        return findings
    return Rule(check, check_id=f"one_of[{path}]", severity=severity,
                description=f"{path} in {sorted(allowed_set)}")


def matches(path, pattern, severity=Severity.BLOCKER, label=None):
    """String must match a regex — IDs, dates, currency codes."""
    compiled = re.compile(pattern)

    def check(data):
        findings = []
        for concrete, value in resolve(data, path):
            if not isinstance(value, str) or not compiled.search(value):
                findings.append(Finding(f"matches[{path}]", severity,
                                        f"{value!r} does not match {label or pattern}",
                                        concrete, value))
        return findings
    return Rule(check, check_id=f"matches[{path}]", severity=severity,
                description=f"{path} matches {label or pattern}")


def word_count(path, minimum=None, maximum=None, severity=Severity.WARNING):
    """
    Text length in words.

    Usually a WARNING: a summary that runs long is a quality problem, not a correctness
    one, and blocking an otherwise-good result over word count tends to train people to
    ignore the whole report.
    """
    def check(data):
        findings = []
        for concrete, value in resolve(data, path):
            if not isinstance(value, str):
                continue
            count = len(value.split())
            if minimum is not None and count < minimum:
                findings.append(Finding(f"word_count[{path}]", severity,
                                        f"{count} words, minimum {minimum}", concrete, count))
            if maximum is not None and count > maximum:
                findings.append(Finding(f"word_count[{path}]", severity,
                                        f"{count} words, maximum {maximum}", concrete, count))
        return findings
    return Rule(check, check_id=f"word_count[{path}]", severity=severity,
                description=f"{path} word count {minimum}..{maximum}")


def no_placeholders(*paths, extra=None, severity=Severity.BLOCKER):
    """
    Catch template text and refusals that were never replaced.

    "[INSERT CLIENT NAME]" reaching a customer is the kind of failure that costs trust
    instantly, and it is invisible to schema validation because the field is a
    perfectly valid non-empty string.
    """
    needles = _PLACEHOLDERS + list(extra or [])

    def check(data):
        findings = []
        targets = paths or ("",)
        for path in targets:
            for concrete, value in resolve(data, path):
                for text_path, text in _iter_strings(value, concrete):
                    lowered = text.lower()
                    for needle in needles:
                        if needle in lowered:
                            findings.append(Finding(
                                "no_placeholders", severity,
                                f"contains placeholder text {needle!r}", text_path, text[:120]))
                            break
        return findings
    return Rule(check, check_id="no_placeholders", severity=severity,
                description="No template or refusal text left in output")


def citations_resolve(claim_path, cite_field, source_ids, severity=Severity.BLOCKER):
    """
    Every citation must point at something that exists.

    This is the check that matters most for retrieval and analysis output. A model will
    cheerfully cite "source_7" when only six sources were supplied, and the result reads
    as more rigorous than uncited text precisely because it has a citation on it.

    Args:
        claim_path:  path to the claims, e.g. "findings[]"
        cite_field:  the key on each claim holding the reference, e.g. "source_id"
        source_ids:  the ids that actually exist
    """
    valid = set(source_ids)

    def check(data):
        findings = []
        for concrete, claim in resolve(data, claim_path):
            if not isinstance(claim, dict):
                continue
            if cite_field not in claim:
                findings.append(Finding("citations_resolve", severity,
                                        "claim has no citation", f"{concrete}.{cite_field}"))
                continue
            cited = claim[cite_field]
            values = cited if isinstance(cited, (list, tuple)) else [cited]
            for value in values:
                if value not in valid:
                    findings.append(Finding(
                        "citations_resolve", severity,
                        f"cites {value!r}, which does not exist",
                        f"{concrete}.{cite_field}", value))
        return findings
    return Rule(check, check_id="citations_resolve", severity=severity,
                description=f"Citations on {claim_path} resolve to known sources")


def grounded_numbers(source, paths=None, tolerance=0.0, severity=Severity.BLOCKER,
                     ignore=(0, 1)):
    """
    Every number in the output must appear somewhere in the source data.

    This is the anti-hallucination check, and it is the reason this library exists.

    A model summarizing a document will produce a figure that is *plausible* — the right
    order of magnitude, the right units, formatted correctly — and entirely invented.
    Schema validation passes it. Type checks pass it. A human skimming passes it. The
    only thing that catches it is asking whether the number is actually in the source.

    SCOPE THIS. Point it at the fields that make claims about the data — summaries,
    findings, computed figures. Do NOT point it at recommendations, forecasts or
    proposals: those are supposed to introduce numbers that are not in the source
    ("move 3 of the 14 roles"), and flagging them produces confident false positives.
    Grounding answers "is this claim supported?", which is only meaningful for
    statements of fact.

    Args:
        source:    the input the output was derived from (any nested structure)
        paths:     which output paths to check; None means every number found, which is
                   only safe when the whole document is factual claims
        tolerance: allowed relative difference, for rounding (0.01 = 1%)
        ignore:    values too common to be meaningful evidence, default 0 and 1
    """
    source_numbers = set(_collect_numbers(source))
    ignored = set(ignore)

    def check(data):
        findings = []
        targets = paths or ("",)
        for path in targets:
            for concrete, value in resolve(data, path):
                for number_path, number in _iter_numbers(value, concrete):
                    if number in ignored:
                        continue
                    if _is_grounded(number, source_numbers, tolerance):
                        continue
                    findings.append(Finding(
                        "grounded_numbers", severity,
                        f"{number} does not appear in the source data", number_path, number))
        return findings
    return Rule(check, check_id="grounded_numbers", severity=severity,
                description="Every number traces back to the source")


def _is_grounded(number, source_numbers, tolerance):
    if number in source_numbers:
        return True
    if tolerance <= 0:
        return False
    for candidate in source_numbers:
        scale = max(abs(candidate), abs(number), 1e-9)
        if abs(candidate - number) / scale <= tolerance:
            return True
    return False


def _iter_strings(node, trail=""):
    """Walk any structure, yielding (path, string)."""
    if isinstance(node, str):
        yield trail, node
    elif isinstance(node, dict):
        for key, value in node.items():
            sub = f"{trail}.{key}" if trail else str(key)
            yield from _iter_strings(value, sub)
    elif isinstance(node, (list, tuple)):
        for i, item in enumerate(node):
            yield from _iter_strings(item, f"{trail}[{i}]")


# Strings that carry digits without making a numeric claim.
_IDENTIFIER = re.compile(r"^[^\s]*[A-Za-z][^\s]*$")          # s9, INV-2024, user_42, v1.2
_DATE_LIKE = re.compile(r"^\d{4}-\d{2}-\d{2}|^\d{1,2}/\d{1,2}/\d{2,4}")  # 2026-01-15, 1/15/26


def _is_numeric_claim(text):
    """
    Decide whether a string is asserting a value or just happens to contain digits.

    This exists because the first version of grounded_numbers mined the "9" out of the
    id "s9" and reported it as a hallucinated figure. A validator that cries wolf gets
    switched off, so a false positive here is more damaging than a missed catch.

    The rule: prose (has whitespace) and bare numbers make claims; identifiers and
    dates do not.
    """
    stripped = text.strip()
    if not stripped:
        return False
    if _DATE_LIKE.match(stripped):
        return False
    if " " not in stripped and _IDENTIFIER.match(stripped):
        return False
    return True


def _iter_numbers(node, trail=""):
    """
    Walk any structure, yielding (path, number).

    Numbers written as strings count — "1,200.50" is still a claim about a value, and
    models emit them constantly inside prose. Identifiers and dates are skipped; see
    _is_numeric_claim.
    """
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        yield trail, float(node)
    elif isinstance(node, str):
        if not _is_numeric_claim(node):
            return
        for match in re.findall(r"-?\d[\d,]*\.?\d*", node):
            cleaned = match.replace(",", "").rstrip(".")
            if cleaned and cleaned not in ("-",):
                try:
                    yield trail, float(cleaned)
                except ValueError:
                    pass
    elif isinstance(node, dict):
        for key, value in node.items():
            sub = f"{trail}.{key}" if trail else str(key)
            yield from _iter_numbers(value, sub)
    elif isinstance(node, (list, tuple)):
        for i, item in enumerate(node):
            yield from _iter_numbers(item, f"{trail}[{i}]")


def _collect_numbers(node):
    for _, number in _iter_numbers(node):
        yield number
