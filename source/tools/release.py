"""Build independently distributable base and task archives. Never includes a venv."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import zipfile
import tomllib
import argparse
import sys
from agent_guidance import write_package_guide, write_workspace_guide

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_archive(destination, paths):
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as z:
        for directory, prefix in paths:
            for file in sorted(directory.rglob('*')):
                rel = file.relative_to(directory)
                if not file.is_file() or any(p in {'.venv', '__pycache__', '.pytest_cache', 'dist', 'pytest-of-agent', 'outputs', 'share_states', 'share_logs'} for p in rel.parts) or file.suffix == '.pyc':
                    continue
                if file.name in {'split_legacy.py', 'create_launchers.py'}:
                    continue
                z.write(file, prefix + rel.as_posix())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', choices=['base', 'ai_flow01', 'ai_flow02'])
    args = parser.parse_args()
    DIST.mkdir(exist_ok=True)
    workspace = DIST / 'workspace'
    workspace.mkdir(exist_ok=True)
    write_workspace_guide(workspace)
    reports = []
    for project, folder in [('base', 'flow_base'), ('ai_flow01', 'ai_flow01'), ('ai_flow02', 'ai_flow02')]:
        if args.project and args.project != project:
            continue
        root = ROOT / project
        if not root.is_dir():
            continue
        version = tomllib.loads((root/'pyproject.toml').read_text())['project']['version']
        if project == 'base':
            subprocess.run([sys.executable, str(root/'scripts/project_launcher.py'), '--root', str(root)], check=True)
            subprocess.run([sys.executable, str(root/'scripts/apsm_validate.py'), '--project', str(root), '--strict'], check=True)
        subprocess.run(['uv', 'build', '--wheel'], cwd=root, check=True)
        wheel = root / 'dist' / f'{folder}-{version}-py3-none-any.whl'
        dest = workspace / folder
        dest.mkdir(exist_ok=True)
        for previous in dest.glob(f'{folder}-*.whl'):
            if previous.name != wheel.name:
                previous.unlink()
        shutil.copyfile(wheel, dest / wheel.name)
        shutil.copyfile(root / 'README.md', dest / 'README.md')
        write_package_guide(dest, root)
        if project == 'base':
            assets = json.loads((ROOT / 'tools/uv-assets.json').read_text())
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
                ps_cases.append(f'        "{arch}" {{ $UvUrl = \'{a["url"]}\'; $UvHash = \'{a["sha256"]}\' }}')
            for name in ('start.sh', 'start.ps1'):
                template = (ROOT / 'tools' / (name + '.in')).read_text()
                text = template.replace('@WHEEL@', wheel.name).replace('@WHEEL_HASH@', sha(wheel))
                text = text.replace('@VERSION@', version)
                text = text.replace('@UV_SH_CASES@', '\n'.join(sh_cases)).replace('@UV_PS_CASES@', '\n'.join(ps_cases))
                (dest / name).write_text(text, encoding='utf-8-sig' if name.endswith('.ps1') else 'utf-8', newline='\n')
            (dest / 'start.sh').chmod(0o755)
            subprocess.run([sys.executable, str(root/'scripts/project_launcher.py'), '--root', str(dest), '--flow-base-package'], check=True)
            # No task implementation is distributed inside the base, even in its source archive.
            source_archive(dest / 'source.zip', [(ROOT/'base', 'base/'), (ROOT/'tests', 'tests/'), (ROOT/'tools', 'tools/')])
        else:
            manifest = json.loads((root / 'flow.template.json').read_text())
            manifest.update(package=wheel.name, sha256=sha(wheel))
            (dest/'flow.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
            subprocess.run([sys.executable, str(ROOT/'base/scripts/project_launcher.py'), '--root', str(dest), '--flow-task-package'], check=True)
            for name in ('settings.example.json', 'WORKFLOW_AUDIT.md', 'PROVENANCE.md'):
                if (root/name).is_file():
                    shutil.copyfile(root/name, dest/name)
            source_archive(dest/'source.zip', [(root, project + '/')])
        display_folder = {'ai_flow01': '課程影片產生字幕', 'ai_flow02': '檔案複製範例'}.get(project, folder)
        archive_name = {'ai_flow01': 'flow_subtitles', 'ai_flow02': 'flow_copy'}.get(project, folder)
        archive = DIST / f'{archive_name}-{version}.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
            for file in sorted(dest.iterdir()):
                if file.is_file():
                    z.write(file, display_folder + '/' + file.name)
        reports.append({'file': archive.name, 'bytes': archive.stat().st_size, 'sha256': sha(archive)})
    (DIST/'release.json').write_text(json.dumps(reports, indent=2) + '\n')
    if not args.project:
        base_version = tomllib.loads((ROOT/'base/pyproject.toml').read_text())['project']['version']
        bundle = DIST / f'flow_workspace-{base_version}.zip'
        instructions = '''# flow_base 與流程一鍵啟動包

產物預設放所選流程 outputs/<工作>/；docs 是摘要與交付文件，artifacts 是實際成果，scripts 僅在交付腳本時建立。不要把成果放進底座或共用狀態區。

初次使用：把本 ZIP 解壓到任意工作資料夾，讓 flow_base、課程影片產生字幕、檔案複製範例保持同層。

直接進入所需流程資料夾：Windows 開啟 run_app.bat；macOS 開啟 run_app.command；Linux 執行 sh run_app.sh。流程會借用同層的共用底座，自動準備 uv、Python、環境及該流程需要的模型。首次需要網路。安裝完成後輸入 /input 檔案路徑，檢查計畫後輸入 /run。

既有使用者：更新 flow_base 資料夾；將「課程影片產生字幕」的內容覆蓋到原有的 ai_flow_ASR_video（或自己命名的資料夾）。不要同時留下兩個相同流程的副本。保留每個流程的 outputs、share_states、share_logs；不要為了更新刪除歷史。舊 wheel 可以保留，入口只讀 flow.json 指定的版本。

資料夾可自由命名，顯示名稱由 flow.json 的 title 決定，內部 id 不必隨之修改。底座只安裝一份；流程包內的三個 run_app 是共用生成的薄入口，不另維護安裝邏輯。

底座入口可選擇全部流程；流程入口直接選定自己。準備階段不處理影片、不呼叫 LLM，執行前會顯示計畫。字幕預設由底座偵測硬體，流程自動選擇 CPU／CUDA，使用 Whisper large-v3 與 VAD，本字幕流程強制零次 LLM API 呼叫；未經真人確認維持 REVIEW_REQUIRED。

.venv、模型與大型套件不在本包，首次執行才建立。資源與狀態集中在 share_states，日誌在 share_logs。失敗後可重新開啟同一入口重試。

分享新學員：提供一份 flow_base 資料夾與所需流程資料夾即可；不要把自己的 share_states（可能含逐字稿與設定）一起公開。流程資料夾也可單獨分享給已有底座的學員。

每包的 source.zip 包含完整原始碼。base/VERIFICATION.md 記錄驗證；Windows CMD 與 macOS Finder 雙擊尚未真機驗收。macOS 可能需依系統提示允許執行或恢復執行權限。
'''
        with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('使用說明.md', instructions)
            z.write(workspace/'AGENTS.md', 'AGENTS.md')
            for report in reports:
                with zipfile.ZipFile(DIST/report['file']) as source:
                    for info in source.infolist():
                        z.writestr(info, source.read(info.filename))
        print(json.dumps({'bundle': bundle.name, 'bytes': bundle.stat().st_size, 'sha256': sha(bundle)}))
    print(json.dumps(reports, indent=2))


if __name__ == '__main__':
    main()
