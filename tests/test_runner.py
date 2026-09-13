"""Tests for the runner, rules and report behaviour."""

import pytest

import checkpoint as cp
from checkpoint import checks as C, rule, Rule, RuleSet
from checkpoint.types import Severity, ValidationError, Finding


class TestReturnShapes:
    """A rule may return None, a string, a tuple, a Finding, or a list of those."""

    def test_none_means_clean(self):
        @rule()
        def always_fine(data):
            return None
        assert cp.validate({}, always_fine).passed

    def test_string_becomes_a_finding(self):
        @rule()
        def complains(data):
            return "something is wrong"
        report = cp.validate({}, complains)
        assert report.blockers[0].message == "something is wrong"

    def test_tuple_carries_a_path(self):
        @rule()
        def located(data):
            return ("a.b", "bad value")
        assert cp.validate({}, located).blockers[0].path == "a.b"

    def test_list_produces_several_findings(self):
        @rule()
        def many(data):
            return ["one", "two", "three"]
        assert len(cp.validate({}, many).findings) == 3


class TestRuleFailureIsAFinding:
    def test_a_crashing_rule_reports_instead_of_passing(self):
        # The worst possible behaviour would be to swallow this and report a clean run.
        @rule()
        def explodes(data):
            raise RuntimeError("boom")
        report = cp.validate({}, explodes)
        assert not report.passed
        assert "RuntimeError" in report.blockers[0].message

    def test_one_crashing_rule_does_not_stop_the_others(self):
        @rule()
        def explodes(data):
            raise RuntimeError("boom")
        @rule()
        def also_fails(data):
            return "second problem"
        report = cp.validate({}, [explodes, also_fails])
        assert report.checks_run == 2
        assert len(report.blockers) == 2


class TestSeverity:
    def test_warnings_do_not_fail_a_run(self):
        @rule(severity=Severity.WARNING)
        def nags(data):
            return "could be better"
        report = cp.validate({}, nags)
        assert report.passed
        assert len(report.warnings) == 1

    def test_info_is_recorded_but_harmless(self):
        @rule(severity=Severity.INFO)
        def notes(data):
            return "fyi"
        report = cp.validate({}, notes)
        assert report.passed
        assert len(report.infos) == 1


class TestReport:
    def test_report_is_falsy_when_blocked(self):
        report = cp.validate({}, C.required("missing"))
        assert not report

    def test_report_is_truthy_when_clean(self):
        assert cp.validate({"a": 1}, C.required("a"))

    def test_raise_for_blockers_is_opt_in(self):
        report = cp.validate({}, C.required("missing"))
        with pytest.raises(ValidationError):
            report.raise_for_blockers()

    def test_raise_for_blockers_is_silent_when_clean(self):
        cp.validate({"a": 1}, C.required("a")).raise_for_blockers()

    def test_exception_carries_the_report(self):
        report = cp.validate({}, C.required("missing"))
        try:
            report.raise_for_blockers()
        except ValidationError as exc:
            assert exc.report is report

    def test_to_dict_is_serializable(self):
        import json
        report = cp.validate({}, C.required("missing"), subject="run-1")
        payload = report.to_dict()
        json.dumps(payload)  # must not raise
        assert payload["subject"] == "run-1"
        assert payload["passed"] is False
        assert payload["counts"]["blocker"] == 1

    def test_every_rule_runs_even_after_a_failure(self):
        report = cp.validate({}, [C.required("a"), C.required("b"), C.required("c")])
        assert report.checks_run == 3
        assert len(report.blockers) == 3


class TestRuleSet:
    def test_add_returns_self_for_chaining(self):
        rs = RuleSet("x").add(C.required("a")).add(C.not_empty("a"))
        assert len(rs) == 2

    def test_rejects_non_rules(self):
        with pytest.raises(TypeError):
            RuleSet("x").add(lambda d: None)

    def test_extend_merges_rulesets(self):
        base = RuleSet("base").add(C.required("a"))
        extra = RuleSet("extra").add(C.not_empty("a"))
        assert len(base.extend(extra)) == 2

    def test_ids_are_listed(self):
        rs = RuleSet("x").add(C.required("a"), C.one_of("b", [1, 2]))
        assert rs.ids() == ["required", "one_of[b]"]


class TestValidateMany:
    def test_batch_reports_are_keyed(self):
        items = [{"a": 1}, {}, {"a": 3}]
        reports = cp.validate_many(items, C.required("a"))
        assert set(reports) == {"0", "1", "2"}
        assert reports["0"].passed and not reports["1"].passed

    def test_custom_key(self):
        items = [{"id": "x", "a": 1}, {"id": "y"}]
        reports = cp.validate_many(items, C.required("a"), key=lambda i: i["id"])
        assert not reports["y"].passed


class TestSeverityCoercion:
    def test_a_string_severity_still_blocks(self):
        # Severity subclasses str, so severity="BLOCKER" looked right and produced a
        # recorded blocker on a report that said PASS.
        from checkpoint import rule
        @rule(severity="BLOCKER")
        def bad(data):
            return "definitely broken"
        report = cp.validate({}, [bad])
        assert not report.passed
        assert report.to_dict()["counts"]["blocker"] == 1

    def test_an_invalid_severity_is_rejected_loudly(self):
        from checkpoint.types import Finding
        with pytest.raises(ValueError):
            Finding("x", "NOT_A_SEVERITY", "msg")


class TestTupleReturn:
    def test_two_messages_produce_two_findings(self):
        from checkpoint import rule
        @rule()
        def two(data):
            return ("summary is empty", "title is empty")
        assert len(cp.validate({}, [two]).findings) == 2

    def test_path_and_message_still_produce_one(self):
        from checkpoint import rule
        @rule()
        def located(data):
            return ("items[0].price", "is negative")
        findings = cp.validate({}, [located]).findings
        assert len(findings) == 1 and findings[0].path == "items[0].price"
