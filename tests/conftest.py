from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for project in [ROOT / 'flow_base', *(ROOT / 'workflows').iterdir()]:
    if (project / 'src').is_dir():
        sys.path.insert(0, str(project / 'src'))
