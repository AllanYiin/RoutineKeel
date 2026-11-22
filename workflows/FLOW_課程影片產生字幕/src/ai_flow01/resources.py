from pathlib import Path
from flow_base.contracts import Resource
from flow_base.runtime import read_json
from flow_base.resources import resource_path

SEPARATOR = "demucs-musdb-plus"
SEPARATOR_PROFILE = "demucs-cpu"

def catalogue():
    return {v["id"]: Resource.model_validate(v) for v in read_json(Path(__file__).with_name("resources.json"))}
