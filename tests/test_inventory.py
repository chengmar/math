from pathlib import Path

from cumcm_lab.inventory import inventory_skills, render_profile_config


def test_user_skill_disable_manifest_generation(tmp_path):
    trainer = tmp_path / "lab" / "trainer"
    trainer.mkdir(parents=True)
    custom = tmp_path / ".codex" / "skills" / "my-skill"
    custom.mkdir(parents=True)
    (custom / "SKILL.md").write_text("---\nname: my-skill\ndescription: test\n---\n", encoding="utf-8")
    report = inventory_skills(trainer, [tmp_path / ".codex" / "skills"])
    assert report["skills"][0]["classification"] == "user_custom"
    assert report["skills"][0]["action"] == "disable_in_profile"
    config = render_profile_config(report)
    assert "[[skills.config]]" in config
    assert "enabled = false" in config


def test_project_phase_skills_remain_enabled(tmp_path):
    trainer = tmp_path / "trainer"
    skill = trainer / ".agents" / "skills" / "cumcm-a-solve"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: cumcm-a-solve\ndescription: test\n---\n", encoding="utf-8")
    report = inventory_skills(trainer, [trainer / ".agents" / "skills"])
    assert report["skills"][0]["action"] == "keep_enabled"
    assert "[[skills.config]]" not in render_profile_config(report)
    assert "[[skills.config]]" in render_profile_config(report, baseline=True)

