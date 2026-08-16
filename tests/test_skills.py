from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {"cumcm-a-solve", "cumcm-a-audit", "cumcm-a-reflect", "cumcm-a-evaluate"}


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    return yaml.safe_load(text.split("---", 2)[1])


def test_skill_metadata_is_valid():
    for name in EXPECTED:
        data = frontmatter(ROOT / ".agents" / "skills" / name / "SKILL.md")
        assert data["name"] == name
        assert isinstance(data["description"], str) and len(data["description"]) > 40


def test_four_skill_names_are_unique():
    names = [frontmatter(path)["name"] for path in (ROOT / ".agents" / "skills").glob("*/SKILL.md")]
    assert set(names) == EXPECTED
    assert len(names) == len(set(names)) == 4


def test_implicit_invocation_is_disabled():
    for name in EXPECTED:
        data = yaml.safe_load((ROOT / ".agents" / "skills" / name / "agents" / "openai.yaml").read_text(encoding="utf-8"))
        assert data["policy"]["allow_implicit_invocation"] is False


def test_skill_resource_contract_exists():
    for name in EXPECTED:
        base = ROOT / ".agents" / "skills" / name
        assert (base / "references" / "output-contract.md").exists()
        assert (base / "scripts" / "check_phase.py").exists()

