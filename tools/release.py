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
PRODUCT = ROOT
BASE = PRODUCT / 'flow_base'
STUDIO = PRODUCT / 'workflow_studio'


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
    flows = [p for p in sorted((PRODUCT / 'workflows').iterdir())
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


def write_bootstrap(dest, wheel, version, media_wheel=None):
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
        body = body.replace('@MEDIA_WHEEL@', media_wheel.name if media_wheel else '')
        body = body.replace('@MEDIA_SHA256@', sha(media_wheel) if media_wheel else '')
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
    if path == STUDIO:
        from studio_release import build_studio
        return build_studio(path, staging, root=ROOT, base=BASE,
                            run=run, write_bootstrap=write_bootstrap)
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
    relative = path.relative_to(ROOT)
    dest = staging / 'workspace' / relative
    dest.mkdir(parents=True)
    shutil.copyfile(wheel, dest / wheel.name)
    shutil.copyfile(path / 'README.md', dest / 'README.md')
    shutil.copyfile(path / 'LICENSE' if (path / 'LICENSE').is_file() else ROOT / 'LICENSE', dest / 'LICENSE')
    write_package_guide(dest, path)
    if path == BASE:
        # Optional local media operations are a separate, offline-installable distribution.
        media = BASE / 'components/blueprint_media'
        run(['uv', 'build', '--wheel', '--out-dir', str(wheels)], cwd=media)
        media_wheel = wheels / f'routinekeel_blueprint_media-{project_info(media)[1]}-py3-none-any.whl'
        shutil.copyfile(media_wheel, dest / media_wheel.name)
        write_bootstrap(dest, wheel, version, media_wheel)
        (dest / 'blueprint-tools.json').write_text(json.dumps({
            'media': {'wheel':media_wheel.name, 'sha256':sha(media_wheel),
                     'operations':['audio.timeline','subtitle.srt','video.compose']}
        }, indent=2), encoding='utf-8')
        run([sys.executable, str(launcher), '--root', str(dest), '--flow-base-package'])
        write_ui_launchers(dest)
        shutil.copyfile(path / 'docs/user-guide.md', dest / 'user-guide.md')
        shutil.copyfile(path / 'docs/mcp-local.md', dest / 'mcp-local.md')
        shutil.copyfile(path / 'docs/blueprint-install.md', dest / 'blueprint-install.md')
        sources = [(BASE, 'flow_base/'), (ROOT / 'tools', 'tools/'),
                   (ROOT / 'examples/blueprint-install', 'examples/blueprint-install/')]
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
        if (path / 'skills').is_dir():
            shutil.copytree(path / 'skills', dest / 'skills')
        sources = [(path, f'workflows/{path.name}/')]
    source_archive(dest / 'source.zip', sources)

    output = staging / 'release' / f'{archive_name}-{version}.zip'
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        if path == BASE:
            for directory in ('workflows/', 'blueprints/', 'flow_base/share_logs/', 'flow_base/share_states/'):
                archive.writestr(directory, b'')
        for file in sorted(dest.rglob('*')):
            if file.is_file():
                member = file.relative_to(staging / 'workspace').as_posix()
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
    return {'file': output.name, 'folder': relative.as_posix(), 'version': version,
            'bytes': output.stat().st_size, 'sha256': sha(output)}


def workspace_directories(workspace):
    """Create empty layout scaffolding, never copy an installed environment."""
    for name in ('workflows', 'blueprints', 'workflow_studio',
                 'flow_base/share_logs', 'flow_base/share_states'):
        (workspace / name).mkdir(parents=True, exist_ok=True)


def write_workspace_archive(staging, reports):
    workspace = staging / 'workspace'
    workspace_directories(workspace)
    (workspace / 'README.md').write_text(
        '# RoutineKeel\n\n'
        '主要方向：workflow_studio 設計 → blueprints 藍圖包 → flow_base 轉換為可執行 workflow。\n'
        'GitHub 保留課程字幕、檔案複製兩個參考案例，不以持續增加內建流程作為產品主線。\n\n'
        'Studio 入口：在宿主對話讀 workflow_studio/AGENTS.md；studio.bat／studio.sh guide 提供引導資料，不開原生視窗。\n'
        '底座入口：flow_base/run_app.bat；參考流程位於 workflows/FLOW_<名稱>/。\n'
        'Studio 的已實作能力與驗收範圍見 workflow_studio/README.md。\n'
        '首次啟動建立 .venv；共用紀錄與狀態位於 flow_base/share_logs、flow_base/share_states。\n'
        '此包不含執行環境、模型、憑證、私人設定或使用者成果。\n', encoding='utf-8')
    version = project_info(BASE)[1]
    output = staging / 'release' / f'RoutineKeel-workspace-{version}.zip'
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('RoutineKeel/', b'')
        for file in sorted(workspace.rglob('*')):
            member = 'RoutineKeel/' + file.relative_to(workspace).as_posix()
            if file.is_dir():
                archive.writestr(member + '/', b'')
            else:
                info = zipfile.ZipInfo(member)
                info.create_system = 3
                info.external_attr = (0o100755 if file.suffix in {'.sh', '.command'} else 0o100644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, file.read_bytes())
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError('Corrupted workspace ZIP')
    return {'file': output.name, 'folder': 'RoutineKeel', 'version': version,
            'bytes': output.stat().st_size, 'sha256': sha(output),
            'components': [item['folder'] for item in reports]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', help='Optional component folder name or Python package name')
    args = parser.parse_args()
    # The default distribution centers on Studio/runtime plus two existing examples.
    projects = ([STUDIO] if args.project in {'studio', 'workflow_studio', 'workflow-studio'}
                else discover_projects())
    if args.project:
        selected = ('flow_base' if args.project == 'base' else
                    'workflow_studio' if args.project == 'studio' else args.project)
        projects = [p for p in projects if selected in (p.name, p.name.removeprefix("FLOW_"), project_info(p)[0])]
        if len(projects) != 1:
            parser.error(f'Unknown or ambiguous component: {args.project}')

    if not args.project:
        projects.append(STUDIO)

    if DIST.is_symlink() or (hasattr(DIST, 'is_junction') and DIST.is_junction()):
        raise ValueError('Build output cannot be a linked directory')
    DIST.mkdir(exist_ok=True)
    if not DIST.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError('Build output must remain inside the repository')
    with tempfile.TemporaryDirectory(dir=DIST, prefix='build-') as temporary:
        staging = Path(temporary)
        (staging / 'release').mkdir()
        reports = [build_project(path, staging) for path in projects]
        if projects == [STUDIO]:
            (staging / 'workspace/AGENTS.md').write_text(
                '# Studio 測試工作區\n\n'
                '唯一套件在 workflow_studio/；先讀該目錄的 AGENTS.md 與 README.md。\n'
                '從其 Studio 啟動器操作，不把設計期套件當成 flow.json 領域流程。\n'
                '這是候選安裝驗證區；正式發布與外部資料傳送仍須原有授權。\n',
                encoding='utf-8')
        else:
            write_workspace_guide(staging / 'workspace')
        if not args.project:
            reports.append(write_workspace_archive(staging, reports))
        (staging / 'release/SHA256SUMS').write_text(
            ''.join(f"{item['sha256']}  {item['file']}\n" for item in reports), encoding='utf-8')
        (staging / 'release/versions.json').write_text(
            json.dumps(reports, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        # Replacing build output also removes stale assets from earlier versions.
        for name in ('release', 'workspace', 'wheels'):
            target = DIST / name
            if (target.is_symlink() or (hasattr(target, 'is_junction') and target.is_junction())
                    or target.resolve().parent != DIST.resolve()):
                raise ValueError(f'Refusing linked build output: {target}')
            if target.exists():
                shutil.rmtree(target)
            (staging / name).rename(target)
    # GitHub Windows runners may use cp1252 stdout; keep the console report ASCII.
    # The published versions.json remains UTF-8 with the original folder names.
    print(json.dumps(reports, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
