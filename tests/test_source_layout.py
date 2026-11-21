"""Keep checkout, build workspace and archive component paths in agreement."""
from pathlib import Path
import json
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_checkout_component_roots_match_distribution_locations():
    assert not (ROOT / 'routinekeel').exists()
    for name in ('flow_base', 'workflow_studio', 'workflows', 'blueprints'):
        assert (ROOT / name).is_dir()
    for name in ('share_states', 'share_logs'):
        assert (ROOT / 'flow_base' / name / '.gitkeep').is_file()
    for project in (ROOT / 'workflows').iterdir():
        if not (project / 'flow.template.json').is_file():
            continue
        assert project.name.startswith('FLOW_')
        manifest = json.loads((project / 'flow.template.json').read_text(encoding='utf-8'))
        package = tomllib.loads((project / 'pyproject.toml').read_text(encoding='utf-8'))
        assert manifest.get('module', manifest['id']) == package['project']['name']


def test_ci_commands_use_existing_checkout_paths():
    for workflow in (ROOT / '.github/workflows').glob('*.yml'):
        text = workflow.read_text(encoding='utf-8')
        assert 'routinekeel/flow_base' not in text
        assert 'routinekeel/workflow_studio' not in text
        assert 'routinekeel/flows' not in text
