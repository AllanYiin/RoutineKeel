# 從訪談藍圖建置、驗證與安裝工作流

原始藍圖是設計資料。底座讀取設計與工具綁定，建置出另一個檔案 `workflow.zip`，內含候選工作流的 manifest 與 wheel。驗收時，底座把這個候選包放入臨時工作區，再由 Runtime 執行案例。使用者的工作區此時仍找不到該流程。通過後，正式安裝會再核對同一份候選包與驗收收據，才將它放入使用者的 workflows 目錄。

## 讓底座接手補強

使用者要求把藍圖轉成工作流時，宿主使用 MCP `blueprint_completion` 的 `start`，或底座 CLI 的 `blueprint-completion start`。傳入工作區內的 `archive` 或既有 `draft_id`，附上這次請求的 `request_id`。底座保存工作 ID、診斷工具與案例、執行已具充分證據的補強，條件齊備便啟動候選包的隔離試做。預設 `intent="build"` 交付通過的候選。使用者明確要求安裝時使用 `intent="install"`，底座依驗收結果安裝，或接續既有版本的比較與升級。

```powershell
flow_base --workspace . blueprint-completion start --archive "blueprints/workflow.zip" --payload request.json
flow_base --workspace . blueprint-completion status --work-id <補強工作識別碼>
```

上例的 `request.json` 內容可為 `{"request_id":"install-from-chat-01","intent":"install"}`。CLI 的 `--archive` 接受使用者明確指定的附件。MCP 啟動與補件使用工作區內的路徑。保存回傳的 `work_id`，可在另一輪對話、CLI 或 MCP 重連後查詢同一件工作。

| 目前狀態 | 宿主接續動作 |
| --- | --- |
| `preparing`、`qualifying`、`trial_queued`、`trialing`、`installing` | 用 `status` 查證實際進度。`trial_progress.progress_available=false` 時，仍可讀取持久化狀態和 `worker_alive`。試做程序中斷時，帶上當前修訂與新請求 ID 呼叫 `advance`。 |
| `needs_source`、`needs_input` | 使用缺口的 `id`、`scope_hash` 和 `actions` 選擇下一步。先說明底座已完成的部分，再請使用者補必要素材、回覆或授權。 |
| `needs_update` | 依 `update.action` 接續既有演進提案。提交獨立比較案例，展示比較結果並取得具體核准後再 `activate_update`。 |
| `verified`、`installed` | 回報已驗證候選或安裝位置、版本與逐案例證據。 |

提交補件時使用 `submit`，帶上當前 `expected_revision`、新的 `request_id` 及指定缺口的資料。對既有請求使用相同內容及識別碼可取回原結果。核對版本衝突時，先以 `status` 重讀最新修訂。可用 `pause`、`resume`、`cancel`、`archive` 管理同一工作，`history` 查閱實際補強與驗收紀錄。

- 工具待辦會提供候選、權限與資格收據。底座可以自動驗證純本機候選。需要使用者選擇或拒絕時用 `select_adapter` 或 `decline_adapter` 提交實際答覆。宿主資格驗證仍依具體範圍另取得授權，並透過受信清冊提交實際證據。清冊更新後用 `recheck` 接續。
- 可用 `complete_case` 補上缺少的輸入與獨立預期輸出。案例對應原藍圖的驗收意圖。若藍圖在 `quality_contract.case_recipes` 提供同一案例序號的明確斷言，底座可自動產生案例。斷言支援具名輸入的 `identity`、`uppercase`，以及明列的 `value`。已確認案例或業務規則要改時，先由 Studio 匯出帶來源關聯的新修訂，再用 `preview_revision` 與 `adopt_revision` 核對並採用。
- 用 `attach_fixture` 複製工作區內的素材，回傳名稱、大小、內容指紋及可供案例引用的位置。若要補進案例指定的位置，一併提供缺口、範圍指紋與 `requested_path`。已存在的同名不同內容會保留，並提示核對原素材。
- 隔離試做失敗會保留案例、檢查階段和錯誤證據。修正輸入、綁定或已確認案例後，底座重驗再接續。必要驗證、實際候選試做及安裝前重驗均須符合正式安裝條件。

開發回滾與版本證據見原始碼儲存庫的 `docs/specs/blueprint-completion.md`。遷移到 v7 前底座會建立有指紋的舊資料備份。需要回到只支援 v6 的底座時，先保留現有 v7 資料，再於獨立位置還原並驗證備份。既有工作流升級後的回復走原更新收據。

RK-NEXT 開發版的底座及 Studio 隨附同一份 `routinekeel-contracts` 驗證器。試做成功的收據記錄 `validation.status=PASS` 與 `policy_version`。若缺少驗證器則記錄 `NOT_RUN`，契約或 wheel RECORD 檢查失敗則記錄 `FAIL`，草稿保留。安裝前會從候選 ZIP 重驗 IR、工具簽名與 wheel。舊草稿可以重新試做取得新版收據；已安裝的流程繼續使用原本的 Runtime 入口。

`resolve-blueprint --draft-id <ID> --offline` 會回傳各節點需要的能力、版本、埠、權限、指紋及缺口。底座可直接辨識內建的純資料操作。嵌入宿主可將已連接的 MCP client 交給 `flow_base.adapters.host_inventory.observe_tools`，以實際 tools/list 結果及宿主既有授權產生限時觀測。宿主在工作區外維護該觀測，並以 `ROUTINEKEEL_HOST_CATALOG` 指向它。權限與有效期由宿主維護，藍圖提供的 probe 自述無法授予操作權限。一般使用者不需逐步確認工具清冊。無可信記錄的外部工具會列為缺口。測試中的宿主替身與真實 MCP 清單觀測分開驗收。

Graph v2 可在 Studio 預覽階段先查能力及案例。匯入後，`resolve-blueprint` 的 `gaps` 列出能力缺口，`build_gaps` 列出案例格式及建置支援缺口。兩者都空時 `build_status` 為 `ready`，候選包仍需實際試做。

Graph 2.3 的一般或巢狀節點可用 `capability_requirement` 宣告相容版本、資料語意、可替換條件與限制。Pattern 及此類一般節點使用共用解析、資格與環境鎖。`blueprint_completion` 會保存觀測識別、淘汰候選和判定欄位。宿主更新受信清冊後，以同一工作識別碼 `recheck`。未宣告需求的既有節點沿用原指定工具及版本。

宿主觀測多個連線時，可用 `observe_tools` 的 `source_id` 識別連線，為同名工具給 `routes` 派送名稱，再以 `merge_tool_observations` 合併。連線識別、派送名稱與實際工具名稱隨觀測保存。派送回覆提供 `source_id` 和 `provider_tool` 供宿主呼叫對應的連線。每個連線的授權仍由宿主提供。

可替換需求若備有完整的輸入與輸出語意，且當前清冊沒有相符的單一工具，底座會搜尋並編譯多工具方案。`toolchain_planning` 記錄步驟、資料邊、合計費用、編譯圖指紋與搜尋進度。出現 `search_limit_reached` 時，對同一補強工作執行 `advance` 可繼續展開；`recheck` 會先核對觀測是否變動。

宿主可經 `submit` 的 `propose_toolchain` 提供 `proposal.steps` 與 `proposal.outputs`。每個步驟宣告 `id`、清冊中的 `tool` 與具名 `inputs`，資料引用沿用 `input://` 和 `artifact://`。請求帶上目前的 `gap_id`、`scope_hash`、`expected_revision` 與 `request_id`。底座依現行清冊重建步驟，驗證資料語意、權限和整鏈成本，給出受控的候選識別。已有多個候選或需要宿主行為證據時，用 `select_adapter` 提交適用的選擇。

合格的工具鏈會在環境鎖與候選中保存同一份編譯圖。純本機工具鏈以獨立程序比對已確認案例。宿主工具鏈須從受信清冊提供與目前資格範圍相符的 `portable_qualification`，含 `toolchain_graph_hash` 及每案各步驟的綁定指紋、成本與實際回執。候選試做中的宿主替身仍標為模擬，正式執行各子步驟依原權限逐次派送。

尚未安裝的本機工具可由受信清冊提供 `install_locked_wheels`。宿主核准個別配方後，以 `preparation_allowed` 表示準備授權。底座從清冊所在目錄開啟宣告的 wheel，核對平台、名稱、版本、依賴與 SHA-256，在工作區隔離位置離線安裝。`preparations` 保存操作 ID、各項配方與收據。更新清冊後，呼叫原補強工作的 `recheck`，讓底座重新觀測並續接。

比較候選時，核對 `candidate_rejections` 的原因與欄位，再檢查 `toolchain_planning.plans` 的具名輸入、資料邊、步驟、目的地和合計成本。對照資格報告與候選試做的獨立預期值。若用了宿主替身或校閱答覆，候選收據會列出模擬步驟。安裝前會再次核對收據、候選 ZIP、環境鎖和實際工具。每條能力需求都可以留在有續作動作的缺口狀態，直到取得相符工具、案例或授權。

尚待設定的工具可由宿主清冊提供 `host_preparation`。底座只呈現當前需求或可行工具鏈所需的具名配置。操作者核對目的地、權限、參數與成本後，以 `authorize_host_preparation` 提交實際答覆。同一工作用 `claim_host_preparation` 領取單次 `host_dispatch`。以上動作均使用原 `operation_id`、`scope_hash`、目前 `expected_revision` 和請求識別碼。重送、連線中斷或同一服務已有待查操作時，先依原操作識別碼查詢服務結果。

宿主持有外部回執。回執須包含原範圍、已觀測成本、目標指紋及實際配置指紋。以 `reconcile_host_preparation` 查證，刷新清冊後底座比對服務的實際狀態。若已知結果需要回復，`request_host_compensation` 產生另一個具名待辦，沿相同授權、派送與回執程序執行。程序失聯時，已確認的結果和回復證據仍可依原工作查閱。

受信清冊也可提供 Adapter 的建置配方。`construct_mapping` 使用具名輸入、JSON Pointer 與欄位投影；逐列投影保留來源順序，缺少必要欄位時回報 `ADAPTER_INPUT_REJECTED`。`construct_wasm` 使用已鎖定的 WebAssembly 來源。底座在獨立程序檢查記憶體上限、無宿主匯入及 Adapter 介面，再封存程式與執行器指紋。兩種 Adapter 都須通過已確認案例及隔離候選試做，原案例的預期值仍用於驗收。若執行器或登錄產物改變，原資格即失效。

Graph v2 的桌面宿主工具可在受信清冊提供 `parameters_schema`，採 JSON Schema Draft 2020-12 的物件契約。底座依契約驗證節點參數，並將參數與輸入一起綁定到待辦、核准及操作指紋。領取待辦後，宿主收到 `dispatch.arguments` 與 `dispatch.parameters`。宿主負責將兩者交給宣告該契約的工具。

需要個別檢視或手動控制建置時，使用下列分段入口。每次呼叫都可由不同對話接續。

對外操作會顯示本次工具、實際參數、範圍與資源資訊，使用者可在目前對話同意或拒絕。核准綁定待辦、資料指紋及版本。宿主領取後只能用同一 idempotency key 查核；斷線或缺少獨立回執時顯示 RECONCILE_REQUIRED，保留待辦。驗收工作區另有拋棄式宿主回執目錄，模擬案例不會在正式宿主留下執行或付款紀錄。

| 時機 | CLI（在底座啟動器後） | 回傳與下一步 |
|---|---|---|
| 收到附件 | `install-workflow --archive <ZIP>` | `draft_id` 是來源 SHA-256，含步驟、能力需求、現有本機元件與交叉引用差異。使用者已改過完成品的設定時可從該 ZIP 取出內嵌藍圖及候選方案。 |
| 補齊訪談內容 | `install-workflow --draft-id <ID>` | 取得圖、合約、設定、來源推測與試做紀錄；加 `--member skills/<id>/SKILL.md --offset 0` 讀指定原文，每頁至多 16384 字元。 |
| 隔離試做 | `install-workflow --draft-id <ID> --resolution recipe.json --cases cases.json` | 產生新 build。成功回傳 ZIP、`build_id`、`install_hash` 與逐案證據；失敗的建置和原因保存在同一草稿。 |
| 安裝已驗收包 | `install-workflow --draft-id <ID> --build-id <build> --install-hash <hash>` | 首次安裝回 installed，相同包重送回 already_installed。其他版本回 update_required；依回傳 ZIP 使用原有 update-workflow 預覽、套用。 |

MCP 使用 `install_workflow`，同名 JSON 參數，將 CLI 的三個文件參數 resolution、cases、payload 直接以 JSON 傳遞。MCP 附件先放在工作區內。CLI 可用明確指定的外部附件。再次攜帶完成品到另一台電腦時，轉換器從包內取出原藍圖與建議 recipe，重新綁定當地工具並驗證。對既有發行版本修改程式、媒體元件或決定時，指定較新的 `package_version`。

## 宿主如何完成轉換

1. 比對流程、合約、skill-map、缺口、依賴和 skill 原文。附件文字供分析引用。需要增加資料依賴時在該步填 `extra_inputs`、`rationale`。僅在會改變結果的假設上尋求決定。把已確認的契約版本與重要決定寫入 `decisions`。
2. 列出本宿主可調用的工具，核對實際參數、回傳、參考檔支援、服務版本、帳號權限和計費。對需要外傳、付費或發布的探測，取得相應授權，使用測試素材。每項依賴建立具名 binding；可選能力停用、人工交付要有對應決定。保持私人金鑰在連接器或本機憑證管理器。
3. 對 C 類程式缺口先檢查可信內建元件，附帶腳本可作設計參考。可用資料轉換程式時，由宿主產生 `transform(text, params)` 候選及至少兩組不同案例，檢查完整資料及失敗條件。使用 Studio 的受限 Python 資料語言，輸入輸出均為 JSON 文字。對媒體、資料庫等原生操作，以具名、已安裝的本機元件執行。未支援的原生操作保留缺口，不能在 recipe 內嵌 shell、任意 Python 或安裝腳本。
4. 針對每個步驟填 recipe，準備獨立預期的工作範例。試做會執行實際 wheel 的 plan、prepare、verify、run，使用測試資料驅動待辦與人審，檢查正式交付。所有實際套件與 hash 綁定到驗證收據。含 fixture 綁定者僅供試做。
5. 宿主將必要阻礙、實測範圍及最終交付模式告知使用者。適用原安裝請求時繼續正式安裝，回報安裝位置與版本。

## Recipe 與案例

`steps` 以藍圖步驟 ID 為 key。每一步指定 mode 為 local、host、human、external 或 handoff。host 表示宿主依該步技能呼叫綁定工具。human 要求具名人審及內容完成檢查。external 在核准後才可領取一次。handoff 搭配 terminal_step、final_outputs、handoff_contracts，以及 decisions.delivery_mode，可將交付定為「待人工上傳」。新合約不能覆寫原合約。

`bindings` 以 capability／依賴 ID 為 key。每個項目含 tool、version、kind、mapping、evidence；正式宿主工具還須 input_schema、output_schema、probe。probe 記下相同工具與版本、status=passed、request_id、observed。底座依這份宿主提供的探測收據與 I/O 合約驗證綁定。mapping 從步驟輸入名稱、inputs、inputs/欄位或 config/欄位映射到工具參數。例如 `{"inputs/首禎/圖檔":"reference_image","inputs/主角配音/音檔":"audio"}`。output_mapping 可將回應欄位取為流程輸出。claim_required 可用於需要防止重送的付費宿主任務。只傳遞引用與必要欄位，測試替身填 kind=fixture。

`gaps` 為每個缺口記錄處理方式與驗證位置。確定性程式透過 result、arguments、assertion 說明轉換與完成條件，context 可讀 inputs、config、answer、result，assertion 另讀 outputs。text_inputs 可宣告要讀取的 UTF-8 小型文字素材。程式測例放 examples 的 context 與 expected；同時有 arguments 與 result 時，還要 expected_arguments。需要實體檔的輸出以 asset_fields 指定路徑，* 代表清單每一項。角色前置可用 reuse_approved，加 profile_config 列出影響該角色的設定鍵。

`cases` 為陣列，每案含 inputs、config、replies、expected。素材以 `{"$file":"fixtures/name.wav"}` 明確匯入，放在工作區 fixtures/。replies 依序指定 step、kind、outputs。expected 覆蓋全部最終產物；物件可指定要精確比對的欄位，`{"$artifact":true}` 驗證實體檔存在、非空且 SHA-256 正確。模擬的外部回傳與真人確認會在報告逐步列出。完整可執行 recipe 與兩種交付模式在 `source.zip` 的 `examples/blueprint-install/reel_trial.py`。

## 執行、等待與恢復

從 `run --task <workflow_id> --input <request.json>` 開始，request 含具名 inputs 與 config。檔案相對於 request 所在位置且位於工作區內。已取得 profile_id 時可省略先前核准的角色來源。透過 MCP 亦可使用原有 submit_workflow 與 worker，從 get_workflow_result 取得 blueprint_session_id。

底座回傳 blueprint.session_id 及待辦。宿主使用 `blueprint-task <session_id> next` 取得當前節點、技能原文、素材、工具綁定、實參與輸出合約。以 `blueprint-task <session_id> complete --payload response.json` 交回 task_id、revision、outputs、evidence。MCP 對應 `blueprint_task(session_id,operation,payload)`。宿主回傳的 $file 相對工作區。重讀待辦後才可提交，過期結果會拒絕。人審的 evidence 需含 actor 及 decision=approved，代表宿主已取得當事人對該內容的決定。

external 或需防重送的操作先 claim，取回新 revision 後才執行。下次讀到 reconcile_receipt 時以原待辦 ID 查詢供應商紀錄，取得 receipt 才 complete。無法確認結果時保存待辦。revise 需要 revision、要退回的 step、reason，會使該步及之後的結果失效。完成時回傳正式 run_id 和 outputs 位置，results.json 以內容指紋連結實際素材。已領取對外請求時不可倒帶重送。

### 原工作續做與重綁

工具失效時，Runtime 停止受影響的派送，並回傳 `REBIND_REQUIRED` 與 `rebind_work_id`。對原 `execution_id` 查詢佇列，或以原 `session_id` 查詢 Graph 工作。用 `blueprint-completion status` 追蹤該次補強。修復後原綁定通過檢查時，`next` 繼續原 Run。等效替換通過同一 Builder 的資格與試做後，`next` 會建立同一工作下唯一的續做 Run，保留 `root_run_id`、`parent_run_id`、`active_run_id` 與原 Run 的成果紀錄。

```powershell
flow_base --workspace . blueprint-completion status --work-id $RebindWorkId
flow_base --workspace . blueprint-task $SessionId status
flow_base --workspace . blueprint-task $SessionId preview_rebind
flow_base --workspace . blueprint-task $SessionId next
```

`preview_rebind` 在候選通過試做後取得可啟用的範圍。若回覆 `needs_approval`，向使用者顯示權限、目的地、連網和成本差異，並取得這次操作的實際同意。把 `scope_hash`、`source_checkpoint` 的值分別填入 `scope_hash`、`revision`，並把實際答覆填入 `user_text`。以 `flow_base --workspace . blueprint-task $SessionId activate_rebind --payload approval.json` 啟用。MCP 使用 `blueprint_task` 的相同操作與 payload。範圍改變時重新預覽。以同一 `scope_hash` 重送可取回已保留的續做 Run。

持有已領取但尚無確定回執的外部操作時，先在原待辦查證結果。若不能證明既有外部效果可在原授權範圍內沿用，底座保留待處理狀態。已完成且可驗證的資料與校閱會在續做節點保留 `inherited_from`。最終交付以結果回覆的 `run_id` 及 `artifacts` 為準。要改業務規則或交付內容，先由 Studio 匯出新版本並走原有版次採用及驗收。

雙 CSV 驗收先讀取兩份報表，再以正規化及逐鍵比較取得差異。替換中間工具後，續做 Run 沿用原讀取指紋，交付 `K01` 相符、`K02` 差 `0.10`、`K03` 僅出現在右表。測試按相同工作 ID 追查兩份候選收據與兩次 Run。

## 本機工具與復原

媒體元件以獨立 wheel 隨底座 ZIP 發行。宿主在此流程需要時，以底座啟動器執行 `--install-blueprint-media`，核對內建 SHA-256 後離線安裝。另需 PATH 上的 FFmpeg 和 ffprobe。開發環境可將 `flow_base/components/blueprint_media` 安裝至實際執行底座的 Python。支援雙軌 WAV 組裝、時間軸轉 SRT，以及直式 MP4 合成。長字幕的時間分配及封面時長由已確認的 recipe 或設定決定。

草稿在 share_states/blueprint_drafts。builds 下保留 resolution、案例、Studio WorkflowIR、工具簽章、workflow.zip、驗收及安裝收據。候選 zip 包含正式 manifest、wheel、IR、文件及來源指紋。wheel 內保留原藍圖作為資料、宿主步驟與受限程式。主機綁定、工作進度、核准紀錄、內容定址素材和角色設定分別保存在 share_states。無 SQLite 遷移。首次安裝以隱藏暫存目錄完成整包後更名。若程序中斷，重送同一 build 與 hash 即可辨識已完成的安裝。已安裝版本的升級與回退仍使用既有更新收據。

續做使用工作範圍內的候選套件。回復原工具及其授權時，從原工作 `next` 觸發重驗。已啟用的續做及原 Run 都保留供查核。正式套件更新中斷時用 `recover-workflow-update`，已完成的套件更新以 `rollback-workflow-update <update_id>` 使用原快照。套件和產物保留規則見隨包 `workflow-updates.md`。

需要驗證舊版資料庫備份時，先核對 `share_states/state.sqlite3.before-v007.sqlite3` 與同名 `.sha256`。以 SQLite 唯讀開啟備份，再用 SQLite 備份功能寫入獨立測試工作區。新副本執行 `PRAGMA integrity_check` 確認為 `ok`，以 `PRAGMA user_version` 確認為 6，再查驗代表性流程與 Run 記錄。其他舊版使用對應的遷移備份及其原資料版本。正式切換前保留目前資料庫、工作區與底座版本。

部署驗收在拋棄式工作區核對 `SHA256SUMS`，再執行底座 `catalog`、Studio `guide`、真實 MCP 工具清單及已安裝工作流的案例。依流程確認有無適用的本機工具與外部授權。完成試做及資料還原演練後，保留舊版軟體、工作區備份、候選檔與驗收收據。
