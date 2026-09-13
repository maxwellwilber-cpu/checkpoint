# checkpoint

Validate AI output against rules you declare, before it reaches anyone.

Schema validation tells you an LLM returned well-formed JSON. It does not tell you the
model invented a number, cited a source that does not exist, or left `[INSERT CLIENT
NAME]` in the summary. Those outputs are structurally perfect and factually wrong, and
they are the ones that cost trust.

`checkpoint` checks the things that actually go wrong.

```bash
git clone https://github.com/maxwellwilber-cpu/checkpoint
cd checkpoint
python examples/validate_llm_output.py
python -m pytest tests/ -q
```

Pure standard library. `pytest` only to run the tests.

---

## The idea in thirty seconds

```python
import checkpoint as cp
from checkpoint import checks as C, RuleSet

source = {"revenue": [82000, 91000, 104000], "headcount": 14}

rules = RuleSet("analysis").add(
    C.required("summary", "findings"),
    C.one_of("confidence", ["high", "medium", "low"]),
    C.no_placeholders(),
    C.citations_resolve("findings[]", "source_id", ["src-pl", "src-payroll"]),
    C.grounded_numbers(source, paths=["summary", "findings[].text"]),
)

report = cp.validate(llm_output, rules, subject="run-1842")

if not report:
    for finding in report.blockers:
        print(finding)   # [BLOCKER] grounded_numbers at summary: 27400.0 does not appear in the source data
```

A report is data, not an exception and not a print statement. The same rules run in a
test suite, a CI job, or inline in production without being rewritten.

---

## The check that matters most

```python
C.grounded_numbers(source, paths=["summary", "findings[].text"])
```

**Every number in the output must appear in the source data.**

A model summarizing a document will produce a figure that is the right order of
magnitude, in the right units, formatted correctly, and entirely invented. Schema
validation passes it. Type checks pass it. A human skimming passes it. The only thing
that catches it is asking where the number came from.

The bundled example makes this concrete. Two analyses differ by one sentence:

```
PASS [clean]:  10 checks, 0 blockers
FAIL [subtle]: 10 checks, 1 blockers
  [BLOCKER] grounded_numbers at summary: 27400.0 does not appear in the source data
```

`27400` reads as a conclusion the analysis would reasonably reach. It is not in the
source and not derivable by any stated method. Nothing else in the document is wrong.

**Scope this check deliberately** — it is the one real subtlety in the library. Grounding
applies to claims *about* the data: summaries, findings, computed figures. It must not
apply to recommendations or forecasts, because a proposal is supposed to introduce new
numbers. "Move 3 of the 14 roles to variable scheduling" contains a 3 that exists nowhere
in the source, and that is correct. Grounding answers *is this claim supported?*, which
is only a meaningful question about statements of fact.

I found that out by writing the example and watching a perfectly good output fail.

---

## Built-in checks

| Check | Catches |
|---|---|
| `required(*paths)` | Missing or null fields |
| `not_empty(*paths)` | Blank strings, empty lists (but `0` is a real value, not an absence) |
| `of_type(path, types)` | `"1200"` returned where `1200` was needed |
| `numeric_range(path, min, max)` | Negative prices, percentages over 100 |
| `one_of(path, allowed)` | Invented enum values — asked for high/medium/low, got "moderate" |
| `matches(path, regex)` | Malformed ids, dates, codes |
| `word_count(path, min, max)` | Summaries that run long or short (warning, not blocker) |
| `no_placeholders()` | `[INSERT NAME]`, `TODO`, `lorem ipsum`, "As an AI language model…" |
| `citations_resolve(...)` | Citations pointing at sources that do not exist |
| `grounded_numbers(source)` | Numbers that appear nowhere in the input |

Anything domain-specific is a short function:

```python
from checkpoint import rule
from checkpoint.types import Severity

@rule(severity=Severity.BLOCKER)
def recommendations_are_actionable(data):
    """Every recommendation must state an action, not an observation."""
    return [
        (f"recommendations[{i}].action", f"hedged: {r['action'][:60]!r}")
        for i, r in enumerate(data.get("recommendations", []))
        if r.get("action", "").lower().startswith(("consider", "perhaps", "it may be worth"))
    ]
```

A rule may return `None`, a string, a `(path, message)` tuple, a `Finding`, or a list of
any of those. The most common rule anyone writes is one line long, and making it build
`Finding` objects is the kind of friction that stops people writing rules at all.

---

## Four design decisions

**Findings are data.** A check returns findings; something else decides what to do with
them. That is what lets one ruleset serve tests, CI, and production without a rewrite.
`report.raise_for_blockers()` is available and opt-in — a library that raises by default
forces every caller into `try/except` even when they only wanted to look.

**Three severities, not five.** `BLOCKER` stops the output, `WARNING` records it,
`INFO` observes. Teams only ever act on two of those. If a warning should stop the
pipeline, it was never a warning.

**A rule that crashes is a finding, not a skip.** Swallowing the exception would report
a clean pass on an unvalidated document, which is the most dangerous thing a validation
library can do. Every other rule still runs.

**Every rule runs, always.** Stopping at the first blocker hides the rest and turns
fixing a bad output into one-at-a-time whack-a-mole.

---

## Paths

```
"total"              a top-level key
"invoice.total"      nested
"items[0].price"     one element
"items[-1].price"    the last element
"items[].price"      every element
```

The wildcard is what makes rules reusable: a rule written against `items[].price` works
on three items or three hundred, and every finding carries the concrete path
(`items[7].price`) so the report says exactly which one is wrong.

---

## Reports

```python
report.passed        # no blockers
report.blockers      # list of Finding
report.warnings
report.infos
report.summary()     # "FAIL [run-1842]: 10 checks, 1 blockers, 0 warnings, 0 info"
report.to_dict()     # JSON-serializable, for CI and log pipelines
bool(report)         # if not validate(data, rules): ...
```

## In CI

```python
import sys, checkpoint as cp
report = cp.validate(output, rules, subject=run_id)
print(report.summary())
sys.exit(0 if report.passed else 1)
```

Batch validation returns a report per item:

```python
reports = cp.validate_many(outputs, rules, key=lambda o: o["id"])
failed = {k: r for k, r in reports.items() if not r.passed}
```

---

## Tests

```bash
python -m pytest tests/ -v
```

67 tests. The ones worth reading are the regressions, because each is a bug this library
actually had:

- `test_identifiers_are_not_treated_as_numeric_claims` — `grounded_numbers` once mined
  the `9` out of the id `"s9"` and reported a hallucinated figure. A validator that cries
  wolf gets switched off, so a false positive here is worse than a missed catch.
- `test_negative_index` — `items[-1]` parsed as a dict key named `-1` and resolved to
  nothing, which looks identical to a missing field.
- `test_a_crashing_rule_reports_instead_of_passing` — the failure mode that would make
  every other guarantee here worthless.

---

## Structure

```
checkpoint/
  types.py     Finding, Report, Severity
  paths.py     nested access with wildcards
  rules.py     Rule, @rule decorator, RuleSet
  checks.py    the built-in check library
  runner.py    validate(), validate_many()
examples/
  validate_llm_output.py    three outputs, one ruleset, worked end to end
tests/                      67 pytest tests
```

## Related

- [evs](https://github.com/maxwellwilber-cpu/evs) — the predecessor: 73 validation checks
  and 43 tests against a specific AI financial-analysis pipeline. `checkpoint` is that
  idea generalized to any AI output.
- [client-data-cleaner](https://github.com/maxwellwilber-cpu/client-data-cleaner) — the
  same principle applied to record linkage: measure the accuracy instead of asserting it.

MIT licensed. Built by [Maxwell Wilber](https://linkedin.com/in/maxwellwilber).
