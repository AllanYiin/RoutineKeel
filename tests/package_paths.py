"""Use all artifacts from the same candidate build when running outside CI."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = Path(os.environ.get("ROUTINEKEEL_TEST_RELEASE_DIR", ROOT / "dist/release")).resolve()
WORKSPACE = RELEASE.parent / "workspace"
WHEELS = RELEASE.parent / "wheels"
