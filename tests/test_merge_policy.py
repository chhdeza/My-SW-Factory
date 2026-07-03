"""Risk-based merge policy tests."""

from factory.config import MergeConfig
from factory.gates.merge_policy import assess_risk, merge_decision

RULES = MergeConfig()


def test_low_risk_auto_merges():
    assessment = assess_risk(["src/app.py", "tests/test_app.py"], 120, RULES)
    assert not assessment.high
    assert merge_decision(assessment, RULES) == "auto_merge"


def test_workflow_edit_is_high_risk():
    assessment = assess_risk([".github/workflows/ci.yml"], 5, RULES)
    assert assessment.high
    assert "workflows" in assessment.reasons[0]
    assert merge_decision(assessment, RULES) == "needs_human"


def test_dependency_upgrade_is_high_risk():
    for manifest in ("pyproject.toml", "sub/package.json", "requirements.txt"):
        assert assess_risk([manifest], 3, RULES).high, manifest


def test_migration_is_high_risk():
    assert assess_risk(["app/migrations/0002_add_field.py"], 10, RULES).high
    assert assess_risk(["alembic/versions/abc.py"], 10, RULES).high


def test_large_diff_is_high_risk():
    assessment = assess_risk(["src/app.py"], RULES.high_risk.max_diff_lines + 1, RULES)
    assert assessment.high


def test_security_flag_is_high_risk():
    assert assess_risk(["src/app.py"], 10, RULES, security_flagged=True).high


def test_reviewer_high_risk_holds():
    assert assess_risk(["src/app.py"], 10, RULES, reviewer_risk="high").high


def test_auto_merge_disabled_forces_human():
    rules = MergeConfig(auto_merge_low_risk=False)
    assessment = assess_risk(["src/app.py"], 10, rules)
    assert not assessment.high
    assert merge_decision(assessment, rules) == "needs_human"


def test_rules_can_be_relaxed():
    rules = MergeConfig()
    rules.high_risk.workflow_edits = False
    assert not assess_risk([".github/workflows/ci.yml"], 5, rules).high
