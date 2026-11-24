def risk_level(ir):
    if any(
        n["kind"] == "llm" or n["side_effects"] == "external" or n["resources"]["network"]
        for n in ir["nodes"]
    ):
        return "high"
    if any(e["confidence_label"] in {"inferred", "conflicted"} for e in ir["evidence"]):
        return "medium"
    return "low"
