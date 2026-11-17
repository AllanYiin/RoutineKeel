"""Build one ZIP per component in dist, with a separate test workspace."""
from pathlib import Path
import argparse
import ast
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile

from agent_guidance import write_package_guide, write_workspace_guide

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist'
PRODUCT = ROOT / 'routinekeel'
BASE = PRODUCT / 'flow_base'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_archive(destination, paths):
    skip_dirs = {'.git', '.venv', '__pycache__', '.pytest_cache', 'dist', 'outputs',
                 'share_states', 'share_logs', 'pytest-of-agent', 'build'}
    skip_names = {'settings.json', 'router.json', 'split_legacy.py', 'create_launchers.py'}
    skip_exts = {'.pyc', '.zip', '.whl', '.log', '.db', '.sqlite3', '.mp4', '.wav',
                 '.safetensors', '.onnx', '.pt', '.pth', '.pem', '.key', '.jsonl'}
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for directory, prefix in paths:
            for file in sorted(directory.rglob('*')):
                rel = file.relative_to(directory)
                if (not file.is_file() or file.is_symlink() or skip_dirs.intersection(rel.parts)
                        or file.name in skip_names or file.name.startswith('.env')
                        or file.suffix.lower() in skip_exts):
                    continue
                archive.write(file, prefix + rel.as_posix())


def run(command, **kwargs):
    subprocess.run(command, check=True, **kwargs)


def project_info(path):
    data = tomllib.loads((path / 'pyproject.toml').read_text(encoding='utf-8'))
    name = data['project']['name']
    version = data['project']['version']
    archive = data.get('tool', {}).get('routinekeel', {}).get('archive-name', name)
    for value in (name, archive):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', value):
            raise ValueError(f'Invalid package or archive name in {path}')
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError(f'Expected major.minor.patch in {path}')
    if path == BASE:
        tree = ast.parse((path / 'src/flow_base/__init__.py').read_text(encoding='utf-8'))
        declared = [node.value.value for node in tree.body
                    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                    and any(isinstance(target, ast.Name) and target.id == '__version__'
                            for target in node.targets)]
        if declared != [version]:
            raise ValueError('flow_base.__version__ must match pyproject.toml before packaging')
    return name, version, archive


def discover_projects():
    flows = [p for p in sorted((PRODUCT / 'flows').iterdir())
             if (p / 'flow.template.json').is_file()]
    projects = [BASE, *flows]
    names, archives = set(), set()
    for path in projects:
        name, _, archive = project_info(path)
        if name.lower() in names or archive.lower() in archives:
            raise ValueError(f'Duplicate package or archive name: {path}')
        names.add(name.lower())
        archives.add(archive.lower())
    return projects


def write_bootstrap(dest, wheel, version):
    assets = json.loads((ROOT / 'tools/uv-assets.json').read_text(encoding='utf-8'))

    def asset(suffix):
        return next(a for a in assets if a['filename'].endswith(suffix))

    sh_cases, ps_cases = [], []
    for platform, suffix in {
        'Linux:x86_64': 'manylinux_2_17_x86_64.manylinux2014_x86_64.whl',
        'Linux:aarch64': 'manylinux_2_17_aarch64.manylinux2014_aarch64.musllinux_1_1_aarch64.whl',
        'Darwin:arm64': 'macosx_11_0_arm64.whl',
        'Darwin:x86_64': 'macosx_10_12_x86_64.whl',
    }.items():
        a = asset(suffix)
        sh_cases.append(f'    {platform}) uv_url="{a["url"]}"; uv_hash="{a["sha256"]}" ;;')
    for arch, suffix in {'AMD64': 'win_amd64.whl', 'ARM64': 'win_arm64.whl'}.items():
        a = asset(suffix)
        ps_cases.append(f"""        "{arch}" {{ $UvUrl = '{a["url"]}'; $UvHash = '{a["sha256"]}' }}""")
    for name in ('start.sh', 'start.ps1'):
        template = (ROOT / 'tools' / f'{name}.in').read_text(encoding='utf-8')
        body = template.replace('@WHEEL@', wheel.name).replace('@WHEEL_HASH@', sha(wheel))
        body = body.replace('@VERSION@', version)
        body = body.replace('@UV_SH_CASES@', '\n'.join(sh_cases)).replace('@UV_PS_CASES@', '\n'.join(ps_cases))
        (dest / name).write_text(body, encoding='utf-8-sig' if name.endswith('.ps1') else 'utf-8', newline='\n')
    (dest / 'start.sh').chmod(0o755)


def write_ui_launchers(dest):
    """Expose optional local tools through the existing bootstrap scripts."""
    wrappers = {
        'open_ui.bat': '@echo off\r\ncall "%~dp0run_app.bat" ui %*\r\n',
        'open_ui.sh': '#!/bin/sh\nHERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec sh "$HERE/run_app.sh" ui "$@"\n',
        'open_ui.command': '#!/bin/sh\nHERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec sh "$HERE/run_app.command" ui "$@"\n',
        'queue_worker.bat': '@echo off\r\ncall "%~dp0run_app.bat" queue-worker %*\r\n',
        'queue_worker.sh': '#!/bin/sh\nHERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec sh "$HERE/run_app.sh" queue-worker "$@"\n',
        'queue_worker.command': '#!/bin/sh\nHERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec sh "$HERE/run_app.command" queue-worker "$@"\n',
    }
    for name, body in wrappers.items():
        target = dest / name
        target.write_text(body, encoding='ascii', newline='')
        if name.endswith(('.sh', '.command')):
            target.chmod(0o755)


def build_project(path, staging):
    name, version, archive_name = project_info(path)
    wheels = staging / 'wheels'
    launcher = BASE / 'scripts/project_launcher.py'
    if path == BASE:
        # The validator expects a generated development launcher. Use an isolated copy.
        validation = staging / 'validate-base'
        shutil.copytree(path, validation, ignore=shutil.ignore_patterns(
            '.venv', 'dist', '__pycache__', '.pytest_cache', '.env', 'outputs', 'share_*'))
        run([sys.executable, str(validation / 'scripts/project_launcher.py'), '--root', str(validation)])
        run([sys.executable, str(validation / 'scripts/apsm_validate.py'),
             '--project', str(validation), '--strict'])
    run(['uv', 'build', '--wheel', '--out-dir', str(wheels)], cwd=path)
    wheel = wheels / f'{name.replace("-", "_")}-{version}-py3-none-any.whl'
    dest = staging / 'workspace' / path.name
    dest.mkdir(parents=True)
    shutil.copyfile(wheel, dest / wheel.name)
    shutil.copyfile(path / 'README.md', dest / 'README.md')
    shutil.copyfile(path / 'LICENSE' if (path / 'LICENSE').is_file() else ROOT / 'LICENSE', dest / 'LICENSE')
    write_package_guide(dest, path)
    if path == BASE:
        write_bootstrap(dest, wheel, version)
        run([sys.executable, str(launcher), '--root', str(dest), '--flow-base-package'])
        write_ui_launchers(dest)
        shutil.copyfile(path / 'docs/user-guide.md', dest / 'user-guide.md')
        shutil.copyfile(path / 'docs/mcp-local.md', dest / 'mcp-local.md')
        sources = [(BASE, 'routinekeel/flow_base/'), (ROOT / 'tools', 'tools/')]
    else:
        manifest = json.loads((path / 'flow.template.json').read_text(encoding='utf-8'))
        if manifest['version'] != version or (manifest.get('module') or manifest.get('id')) != name:
            raise ValueError(f'Manifest does not match the package name/version: {path}')
        manifest.update(package=wheel.name, sha256=sha(wheel))
        (dest / 'flow.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        run([sys.executable, str(launcher), '--root', str(dest), '--flow-task-package'])
        for doc in ('settings.example.json', 'WORKFLOW_AUDIT.md', 'PROVENANCE.md'):
            if (path / doc).is_file():
                shutil.copyfile(path / doc, dest / doc)
        sources = [(path, f'routinekeel/flows/{path.name}/')]
    source_archive(dest / 'source.zip', sources)

    output = staging / 'release' / f'{archive_name}-{version}.zip'
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(dest.iterdir()):
            if file.is_file():
                member = f'{path.name}/{file.name}'
                if file.suffix in {'.sh', '.command'}:
                    info = zipfile.ZipInfo(member)
                    info.create_system = 3
                    info.external_attr = 0o100755 << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, file.read_bytes())
                else:
                    archive.write(file, member)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError(f'Corrupted release file: {output}')
    return {'file': output.name, 'folder': path.name, 'version': version,
            'bytes': output.stat().st_size, 'sha256': sha(output)}


def main():
    projects = discover_projects()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', help='Optional component folder name or Python package name')
    args = parser.parse_args()
    if args.project:
        selected = 'flow_base' if args.project == 'base' else args.project
        projects = [p for p in projects if selected in (p.name, project_info(p)[0])]
        if len(projects) != 1:
            parser.error(f'Unknown or ambiguous component: {args.project}')

    DIST.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=DIST, prefix='build-') as temporary:
        staging = Path(temporary)
        (staging / 'release').mkdir()
        reports = [build_project(path, staging) for path in projects]
        write_workspace_guide(staging / 'workspace')
        (staging / 'release/SHA256SUMS').write_text(
            ''.join(f"{item['sha256']}  {item['file']}\n" for item in reports), encoding='utf-8')
        (staging / 'release/versions.json').write_text(
            json.dumps(reports, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        # Replacing build output also removes stale assets from earlier versions.
        for name in ('release', 'workspace', 'wheels'):
            target = DIST / name
            if target.exists():
                shutil.rmtree(target)
            (staging / name).rename(target)
    # GitHub Windows runners may use cp1252 stdout; keep the console report ASCII.
    # The published versions.json remains UTF-8 with the original folder names.
    print(json.dumps(reports, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
