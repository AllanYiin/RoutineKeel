"""Ship host-agent instructions separately from runtime and domain implementations."""
from pathlib import Path
from shutil import copyfile

WORKSPACE_GUIDE = '''# flow_base 工作區的 Agent 入口

此處包含一個共用底座與多個獨立流程。使用者要求執行任務時，先讀 flow_base/AGENTS.md，再讀目標流程資料夾的 AGENTS.md、flow.json 與 README.md，使用已封裝 CLI 執行。

預設每個流程各自建立 Codex 專案，這個父資料夾只作為共用執行工作區。只有使用者明確要跨流程管理時，才以此處作為對話專案。選擇流程不等於重新選擇技術棧；不要另寫替代流程或掃描其他磁碟找現成結果。沒有指定流程時，先呼叫底座 catalog，按宣告能力選擇；缺必要資訊才詢問。

一般操作不修改 wheel、安裝器或封裝程式；開發需求才閱讀 source.zip。依據本次 report 與 run 紀錄回報結果，不以檔名推定工作已完成。
'''


def write_workspace_guide(workspace):
    (Path(workspace) / 'AGENTS.md').write_text(WORKSPACE_GUIDE, encoding='utf-8')


def write_package_guide(destination, project):
    destination, project = Path(destination), Path(project)
    if project.name == 'flow_base':
        content = Path(__file__).with_name('agent_guidance.md').read_text(encoding='utf-8')
    elif (project / 'AGENTS.md').is_file():
        content = (project / 'AGENTS.md').read_text(encoding='utf-8')
    else:
        content = '''# 流程包 Agent 入口

先讀 ../flow_base/AGENTS.md 的通用執行契約，以及本目錄 flow.json、README.md 與 settings.json（若存在）。用同層底座 catalog → plan → run 執行本流程，不重新發明實作。工作區為共同父目錄；流程能力、參數及驗收由本包宣告，不把規則寫入底座。
'''
    (destination / 'AGENTS.md').write_text(content, encoding='utf-8')

    skills = project / 'skills'
    if skills.resolve() == project.resolve() / 'skills':
        for bundle in sorted(skills.glob('*.skill')):
            if bundle.is_file() and not bundle.is_symlink():
                output = destination / 'skills' / bundle.name
                output.parent.mkdir(exist_ok=True)
                copyfile(bundle, output)
