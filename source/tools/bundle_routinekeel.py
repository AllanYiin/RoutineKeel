"""Build a branded learner bundle from the repository's published snapshots.

Run from a clean release checkout. Uses stdlib only; never walks runtime data.
"""
from pathlib import Path
import hashlib
import json
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = {'base': 'flow_base', 'ai_flow01': '課程影片產生字幕', 'ai_flow02': '檔案複製範例'}
COMMON = ('README.md', 'AGENTS.md', 'source.zip', 'run_app.bat', 'run_app.command', 'run_app.sh')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    versions = {name: tomllib.loads((ROOT / 'source' / name / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
                for name in PACKAGES}
    files = [ROOT / 'README.md', ROOT / 'AGENTS.md', ROOT / 'LICENSE',
             ROOT / 'assets' / 'routinekeel-logo.png', ROOT / 'assets' / 'README.md',
             ROOT / 'docs' / 'structure.md', ROOT / 'docs' / 'maintenance.md']
    for name, folder in PACKAGES.items():
        package = ROOT / folder
        files.extend(package / item for item in COMMON)
        files.extend(package / item for item in ('recover_failed_run.bat', 'recover_failed_run.command', 'recover_failed_run.sh')
                     if (package / item).is_file())
        namespace = 'flow_base' if name == 'base' else name
        wheel = package / f'{namespace}-{versions[name]}-py3-none-any.whl'
        files.append(wheel)
        if name == 'base':
            for entry in ('start.ps1', 'start.sh'):
                path = package / entry
                if sha(wheel) not in path.read_text(encoding='utf-8-sig'):
                    raise ValueError(f'Launcher wheel hash mismatch: {entry}')
                files.append(path)
        else:
            manifest = package / 'flow.json'
            data = json.loads(manifest.read_text(encoding='utf-8'))
            if data['sha256'] != sha(wheel) or data['package'] != wheel.name:
                raise ValueError(f'Wheel does not match {manifest}')
            files.append(manifest)
            files.extend(package / name for name in ('settings.example.json', 'WORKFLOW_AUDIT.md', 'PROVENANCE.md')
                         if (package / name).is_file())
    if any(not p.is_file() or p.is_symlink() for p in files):
        raise ValueError('Missing file or symlink in release snapshot')
    downloads = ROOT / 'downloads'
    downloads.mkdir(exist_ok=True)
    archive = downloads / f'RoutineKeel-{versions["base"]}.zip'
    # Stable entry order, timestamp and executable bits make the bundle reproducible.
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for path in sorted(files):
            relative = path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo('RoutineKeel/' + relative, date_time=(2026, 11, 16, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o100755 if path.suffix in {'.sh', '.command'} else 0o100644) << 16
            body = path.read_bytes()
            if relative == 'README.md':
                text = body.decode('utf-8')
                text = text.replace('**建議直接下載 [RoutineKeel 一鍵啟動包](downloads/RoutineKeel-0.2.10.zip)。** 開啟檔案頁後按下載按鈕，完整解壓縮，就會得到名為 `RoutineKeel` 的資料夾。不要直接在 ZIP 壓縮檔內啟動。', '**你已取得 RoutineKeel 一鍵啟動包。** 請完整解壓縮，再開啟流程入口；不要直接在 ZIP 內執行。GitHub 另提供可瀏覽的開發原始碼；本包各套件的 source.zip 也保留完整分包原始碼。')
                body = text.encode('utf-8')
            z.writestr(info, body)
        note = zipfile.ZipInfo('RoutineKeel/使用說明.txt', date_time=(2026, 11, 16, 0, 0, 0))
        note.compress_type = zipfile.ZIP_DEFLATED
        note.create_system = 3
        note.external_attr = 0o100644 << 16
        z.writestr(note,
                   '進入要使用的流程資料夾。Windows 開啟 run_app.bat；macOS 開啟 run_app.command；Linux 執行 sh run_app.sh。\n'
                   '首次啟動需要網路。README 的 downloads/、source/ 連結是 GitHub 原始碼版的路徑；本包各套件的 source.zip 保留完整分包原始碼。\n')
    digest = sha(archive)
    (downloads / 'SHA256SUMS').write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
    result = {'project': 'RoutineKeel', 'components': versions, 'archive': archive.name,
              'bytes': archive.stat().st_size, 'sha256': digest,
              'runtime_code_changed': False, 'models_and_environments_included': False}
    (downloads / 'versions.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
