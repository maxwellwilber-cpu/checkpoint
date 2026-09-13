"""Tests for the built-in checks."""

import checkpoint as cp
from checkpoint import checks as C
from checkpoint.types import Severity


def run(data, *rules):
    return cp.validate(data, list(rules))


class TestRequired:
    def test_passes_when_present(self):
        assert run({"a": 1}, C.required("a")).passed

    def test_fails_when_missing(self):
        report = run({}, C.required("a"))
        assert not report.passed
        assert "missing" in report.blockers[0].message

    def test_fails_when_null(self):
        report = run({"a": None}, C.required("a"))
        assert not report.passed
        assert "null" in report.blockers[0].message


class TestNotEmpty:
    def test_whitespace_only_string_is_empty(self):
        assert not run({"a": "   "}, C.not_empty("a")).passed

    def test_empty_list_is_empty(self):
        assert not run({"a": []}, C.not_empty("a")).passed

    def test_zero_is_not_empty(self):
        # 0 is a legitimate value, not an absence. Treating it as empty is a classic bug.
        assert run({"a": 0}, C.not_empty("a")).passed


class TestOfType:
    def test_number_as_string_is_caught(self):
        # The most common LLM-JSON defect: "1200" instead of 1200.
        report = run({"total": "1200"}, C.of_type("total", (int, float)))
        assert not report.passed
        assert "got str" in report.blockers[0].message

    def test_bool_is_not_accepted_as_int(self):
        # bool subclasses int in Python; silently accepting True as a number is wrong.
        assert not run({"n": True}, C.of_type("n", int)).passed

    def test_correct_type_passes(self):
        assert run({"total": 1200}, C.of_type("total", (int, float))).passed


class TestNumericRange:
    def test_below_minimum_fails(self):
        assert not run({"price": -5}, C.numeric_range("price", minimum=0)).passed

    def test_above_maximum_fails(self):
        assert not run({"pct": 140}, C.numeric_range("pct", maximum=100)).passed

    def test_inside_range_passes(self):
        assert run({"pct": 40}, C.numeric_range("pct", 0, 100)).passed

    def test_applies_across_a_wildcard(self):
        data = {"items": [{"price": 10}, {"price": -1}]}
        report = run(data, C.numeric_range("items[].price", minimum=0))
        assert not report.passed
        assert report.blockers[0].path == "items[1].price"


class TestOneOf:
    def test_invented_enum_value_is_caught(self):
        # Asked for high/medium/low, model returns "moderate".
        assert not run({"c": "moderate"}, C.one_of("c", ["high", "medium", "low"])).passed

    def test_allowed_value_passes(self):
        assert run({"c": "high"}, C.one_of("c", ["high", "medium", "low"])).passed


class TestMatches:
    def test_bad_format_fails(self):
        assert not run({"id": "nope"}, C.matches("id", r"^INV-\d{4}$")).passed

    def test_good_format_passes(self):
        assert run({"id": "INV-2026"}, C.matches("id", r"^INV-\d{4}$")).passed


class TestWordCount:
    def test_too_short_warns_but_does_not_block(self):
        report = run({"s": "short"}, C.word_count("s", minimum=10))
        assert report.passed          # warnings never fail a run
        assert len(report.warnings) == 1

    def test_too_long_warns(self):
        report = run({"s": " ".join(["w"] * 50)}, C.word_count("s", maximum=10))
        assert len(report.warnings) == 1


class TestNoPlaceholders:
    def test_unreplaced_template_is_caught(self):
        assert not run({"s": "Dear [INSERT NAME], thanks"}, C.no_placeholders()).passed

    def test_model_refusal_is_caught(self):
        assert not run({"s": "As an AI language model, I cannot"}, C.no_placeholders()).passed

    def test_searches_nested_structures(self):
        data = {"sections": [{"body": "TODO: write this"}]}
        assert not run(data, C.no_placeholders()).passed

    def test_clean_text_passes(self):
        assert run({"s": "Revenue rose in Q3."}, C.no_placeholders()).passed


class TestCitationsResolve:
    SOURCES = ["s1", "s2", "s3"]

    def test_valid_citation_passes(self):
        data = {"findings": [{"text": "x", "source_id": "s2"}]}
        assert run(data, C.citations_resolve("findings[]", "source_id", self.SOURCES)).passed

    def test_invented_citation_is_caught(self):
        # The dangerous case: a fabricated citation makes output look MORE rigorous.
        data = {"findings": [{"text": "x", "source_id": "s9"}]}
        report = run(data, C.citations_resolve("findings[]", "source_id", self.SOURCES))
        assert not report.passed
        assert "does not exist" in report.blockers[0].message

    def test_missing_citation_is_caught(self):
        data = {"findings": [{"text": "uncited claim"}]}
        assert not run(data, C.citations_resolve("findings[]", "source_id", self.SOURCES)).passed

    def test_handles_lists_of_citations(self):
        data = {"findings": [{"text": "x", "source_id": ["s1", "s9"]}]}
        report = run(data, C.citations_resolve("findings[]", "source_id", self.SOURCES))
        assert len(report.blockers) == 1


class TestGroundedNumbers:
    SOURCE = {"revenue": [100000, 125000], "headcount": 12}

    def test_number_present_in_source_passes(self):
        data = {"summary": "Revenue reached 125000 this year."}
        assert run(data, C.grounded_numbers(self.SOURCE)).passed

    def test_invented_number_is_caught(self):
        # Plausible, correctly formatted, right order of magnitude, entirely made up.
        data = {"summary": "Revenue reached 187500 this year."}
        report = run(data, C.grounded_numbers(self.SOURCE))
        assert not report.passed
        assert "187500" in report.blockers[0].message

    def test_identifiers_are_not_treated_as_numeric_claims(self):
        # Regression: "s9" once produced a phantom finding for the number 9.
        data = {"findings": [{"source_id": "s9", "ref": "INV-2024"}]}
        assert run(data, C.grounded_numbers(self.SOURCE)).passed

    def test_dates_are_not_treated_as_numeric_claims(self):
        data = {"as_of": "2026-01-15"}
        assert run(data, C.grounded_numbers(self.SOURCE)).passed

    def test_zero_and_one_are_ignored_by_default(self):
        # Too common to be evidence of anything.
        data = {"summary": "There was 1 issue and 0 failures."}
        assert run(data, C.grounded_numbers(self.SOURCE)).passed

    def test_tolerance_allows_rounding(self):
        data = {"summary": "Revenue was about 125500."}
        assert not run(data, C.grounded_numbers(self.SOURCE)).passed
        assert run(data, C.grounded_numbers(self.SOURCE, tolerance=0.01)).passed

    def test_identifier_in_prose_is_not_mined_for_numbers(self):
        # Regression: "INV-2024" in a sentence yielded -2024, because the regex read the
        # hyphen as a minus sign. A real negative is preceded by a space, not a letter.
        data = {"summary": "Invoice INV-2024 cleared on time."}
        assert run(data, C.grounded_numbers(self.SOURCE)).passed

    def test_a_real_negative_number_is_still_read(self):
        # The fix above must not stop genuine negatives being checked.
        data = {"summary": "Margin was -4000 that month."}
        assert not run(data, C.grounded_numbers(self.SOURCE)).passed

    def test_documented_limit_arithmetic_is_not_understood(self):
        # This asserts a LIMITATION, not a feature. The correct annual total of the
        # source's monthly revenue is flagged, because the check tests membership in the
        # source rather than derivability from it. Documented in the docstring; this test
        # exists so the limit cannot change silently.
        total = sum(self.SOURCE["revenue"])
        data = {"summary": f"Revenue totalled {total} for the year."}
        assert not run(data, C.grounded_numbers(self.SOURCE)).passed

    def test_documented_limit_no_field_association(self):
        # Also a limitation. 104000 is a real value in the source, so attaching it to the
        # wrong subject passes. Catching this needs a rule that knows the schema.
        data = {"summary": "Headcount is 125000."}
        assert run(data, C.grounded_numbers(self.SOURCE)).passed

    def test_comma_formatted_numbers_are_read(self):
        data = {"summary": "Revenue reached 187,500 this year."}
        assert not run(data, C.grounded_numbers(self.SOURCE)).passed
