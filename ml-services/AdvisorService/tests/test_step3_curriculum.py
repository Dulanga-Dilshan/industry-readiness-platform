import os

import pytest

from advisor.curriculum.tools import CurriculumTools
from advisor.curriculum.validator import load_curriculum, load_glossary
from advisor.tools.envelope import ToolResult

FX = os.path.join(os.path.dirname(__file__), "fixtures")
GLOSS = os.path.join(os.path.dirname(__file__), "..", "data", "skills_glossary.txt")


def tools(name="curriculum_ok.yaml"):
    return CurriculumTools(load_curriculum(os.path.join(FX, name), GLOSS))


def test_valid_file_loads():
    st = load_curriculum(os.path.join(FX, "curriculum_ok.yaml"), GLOSS)
    assert st.status == "ok" and len(st.curriculum.semesters) == 4


def test_bad_file_gives_readable_errors_with_lines_and_suggestions():
    st = load_curriculum(os.path.join(FX, "curriculum_bad.yaml"), GLOSS)
    assert st.status == "unavailable"
    text = "\n".join(str(i) for i in st.issues)
    assert "unknown skill 'Pyhton'. Did you mean 'Python'?" in text
    assert "unknown cluster 'Data and AI'. Did you mean 'Data & AI'?" in text
    assert "duplicate module code 'A-1'" in text
    assert "level 'expert'" in text and "semester id 'three'" in text and "has no modules" in text
    assert any(i.line for i in st.issues)                      # line numbers present for YAML


def test_missing_file_is_unavailable_not_fatal():
    st = load_curriculum("/nope/curriculum.yaml", GLOSS)
    assert st.status == "unavailable"
    r = CurriculumTools(st).get_curriculum_progress("3.1")
    assert r.status == "unavailable" and "not found" in r.reason


def test_template_warns_about_example_data():
    st = load_curriculum(os.path.join(os.path.dirname(__file__), "..", "curriculum", "curriculum.yaml"), GLOSS)
    assert st.status == "ok"
    assert any("template" in i.message for i in st.issues)


def test_json_is_accepted(tmp_path):
    import json, yaml
    data = yaml.safe_load(open(os.path.join(FX, "curriculum_ok.yaml")))
    p = tmp_path / "c.json"
    p.write_text(json.dumps(data))
    assert load_curriculum(str(p), GLOSS).status == "ok"


def test_completed_current_upcoming_rule():
    r = tools().get_curriculum_progress("3.1")
    d = r.data
    assert [m["code"] for m in d["completed"]] == ["T-101", "T-201"]
    assert [m["code"] for m in d["current"]] == ["T-301"]
    assert [m["code"] for m in d["upcoming"]] == ["T-401"] and d["upcoming"][0]["semester"] == "4.1"
    assert set(d["skills_covered"]) == {"Python", "SQL", "JavaScript", "HTML"}


def test_semester_missing_or_unknown_is_error_with_ask():
    t = tools()
    assert t.get_curriculum_progress(None).status == "error"
    r = t.get_curriculum_progress("9.9")
    assert r.status == "error" and "ask the student" in r.reason


async def top(items, status="ok", as_of="2026-11-13T00:00:00Z"):
    async def fn(cluster, window, limit):
        if status == "ok":
            return ToolResult.ok({"items": items}, as_of)
        return ToolResult(status=status, reason="down")
    return fn


async def test_gaps_with_demand_data():
    items = [{"skill": "Python", "count": 120}, {"skill": "Docker", "count": 90}, {"skill": "Kubernetes", "count": 70},
             {"skill": "SQL", "count": 60}]
    r = await tools().get_skill_gaps("3.1", "Software Engineering", await top(items))
    d = r.data
    assert d["demand_status"] == "ok" and d["as_of"].startswith("2026-11-13")
    assert [c["skill"] for c in d["covered"]] == ["Python", "SQL"]
    gaps = {g["skill"]: g for g in d["gaps"]}
    assert gaps["Docker"]["coming_in"] == "4.1"            # later semester
    assert gaps["Kubernetes"]["coming_in"] is None
    assert d["not_in_curriculum"] == ["Kubernetes"]


async def test_gaps_without_demand_data_still_returns_curriculum_part():
    for status in ("unavailable", "error", "empty"):
        r = await tools().get_skill_gaps("3.1", "Software Engineering", await top([], status))
        assert r.status == "ok"
        assert r.data["demand_status"] in ("unavailable", "empty")
        assert "Python" in r.data["skills_covered"] and r.data["gaps"] == []


async def test_gaps_skill_matching_is_case_insensitive():
    r = await tools().get_skill_gaps("3.1", "x", await top([{"skill": "python", "count": 5}]))
    assert r.data["covered"][0]["modules"] == ["T-101"]
