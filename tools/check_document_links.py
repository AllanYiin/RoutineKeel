"""Check local Markdown and SVG targets in source or an extracted candidate."""

import argparse
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from functools import cache
from pathlib import Path
from urllib.parse import unquote, urlsplit


def visible_lines(text):
    fence = None
    for line in text.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence = marker[1]
            elif marker[1].startswith(fence):
                fence = None
            continue
        if fence is None:
            yield line


def targets(path):
    if path.suffix.lower() == ".svg":
        for element in ET.parse(path).iter():
            for key, value in element.attrib.items():
                if key in {"href", "{http://www.w3.org/1999/xlink}href"}:
                    yield value
        return
    for line in visible_lines(path.read_text(encoding="utf-8-sig")):
        line = re.sub(r"`[^`]*`", "", line)
        for match in re.finditer(r"\]\(\s*(?:<([^>]+)>|([^()\s]+(?:\([^()]*\)[^()\s]*)*))", line):
            yield match[1] or match[2]
        for match in re.finditer(
            r"""<(?:a|img)\b[^>]*?\b(?:href|src)=["']([^"']+)["']""", line, re.IGNORECASE
        ):
            yield match[1]
        reference = re.match(r"^\s*\[[^\]]+\]:\s*(?:<([^>]+)>|(\S+))", line)
        if reference:
            yield reference[1] or reference[2]


@cache
def anchors(path):
    if path.suffix.lower() == ".svg":
        return {e.get("id") for e in ET.parse(path).iter() if e.get("id")}
    result = set()
    for line in visible_lines(path.read_text(encoding="utf-8-sig")):
        result.update(re.findall(r"""\b(?:id|name)=["']([^"']+)["']""", line))
        heading = re.match(r"^#{1,6}\s+(.+)$", line)
        if not heading:
            continue
        title = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", heading[1]).strip().lower()
        slug = "".join(
            c for c in title if c in " _-" or unicodedata.category(c)[0] in "LNM"
        ).replace(" ", "-")
        key = slug
        suffix = 0
        while key in result:
            suffix += 1
            key = f"{slug}-{suffix}"
        result.add(key)
    return result


def check(paths):
    problems = []
    documents = set()
    count = 0
    for path in paths:
        if not path.exists():
            problems.append(f"Missing document: {path}")
            continue
        files = path.rglob("*") if path.is_dir() else (path,)
        documents.update(f for f in files if f.suffix.lower() in {".md", ".svg"} and f.is_file())
    for document in sorted(documents):
        for value in targets(document):
            url = urlsplit(value)
            if url.scheme or url.netloc:
                if url.scheme in {"https", "http"} and not url.netloc:
                    problems.append(f"{document}: {value}")
                continue
            target = (
                (document.parent / unquote(url.path)).resolve() if url.path else document.resolve()
            )
            count += 1
            if not target.exists() or (
                url.fragment
                and target.suffix.lower() in {".svg", ".md"}
                and unquote(url.fragment) not in anchors(target)
            ):
                problems.append(f"{document}: {value}")
    return len(documents), count, problems


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    docs, count, problems = check(parser.parse_args().paths)
    print(f"{docs} documents, {count} local link targets, {len(problems)} missing")
    for problem in problems:
        print(problem)
    sys.exit(bool(problems))
