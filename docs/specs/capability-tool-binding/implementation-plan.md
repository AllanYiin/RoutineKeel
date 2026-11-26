# 規格整理 v 1.2.0

## Codex / Claude Code 分階段開發計畫

以[技術規格](technical.md)的 B01–B09 為放行索引。Stage 1 和 Stage 2 可同期準備相容測試。Stage 2 的工作契約確定後，Stage 3、4 可並行推進，最後用同一 Runtime 與工作儲存驗收。

每階段先讀根層 AGENTS.md、DEVNOTE.md、受影響元件的 AGENTS.md 和本規格。Claude Code 另讀當前專案既有的 CLAUDE.md（若有）。長期規則同步至既有對應指引，測試與回復保留於專案文件。以下可直接貼到相應工具。

| 階段 | 依賴 | 交付 |
| --- | --- | --- |
| 0 基線與回復點 | — | 原流程、Windows 問題、串行基準及可還原備份 |
| 1 Runtime 與新安裝 | 0 | 目標 Python 一致、錯誤區分與短路徑續做 |
| 2 綁定狀態與併行 | 0，與 1 合流 | 持久狀態、工作依賴、有限度併行及實際領取 |
| 3 Python、Skill 與 CLI | 1、2 | 三類本機工具從需求到實際輸出 |
| 4 MCP 與宿主 LLM | 2 | 已連線工具真實派送、任意有契約的文字步驟 |
| 5 ToolCalling | 3、4 | 有界工具循環和模型結果回填 |
| 6 三路補件 | 1–5 | 等效方案、來源準備與 Adapter 補寫的共同閉環 |
| 7 整合、效能與故障驗收 | 1–6 | 三平台、隔離候選、併行量測與回滾 |
| 8 文件與本機交付 | 7 | 技術版、操作文件、驗證與本機候選 |

### Stage 0 基線與回復點

**Codex Instructions**

```text
在 RoutineKeel 工作區執行此階段。讀 docs/specs/capability-tool-binding/technical.md 和階段前置規則。
先讀根層規則與技術規格。在隔離環境重跑初裝、綁定、重綁與模型用量。記錄三入口所用 Python，重現 Windows 深暫存與短路徑差異。讀現有 JUnit 和 Git 差異，核對 SQLite 備份可還原。
提供可重跑命令、現有失敗、未測平台與回復點。
```

**Claude Code Instructions**

```text
在 RoutineKeel 工作區執行此階段。讀 docs/specs/capability-tool-binding/technical.md 和現有專案規則。
依 AGENTS.md 和既有 CLAUDE.md 盤點 Runtime 選擇、初裝與去重回歸，保留深／短暫存證據及串行樣本。還原一份 SQLite 備份並核對原版套件。
提供可重跑命令、現有失敗、未測平台與回復點。
```

### Stage 1 Runtime 與新安裝

**Codex Instructions**

```text
在 RoutineKeel 工作區執行此階段。讀 docs/specs/capability-tool-binding/technical.md 和階段前置規則。
讀 tools/start.ps1.in、tools/start.sh.in、tools/studio_release.py、flow_base/studio.py、workflow_studio/scripts/studio_bootstrap.py 與 graph/preparation.py。接通整合版唯一 Runtime。保留 uv 原始失敗階段，對 Windows 深層路徑故障安全換短 staging。
通過 B01、B02 與原啟動回歸。環境更新障礙可用前版啟動器和 venv 回復。
```

**Claude Code Instructions**

```text
在 RoutineKeel 工作區執行此階段。讀 docs/specs/capability-tool-binding/technical.md 和現有專案規則。
查底座與 Studio bootstrap 的兩種啟動方向。驗證工作區 Runtime，區分錯配、暫存故障及真缺件；注入 uv 失敗並在臨時解壓包驗證恢復。
通過 B01、B02 與原啟動回歸。環境更新障礙可用前版啟動器和 venv 回復。
```

### Stage 2 綁定狀態與併行

**Codex Instructions**

```text
在 RoutineKeel 工作區執行此階段。讀 docs/specs/capability-tool-binding/technical.md 和階段前置規則。
由 core/service.py、blueprints/completion.py 和 completion_store.py 收斂綁定入口。以短 SQLite 交易保存工作依賴、資源鍵、租約世代、去重與回執，慢操作在鎖外執行。同步三入口狀態。
通過 B03、B04、B07。停止新領取後可退回單槽相容模式。
```

**Claude Code Instructions**

```text
在 RoutineKeel 工作區執行此階段。讀 docs/specs/capability-tool-binding/technical.md 和現有專案規則。
依 technical.md 第 4、7、8 節將探測、準備、資格接成可領取子工作。保留 work_id、revision 與現有入口，以屏障和雙 Worker 測可靠恢復。
通過 B03、B04、B07。停止新領取後可退回單槽相容模式。
```

### Stage 3 Python、Skill 與 CLI

**Codex Instructions**

```text
在 RoutineKeel 工作區執行此階段。讀 docs/specs/capability-tool-binding/technical.md 和階段前置規則。
重用 graph/qualification.py、graph/preparation.py、blueprints/installer.py、execution/process.py 和字幕既有 faster-whisper 程式。從正常入口驗證字幕推論接線、Skill 指定腳本、ffprobe 具名輸出。
通過 B05 三組案例。停用單項 Adapter 或切回原已驗證鎖。
```

**Claude Code Instructions**

```text
在 RoutineKeel 工作區執行此階段。讀 docs/specs/capability-tool-binding/technical.md 和現有專案規則。
實作技術規格中三種本機工具。測 faster-whisper 語音與時間映射、Skill 腳本與規則兩條路徑、CLI 參數陣列與解析；覆蓋中文及空格路徑、逾時和版本漂移。
通過 B05 三組案例。停用單項 Adapter 或切回原已驗證鎖。
```

### Stage 4 MCP 與宿主 LLM

**Codex Instructions**

```text
在 RoutineKeel 工作區執行此階段。讀 docs/specs/capability-tool-binding/technical.md 和階段前置規則。
從 adapters/host_inventory.py、graph/capabilities.py、graph/session.py、graph/external.py 接上真實 MCP 呼叫及宿主 LLM Streaming、映射、結果回填與用量。用已配置模型及 MCP 測試 server 實跑，測斷線及未知回執。
通過 B05 對應案例、B07。停用新宿主路徑時保留已領取的回執待查。
```

**Claude Code Instructions**

```text
在 RoutineKeel 工作區執行此階段。讀 docs/specs/capability-tool-binding/technical.md 和現有專案規則。
沿用清冊、單次領取、授權與可信觀測，完成真實 MCP 結果和模型 Streaming。測結構映射、模型證據、回執缺失與原操作重查。
通過 B05 對應案例、B07。停用新宿主路徑時保留已領取的回執待查。
```

### Stage 5 ToolCalling

**Codex Instructions**

```text
在 RoutineKeel 工作區執行此階段。讀 docs/specs/capability-tool-binding/technical.md 和階段前置規則。
在 workflow_studio 的 llm/provider.py 保留純文字入口並新增工具模式。由底座統一核准、扣抵、派送和回填。Streaming 組合完整工具呼叫，獨立工作可併行；按原 ID 回填結果。驗證實際模型往返和字幕預算。
通過 B05、B08。以模式切換回純文字，保持既有操作與回執可查。
```

**Claude Code Instructions**

```text
在 RoutineKeel 工作區執行此階段。讀 docs/specs/capability-tool-binding/technical.md 和現有專案規則。
由 provider 重組 Streaming 的工具參數，由底座以已鎖定工具集執行有界循環。用替身驗證截斷、越權、額度、順序後，以已配置且支援的模型實跑。
通過 B05、B08。以模式切換回純文字，保持既有操作與回執可查。
```

### Stage 6 三路補件

**Codex Instructions**

```text
在 RoutineKeel 工作區執行此階段。讀 docs/specs/capability-tool-binding/technical.md 和階段前置規則。
從 blueprints/binding_options.py、completion.py、graph/preparation.py 和 construction.py 接通三條路。自動完成已授權又具條件的候選，保存拒絕原因。實跑原廠來源、配方核對、隔離安裝或生成 Adapter 的具名輸出及試做。
通過 B04、B06 的成功、拒絕、等待與恢復案例。停用任一條準備來源後仍可沿用原鎖及候選。
```

**Claude Code Instructions**

```text
在 RoutineKeel 工作區執行此階段。讀 docs/specs/capability-tool-binding/technical.md 和現有專案規則。
以同一個 work_id 串接三條補件路徑。讓相互獨立的觀測與驗證可併行，安裝依據版本、校驗和授權；寫出的 Adapter 以執行邊界與獨立預期驗收。
通過 B04、B06 的成功、拒絕、等待與恢復案例。停用任一條準備來源後仍可沿用原鎖及候選。
```

### Stage 7 整合、效能與故障驗收

**Codex Instructions**

```text
在 RoutineKeel 工作區執行此階段。讀 docs/specs/capability-tool-binding/technical.md 和階段前置規則。
從 tests/ 與現有發行工具重跑 B01–B09。核對串行與併行等價、冷／熱、p50、p95、實際同時執行、六類工具和三條補件。另實跑 SQLite 還原、IR／wheel RECORD、安裝前重驗及重綁，分開列正式素材與替身證據。
所有必要案例依實測放行；失敗保存具體阻礙並還原前版。
```

**Claude Code Instructions**

```text
在 RoutineKeel 工作區執行此階段。讀 docs/specs/capability-tool-binding/technical.md 和現有專案規則。
依 B01–B09 做故障注入、跨平台與新解壓測試，測兩種初裝故障、授權等待和未知回執。交付重跑命令、量測結果、真實與替身涵蓋及回復證據。
所有必要案例依實測放行；失敗保存具體阻礙並還原前版。
```

### Stage 8 文件與本機交付

**Codex Instructions**

```text
在 RoutineKeel 工作區執行此階段。讀 docs/specs/capability-tool-binding/technical.md 和階段前置規則。
更新 flow_base/docs/blueprint-install.md 和 DEVNOTE.md，保存 SHA-256、候選版本、實測、回復與未測案例。從乾淨工作區重跑交付。正式啟用依 AGENTS.md 的授權處理。
保留可重跑的隔離驗收、操作文件及前版回復位置。
```

**Claude Code Instructions**

```text
在 RoutineKeel 工作區執行此階段。讀 docs/specs/capability-tool-binding/technical.md 和現有專案規則。
維護既有底座與 Studio 操作文件，把初裝診斷、六種綁定、併行、缺件與續做整理成操作步驟。核對本機候選和回復點，依專案授權處理後續正式啟用。
保留可重跑的隔離驗收、操作文件及前版回復位置。
```
