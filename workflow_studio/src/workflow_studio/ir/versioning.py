import hashlib

from ..project.models import canonical_json
from .models import WorkflowIR


def ir_fingerprint(ir: WorkflowIR) -> str:
    return hashlib.sha256(canonical_json(ir.model_dump()).encode("utf-8")).hexdigest()
