"""Conservative cross-source disagreement detection, without choosing a winner."""


def fuse_claims(claims):
    groups = {}
    for claim in claims:
        groups.setdefault(claim["subject"], []).append(dict(claim))
    result = []
    for subject, items in groups.items():
        conflicted = len({item["claim"] for item in items}) > 1
        result.append(
            {
                "subject": subject,
                "confidence_label": "conflicted" if conflicted else "inferred",
                "evidence": items,
            }
        )
    return result
