from pathlib import Path
import sys

FLOW = Path(__file__).resolve().parents[1]
PRODUCT = FLOW.parents[1]
sys.path.insert(0, str(PRODUCT / 'flow_base/src'))
sys.path.insert(0, str(FLOW / 'src'))
