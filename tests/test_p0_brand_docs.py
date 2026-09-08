import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
PRODUCTION = [ROOT / "app/static/radar.html", ROOT / "app/static/index.html",
              ROOT / "app/static/locales/en-US.json", ROOT / "app/static/locales/zh-CN.json"]


def test_brand_is_signal():
    html = (ROOT / "app/static/radar.html").read_text(encoding="utf-8")
    assert "SIGNAL" in html and "PRIVATE DISCOVERY RADAR" in html
    assert "私人发现雷达" in (ROOT / "app/static/locales/zh-CN.json").read_text(encoding="utf-8")


def test_no_old_brand_on_production_ui():
    text = "\n".join(path.read_text(encoding="utf-8") for path in PRODUCTION)
    for old in ("AMAZING KIMI", "Cross-Source Opportunity Radar", "Private Opportunity Radar", "私人机会雷达"):
        assert old.lower() not in text.lower()


def test_readme_source_count_matches_registry():
    registry = json.loads((ROOT / "app/source_registry.json").read_text(encoding="utf-8"))
    assert f"**{len(registry)} sources**" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_agents_md_exists():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert all(key in text for key in ("max_full_repo_scans", "max_full_test_runs", "max_test_log_lines", "max_tool_iterations"))
