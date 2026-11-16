from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for name in ('base', 'ai_flow01', 'ai_flow02'):
    sys.path.insert(0, str(ROOT / name / 'src'))
