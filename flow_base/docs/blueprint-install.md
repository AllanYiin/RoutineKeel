# 從訪談藍圖建置、驗證與安裝工作流

原始藍圖是設計資料。底座讀取設計與工具綁定，建置出另一個檔案 `workflow.zip`，內含候選工作流的 manifest 與 wheel。驗收時，底座把這個候選包放入臨時工作區，再由 Runtime 執行案例。使用者的工作區此時仍找不到該流程。通過後，正式安裝會再核對同一份候選包與驗收收據，才將它放入使用者的 workflows 目錄。

RK-NEXT 開發版的底座及 Studio 隨附同一份 `routinekeel-contracts` 驗證器。試做成功的收據記錄 `validation.status=PASS` 與 `policy_version`。若缺少驗證器則記錄 `NOT_RUN`，契約或 wheel RECORD 檢查失敗則記錄 `FAIL`，草稿保留。安裝前會從候選 ZIP 重驗 IR、工具簽名與 wheel。舊草稿可以重新試做取得新版收據；已安裝的流程繼續使用原本的 Runtime 入口。

`resolve-blueprint --draft-id <ID> --offline` 會回傳各節點需要的能力、版本、埠、權限、指紋及缺口。底座可直接辨識內建的純資料操作。嵌入宿主可將已連接的 MCP client 交給 `flow_base.adapters.host_inventory.observe_tools`，以實際 tools/list 結果及宿主既有授權產生限時觀測。宿主在工作區外維護該觀測，並以 `ROUTINEKEEL_HOST_CATALOG` 指向它。權限與有效期由宿主維護，藍圖提供的 probe 自述無法授予操作權限。一般使用者不需逐步確認工具清冊。無可信記錄的外部工具會列為缺口。測試中的宿主替身與真實 MCP 清單觀測分開驗收。

Graph v2 可在 Studio 預覽階段先查能力及案例。匯入後，`resolve-blueprint` 的 `gaps` 列出能力缺口，`build_gaps` 列出案例格式及建置支援缺口。兩者都空時 `build_status` 為 `ready`，候選包仍需實際試做。

Graph v2 的桌面宿主工具可在受信清冊提供 `parameters_schema`，採 JSON Schema Draft 2020-12 的物件契約。底座依契約驗證節點參數，並將參數與輸入一起綁定到待辦、核准及操作指紋。領取待辦後，宿主收到 `dispatch.arguments` 與 `dispatch.parameters`。宿主負責將兩者交給宣告該契約的工具。

使用者在對話附上 Studio 訪談 ZIP 並說「安裝這個工作流」。宿主以工作區父目錄的底座操作。四次呼叫可分屬不同對話。

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

## 本機工具與復原

媒體元件以獨立 wheel 隨底座 ZIP 發行。宿主在此流程需要時，以底座啟動器執行 `--install-blueprint-media`，核對內建 SHA-256 後離線安裝。另需 PATH 上的 FFmpeg 和 ffprobe。開發環境可將 `flow_base/components/blueprint_media` 安裝至實際執行底座的 Python。支援雙軌 WAV 組裝、時間軸轉 SRT，以及直式 MP4 合成。長字幕的時間分配及封面時長由已確認的 recipe 或設定決定。

草稿在 share_states/blueprint_drafts。builds 下保留 resolution、案例、Studio WorkflowIR、工具簽章、workflow.zip、驗收及安裝收據。候選 zip 包含正式 manifest、wheel、IR、文件及來源指紋。wheel 內保留原藍圖作為資料、宿主步驟與受限程式。主機綁定、工作進度、核准紀錄、內容定址素材和角色設定分別保存在 share_states。無 SQLite 遷移。首次安裝以隱藏暫存目錄完成整包後更名。若程序中斷，重送同一 build 與 hash 即可辨識已完成的安裝。已安裝版本的升級與回退仍使用既有更新收據。
