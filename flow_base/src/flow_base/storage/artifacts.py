"""Content-verified multi-file step artifacts; old single-file caches stay valid."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import shutil
from contextlib import closing

from ..runtime import FlowError, atomic_json, read_json, sha256

MANIFEST = 'artifact-set.json'
PREVIEWABLE = frozenset({'.txt', '.md', '.json', '.csv', '.srt', '.vtt', '.png', '.jpg', '.jpeg', '.webp'})


def _member(root: Path, relative: str) -> Path:
    candidate = PurePosixPath(relative)
    if (not relative or relative in {'.', '..'} or candidate.as_posix() != relative or
            candidate.is_absolute() or '..' in candidate.parts or '\\' in relative or ':' in relative):
        raise FlowError('ARTIFACT_PATH', 'ArtifactSet 檔案路徑必須位於工作目錄內', path=relative)
    path = root.joinpath(*candidate.parts)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent == root or root in parent.parents):
        raise FlowError('ARTIFACT_LINK', 'ArtifactSet 不允許符號連結', path=relative)
    if not path.resolve().is_relative_to(root.resolve()):
        raise FlowError('ARTIFACT_PATH', 'ArtifactSet 檔案越出工作目錄', path=relative)
    return path


def build_artifact_set(primary: Path, declared=()):
    """Write a manifest only after every declared sidecar exists and is hashed."""
    primary = Path(primary).absolute()
    root = primary.parent
    required = [primary.name, *declared]
    files = []
    seen = set()
    for relative in required:
        if relative in seen:
            continue
        seen.add(relative)
        path = _member(root, relative)
        if not path.is_file():
            raise FlowError('ARTIFACT_MISSING', '宣告的產物或 sidecar 不存在', path=relative)
        files.append({'path': relative, 'size_bytes': path.stat().st_size,
                      'sha256': sha256(path), 'type': path.suffix.lower().lstrip('.') or 'binary',
                      'previewable': path.suffix.lower() in PREVIEWABLE})
    manifest = {'schema_version': 1, 'primary': primary.name, 'files': files}
    atomic_json(root / MANIFEST, manifest)
    return manifest


def validate_artifact_set(primary: Path, declared=()):
    """Return None for a cache miss, including any missing or changed sidecar."""
    primary = Path(primary)
    root = primary.parent
    manifest_path = root / MANIFEST
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return None
    try:
        manifest = read_json(manifest_path)
        if manifest.get('schema_version') != 1 or manifest.get('primary') != primary.name:
            return None
        rows = manifest['files']
        if not isinstance(rows, list):
            return None
        known = {row['path'] for row in rows}
        if len(known) != len(rows) or not {primary.name, *declared} <= known:
            return None
        for row in rows:
            path = _member(root, row['path'])
            if not path.is_file() or path.stat().st_size != row['size_bytes'] or sha256(path) != row['sha256']:
                return None
        return manifest
    except (OSError, KeyError, TypeError, ValueError, FlowError):
        return None


def gc_preview(runtime):
    """List only cache directories with no SQLite reference; never delete here."""
    runtime.initialize()
    root = runtime.home / 'artifacts'
    if not root.is_dir():
        return {'candidates': [], 'bytes': 0}
    if root.is_symlink() or (hasattr(root, 'is_junction') and root.is_junction()):
        raise FlowError('GC_PATH', '快取根目錄不可為連結')
    with closing(runtime.connect()) as db:
        primary = [runtime.home / row[0] for row in db.execute("SELECT artifact FROM steps WHERE status='done' AND artifact IS NOT NULL")]
        rows = [row[0] for row in db.execute('SELECT manifest_json FROM artifact_sets WHERE step_attempt_id IS NOT NULL')]
    referenced = {path.resolve() for path in primary}
    from ..core.lifecycle import resolve_manifest_reference
    for payload in rows:
        try:
            referenced.add(resolve_manifest_reference(runtime, payload))
        except (KeyError, ValueError, TypeError, FlowError):
            continue

    candidates = []
    def visit(folder):
        if folder.is_symlink() or (hasattr(folder, 'is_junction') and folder.is_junction()):
            return
        if not any(path.is_relative_to(folder.resolve()) for path in referenced):
            size = sum(path.stat().st_size for path in folder.rglob('*')
                       if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root.resolve()))
            candidates.append({'path': str(folder), 'bytes': size})
            return
        for child in sorted(folder.iterdir()):
            if child.is_dir():
                visit(child)
    for folder in sorted(root.iterdir()):
        if folder.is_dir():
            visit(folder)
    return {'candidates': candidates, 'bytes': sum(item['bytes'] for item in candidates)}


def gc_apply(runtime, *, confirm=False):
    """Explicit maintenance action; recalculate references under a cross-process lock."""
    if not confirm:
        raise FlowError('GC_CONFIRM_REQUIRED', '清理快取前須明確確認')
    root = (runtime.home / 'artifacts').resolve()
    with runtime.lock('artifact_gc', timeout=30):
        preview = gc_preview(runtime)
        removed = []
        for item in preview['candidates']:
            folder = Path(item['path']).resolve()
            if folder == root or not folder.is_relative_to(root) or folder.is_symlink():
                raise FlowError('GC_PATH', '拒絕清理快取目錄以外的路徑')
            if any(path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())
                   for path in folder.rglob('*')):
                raise FlowError('GC_PATH', '候選快取含連結，拒絕清理')
            shutil.rmtree(folder)
            removed.append(item)
        return {'removed': removed, 'bytes': sum(item['bytes'] for item in removed)}
