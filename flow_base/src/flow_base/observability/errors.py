"""Stable, local-only error explanations; legacy codes remain public identities."""
from __future__ import annotations

import errno
import subprocess

from pydantic import ValidationError

STANDARD = {
    'GRAPH_UNSUPPORTED': ('configuration', 'error', '流程圖使用未支援的功能', '目前底座無法建置這項流程圖宣告。',
                          ['保留藍圖草稿並檢查節點種類。', '使用支援該版本的底座重新驗證。'], 'after_fix', ['blueprint_schema_version']),
    'GRAPH_INVALID': ('configuration', 'error', '流程圖驗證失敗', '流程圖的型別、依賴或路徑無法成立。',
                      ['查看錯誤指出的來源或匯合節點。', '修訂草稿並重新驗證。'], 'after_fix', ['validation_issue']),
    'STUDIO_UNAVAILABLE': ('environment', 'error', '訪談服務無法啟動', '未收到本機 Studio 的有效回覆。',
                           ['用發行包的 Studio 啟動器準備環境。', '確認本機 Studio Python 路徑及版本。'], 'after_fix', ['studio_environment']),
    'STUDIO_TIMEOUT': ('execution', 'error', '訪談服務回覆逾時', '目前無法確認訪談操作是否已經保存。',
                       ['先重新讀取專案狀態。', '必要時重送相同請求識別碼。'], 'after_fix', ['request_id']),
    'USER_CONFIRMATION_REQUIRED': ('permission', 'warning', '等待使用者確認', '此項決定需要使用者在可信本機文字終端確認。',
                                   ['查看問題、回答與專案版本。', '在可信終端完成或取消此次確認。'], 'after_fix', ['project_id', 'revision']),
    'INPUT_NOT_FOUND': ('input', 'error', '找不到輸入', '指定的檔案、流程或執行紀錄不存在。',
                        ['確認名稱與路徑後重新選取。', '若專案曾搬移，從目前工作區重新開啟。'], 'after_fix', ['requested_identity']),
    'INVALID_CONFIGURATION': ('configuration', 'error', '設定需要修正', '設定或輸入參數未通過檢查。',
                              ['檢查設定檔與預先檢查列出的欄位。', '修正後重新產生計畫再執行。'], 'after_fix', ['validation_issue']),
    'DISK_SPACE_INSUFFICIENT': ('storage', 'error', '可用空間不足', '本機磁碟空間不足以安全完成這次工作。',
                                ['保留既有成果，先釋出目標磁碟空間。', '確認剩餘空間後重新執行。'], 'after_fix', ['required_bytes', 'available_bytes']),
    'RESOURCE_INTEGRITY_FAILED': ('resource', 'error', '資源完整性未通過', '模型、快取或交付檔案缺失或與記錄不符。',
                                  ['檢查檔案是否移動或修改。', '依流程說明重新準備資源或重算受影響步驟。'], 'after_fix', ['expected_hash', 'observed_hash']),
    'DEPENDENCY_UNAVAILABLE': ('dependency', 'error', '所需環境尚未就緒', '執行所需的環境、資源或服務目前不可用。',
                               ['執行 doctor 查看缺項。', '準備所需環境後重試；離線時先確認快取已齊全。'], 'after_fix', ['dependency_status']),
    'PROCESS_START_FAILED': ('process', 'error', '無法啟動工作程序', '本機 Worker 或其執行環境未能啟動。',
                             ['查看 process evidence 的啟動錯誤。', '確認執行檔與權限後重試。'], 'after_fix', ['process_evidence']),
    'WORKER_TIMEOUT': ('process', 'error', '處理超過時間限制', 'Worker 未在設定時間內完成，底座已嘗試停止程序樹。',
                       ['查看最後進度與 process evidence。', '確認輸入大小和資源，必要時調整流程逾時設定。'], 'manual', ['timeout_seconds', 'last_event']),
    'WORKER_CRASHED': ('process', 'error', '工作程序異常結束', 'Worker 回報非零退出；目前不能只憑退出碼確定根因。',
                       ['查看 Worker 日誌及 process evidence。', '保留 error_id，將已遮罩的診斷交給維護者。'], 'manual', ['exit_code', 'process_evidence']),
    'STATE_CONFLICT': ('state', 'error', '目前狀態無法執行此操作', '工作可能仍在執行、被取消或已由另一個程序更新。',
                       ['查詢 Run 狀態與最後事件。', '等待進行中的操作結束後重新取得狀態。'], 'manual', ['run_state', 'state_version']),
    'DELIVERY_FAILED': ('delivery', 'error', '成果未能安全交付', '新版本未通過校驗或發布；前一版正式成果應保持可用。',
                        ['查看交付版本與交易診斷。', '確認 staging 和磁碟狀態後重試，不要手動覆寫正式版本。'], 'manual', ['delivery_manifest', 'pointer']),
    'PERMISSION_DENIED': ('permission', 'error', '操作尚未獲准', '此動作需要明確確認，或目前路徑不允許寫入。',
                          ['檢查目標路徑與操作範圍。', '只有在確認影響後才重新執行需要核准的操作。'], 'manual', ['path_policy']),
    'UNEXPECTED_INTERNAL_ERROR': ('internal', 'error', '發生未預期的內部錯誤', '目前沒有足夠證據判定根因。',
                                  ['保留 error_id 與最後事件。', '匯出已遮罩診斷交給維護者查核。'], 'manual', ['exception_chain']),
    'DIAGNOSTIC_PERSIST_FAILED': ('diagnostic', 'error', '問題紀錄未完整保存', '部分日誌或診斷未能寫入；原始錯誤仍保留。',
                                  ['記下 error_id 與終端顯示的最低限度訊息。', '確認日誌磁碟與權限後再重試。'], 'after_fix', ['diagnostic_persistence']),
    'UNKNOWN': ('unknown', 'error', '尚無法判定問題', '目前證據不足，不能猜測故障原因。',
                ['記下 error_id 與最後已確認事件。', '匯出已遮罩診斷供維護者查核。'], 'manual', ['event_timeline']),
}

# Every base FlowError code is explicit here; tests compare this list with source.
GROUPS = {
    'INPUT_NOT_FOUND': '''FLOW_UNKNOWN INPUT_MISSING RUN_UNKNOWN EXECUTION_UNKNOWN TASK_UNKNOWN RESOURCE_MISSING DIAGNOSTIC_UNKNOWN DELIVERY_UNKNOWN''',
    'INVALID_CONFIGURATION': '''API_KEY_MISSING BUDGET_EXHAUSTED INTENT_CONFIG INTENT_PARAMETER INTENT_TASK INTENT_TRUNCATED
        OUTPUT_CONFLICT OUTPUT_LAYOUT PREFLIGHT_BLOCKED PROCESS_ARGUMENTS PROFILE_COLLISION PROFILE_INVALID
        ROUTER_DISABLED ROUTER_NOT_CONFIGURED RUN_SPEC_INVALID RUN_SPEC_MISMATCH TASK_ACTION_PARAMETERS
        TASK_CONFIG TASK_ACTION_UNKNOWN TASK_CAPABILITY USAGE_REQUEST_ID INPUT_OR_ENVIRONMENT''',
    'DISK_SPACE_INSUFFICIENT': '''DISK_SPACE''',
    'RESOURCE_INTEGRITY_FAILED': '''ARCHIVE_INVALID ARTIFACT_LINK ARTIFACT_MISSING ARTIFACT_PATH ARTIFACT_SET_INVALID
        RESOURCE_HASH RESOURCE_REDIRECT RESOURCE_SIZE TASK_ARCHIVE TASK_INTEGRITY''',
    'DEPENDENCY_UNAVAILABLE': '''DOWNLOAD_FAILED ENV_INVALID ENV_MISSING OFFLINE_LLM OFFLINE_RESOURCE_MISSING
        PROFILE_MISSING SUPERVISOR_HEARTBEAT TASK_PREPARE UV_MISSING PLAN_NO_STATE''',
    'PROCESS_START_FAILED': '''PROCESS_START SUPERVISOR_IDENTITY SUPERVISOR_START''',
    'WORKER_TIMEOUT': '''PROCESS_TIMEOUT''',
    'WORKER_CRASHED': '''PROCESS_FAILED''',
    'STATE_CONFLICT': '''BUSY DELIVERY_CONFLICT HUMAN_REVIEW_PRESENT IDEMPOTENCY_CONFLICT LLM_UNCERTAIN
        OUTPUT_EXISTS PROCESS_CANCELLED RUN_CANCEL_REQUESTED RUN_CONTEXT_MISMATCH RUN_CONTEXT_REQUIRED
        STATE_CONFLICT TASK_AMBIGUOUS TASK_DUPLICATE TASK_RESTART INTERRUPTED
        SCHEMA_BACKUP_INVALID SCHEMA_MISMATCH SCHEMA_TOO_NEW RETRY_UNSAFE''',
    'DELIVERY_FAILED': '''DELIVERY_INCOMPLETE DELIVERY_INVALID DELIVERY_PATH DELIVERY_RECOVERY_REQUIRED
        DELIVERY_REQUIRED DELIVERY_RUN_ID DELIVERY_STAGE_EXISTS DELIVERY_TRANSACTION_EXISTS
        DELIVERY_VERSION_EXISTS''',
    'PERMISSION_DENIED': '''ARCHIVE_UNTRUSTED GC_CONFIRM_REQUIRED GC_PATH LOCAL_LLM_FORBIDDEN PLAN_SIDE_EFFECT
        OUTPUT_LINK OUTPUT_PROTECTED ROLLBACK_CONFIRM_REQUIRED TASK_FOLDER''',
    'UNEXPECTED_INTERNAL_ERROR': '''HANDLER_CONTRACT LLM_RESPONSE_TOO_LARGE UNEXPECTED_ERROR PLAN_PROBE_FAILED''',
    'DIAGNOSTIC_PERSIST_FAILED': '''EVENT_LOCK_TIMEOUT EVENT_PERSIST_FAILED EVENT_TOO_LARGE PROCESS_EVIDENCE PROCESS_LOG_FAILED''',
}

GROUPS['RESOURCE_INTEGRITY_FAILED'] += ' WORKFLOW_UPDATE_INVALID'
GROUPS['STATE_CONFLICT'] += ' WORKFLOW_UPDATE_CONFLICT WORKFLOW_UPDATE_PENDING'

ALIASES = {}
for canonical, names in GROUPS.items():
    for name in names.split():
        if name in ALIASES:
            raise RuntimeError(f'Duplicate Error Catalog alias: {name}')
        ALIASES[name] = canonical

SPECIFIC = {
    'WORKFLOW_UPDATE_INVALID': ('流程 ZIP、更新紀錄或備份未通過完整性與安全檢查。', ['保留現有資料，取得正確的流程發行 ZIP 後重試；備份損壞請交維護者查核。']),
    'WORKFLOW_UPDATE_CONFLICT': ('附件、目標版本或套件內容已改變，不能直接套用或回復。', ['重新預覽新版 ZIP，核對流程識別碼與版本；不要手動覆蓋設定或成果。']),
    'WORKFLOW_UPDATE_PENDING': ('上次流程更新尚未完成，底座已暫停載入套件。', ['執行 recover-workflow-update 復原，再重新預覽與更新。']),
    'INPUT_MISSING': ('輸入檔案不存在或不是一般檔案。', ['重新選取可讀的輸入檔案。']),
    'TASK_UNKNOWN': ('工作區內找不到指定流程。', ['確認流程包與 flow_base 放在同一工作區後重新列出流程。']),
    'EXECUTION_UNKNOWN': ('找不到指定的待執行工作。', ['核對完整 execution_id 與工作區位置。']),
    'BUSY': ('另一個寫入型 Run 正在使用工作區。', ['用 history 或 diagnose 確認其狀態，完成後再提交。']),
    'HUMAN_REVIEW_PRESENT': ('正式成果含人工修訂或核准，重跑不能覆寫。', ['使用流程宣告的審閱能力，或選擇新的輸出工作。']),
    'OFFLINE_RESOURCE_MISSING': ('離線模式缺少流程需要的資源。', ['連線準備資源並驗證後，再以離線模式執行。']),
    'RESOURCE_HASH': ('下載資源的雜湊與宣告不符。', ['不要使用該檔案；重新準備並驗證資源。']),
    'PROCESS_FAILED': ('Worker 非零退出，根因仍待日誌與 OS 證據查證。', ['用 diagnose 查最後事件，再檢查 Worker 日誌與 process evidence。']),
    'PROCESS_TIMEOUT': ('Worker 超過逾時限制並已進入停止程序。', ['查看最後進度；確認資源後由使用者決定是否重試。']),
    'SCHEMA_TOO_NEW': ('資料庫來自較新版底座。', ['使用相容的新版本開啟，不要以舊版程式寫入。']),
    'SCHEMA_MISMATCH': ('資料庫欄位或遷移紀錄不一致。', ['停止寫入，依已驗證備份與 migration 指引還原。']),
    'SCHEMA_BACKUP_INVALID': ('遷移備份校驗失敗。', ['停止升級，保存原資料庫並重新建立可驗證備份。']),
    'DELIVERY_RECOVERY_REQUIRED': ('前次交付交易尚未安全完成恢復。', ['先重新開啟專案完成 reconcile，保留 transaction journal。']),
    'DELIVERY_INVALID': ('版本清單或其中檔案不完整。', ['保留前一版指標，依錯誤 ID 查出缺少的檔案。']),
    'ROLLBACK_CONFIRM_REQUIRED': ('回復舊成果尚未取得明確確認。', ['檢視目標版本與影響，再以 confirm 重新執行。']),
    'GC_CONFIRM_REQUIRED': ('清理仍在 dry-run 階段。', ['先檢視候選與位元組數，確認後才執行清理。']),
    'API_KEY_MISSING': ('流程啟用了 API，但設定的環境金鑰不存在。', ['只在本機安全設定金鑰，重新執行驗證。']),
    'BUDGET_EXHAUSTED': ('已達流程設定的 API 用量限制。', ['檢查用量收據，調整工作或經核准的預算後重試。']),
    'UV_MISSING': ('找不到 uv。', ['使用隨附啟動器準備 uv，再重新開啟流程。']),
    'TASK_INTEGRITY': ('已安裝流程包與 manifest 指紋不一致。', ['從可信發行包重新安裝該流程；保留既有 outputs。']),
    'OUTPUT_EXISTS': ('輸出資料夾屬於其他工作或含未識別資料。', ['選擇空資料夾或原工作既有輸出位置，不要手動覆寫。']),
    'OUTPUT_PROTECTED': ('輸出路徑指向底座、狀態或其他受保護區域。', ['選擇流程 outputs/<工作> 之下的獨立成果資料夾。']),
    'TASK_ACTION_UNKNOWN': ('流程沒有宣告此操作。', ['執行 catalog 查看可用 actions，使用正確名稱。']),
    'TASK_CAPABILITY': ('已安裝流程未提供要求的能力。', ['查看流程 manifest 的能力清單，改用已宣告的操作。']),
    'TASK_RESTART': ('流程套件在本次工作階段中已更動。', ['關閉底座後重新開啟，以載入一致版本。']),
    'DELIVERY_UNKNOWN': ('找不到指定的交付版本。', ['用 history 或 diagnose 核對 delivery_id。']),
    'RUN_CANCEL_REQUESTED': ('取消已提出或工作正在停止。', ['等待 Worker 真正退出，再查看最終 Run 狀態。']),
    'PROCESS_CANCELLED': ('Worker 已確認停止。', ['查看 Run 狀態與已保存進度；需要時建立新 Run。']),
    'DELIVERY_REQUIRED': ('執行完成但尚無可驗證的正式交付。', ['保留 staging，檢查交付清單後再發布。']),
    'DELIVERY_STAGE_EXISTS': ('同一 Run 的 staging 已存在。', ['先用 diagnose 確認前次交易，再執行恢復。']),
    'DELIVERY_TRANSACTION_EXISTS': ('交付 journal 尚未清理。', ['重新開啟專案執行恢復，不要刪除 transaction 檔。']),
    'OUTPUT_LINK': ('輸出路徑包含符號連結或 junction。', ['改用直接的本機資料夾，避免指向受保護區域。']),
    'ARCHIVE_UNTRUSTED': ('流程封裝來源未通過可信檢查。', ['只使用來源可追溯的正式發行包。']),
    'ROUTER_DISABLED': ('可選 LLM 路由已停用。', ['改用本機路由，或在確認政策後啟用設定。']),
    'ROUTER_NOT_CONFIGURED': ('LLM 路由缺少完整設定。', ['檢查 provider、價格驗證與本機金鑰設定。']),
    'OFFLINE_LLM': ('離線模式不能呼叫外部 LLM。', ['改用本機路由，或明確切換到允許連線的模式。']),
    'LOCAL_LLM_FORBIDDEN': ('目前流程禁止外部 LLM API。', ['沿用本機流程與宿主路由，不要傳送流程輸入。']),
    'LLM_UNCERTAIN': ('上次 API 請求結果不明，不能安全自動重送。', ['檢查用量收據與歷史，再由人決定是否重試。']),
    'TASK_AMBIGUOUS': ('多個流程符合所選名稱。', ['用 catalog 查穩定 flow_id，明確指定目標。']),
    'TASK_DUPLICATE': ('工作區有重複流程身分。', ['移除或修正重複的流程包後重新列出。']),
    'ENV_MISSING': ('流程專用 Python 環境尚未建立。', ['先執行 prepare，再驗證所選 profile。']),
    'ENV_INVALID': ('流程專用環境未通過建立後檢查。', ['保留安裝紀錄，重新準備該 profile。']),
    'PROFILE_MISSING': ('流程宣告的依賴 profile 未登錄。', ['檢查流程包的 profile 設定與 lock 檔。']),
    'PROFILE_INVALID': ('依賴 profile 的 Python 或 uv lock 設定無效。', ['對照流程 README 修正 profile，再執行 prepare。']),
    'SUPERVISOR_HEARTBEAT': ('本機監督程序未能維持執行 lease。', ['重新開啟專案執行 reconcile，查看最後事件與程序證據。']),
    'RUN_CONTEXT_REQUIRED': ('操作缺少本次 Run 的安全脈絡。', ['透過底座執行流程；維護者應檢查 Adapter 的 context 傳遞。']),
    'PROCESS_EVIDENCE': ('Worker 證據檔無法保存。', ['確認 share_logs 的空間與權限，保留已顯示的 error_id。']),
    'PROCESS_LOG_FAILED': ('Worker 輸出未能完整保存。', ['查看 process evidence 的截斷／寫入狀態，確認日誌空間。']),
    'EVENT_PERSIST_FAILED': ('關鍵事件在操作前無法落盤。', ['確認 share_logs 可寫，修復後再重試；操作尚未安全開始。']),
    'EVENT_LOCK_TIMEOUT': ('事件紀錄被另一程序占用過久。', ['確認同一 Run 的其他程序狀態後重試。']),
    'GC_PATH': ('清理候選含不安全連結或越界路徑。', ['保留候選，先檢查目錄與 symlink。']),
    'RETRY_UNSAFE': ('原輸入、流程版本或副作用狀態不足以證明重試安全。', ['重新檢查計畫，由使用者明確提交新 Run。']),
    'PLAN_SIDE_EFFECT': ('流程在計畫階段嘗試執行會改動狀態的操作。', ['請維護者修正 Adapter.plan，只保留唯讀檢查。']),
    'PLAN_PROBE_FAILED': ('隔離計畫程序未能完成。', ['保留本次錯誤編號，檢查流程包與本機環境。']),
}


def explain_error(code, *, cause_status='unknown', evidence=None):
    canonical = code if code in STANDARD else ALIASES.get(code, 'UNKNOWN')
    category, severity, title, message, actions, retry, requirements = STANDARD[canonical]
    if code in SPECIFIC:
        message, actions = SPECIFIC[code]
    return {'code': code, 'canonical_code': canonical, 'known': canonical != 'UNKNOWN',
            'category': category, 'severity': severity, 'user_title': title,
            'user_message': message, 'suggested_actions': list(actions),
            'retry_policy': retry, 'help_key': canonical.lower().replace('_', '-'),
            'evidence_requirements': list(requirements),
            'cause_status': cause_status if cause_status in {'confirmed','suspected','unknown'} else 'unknown',
            'evidence': evidence if isinstance(evidence, list) else []}


def explain_exception(exc, diagnostic=None):
    from ..runtime import FlowError
    original_code = exc.code if isinstance(exc, FlowError) else None
    if isinstance(exc, FlowError):
        code = exc.code
        if code == 'PREFLIGHT_BLOCKED':
            blockers = exc.details.get('blockers', [])
            if isinstance(blockers, list) and len(blockers) == 1 and isinstance(blockers[0], dict):
                if blockers[0].get('code') == 'DISK_SPACE':
                    code = 'DISK_SPACE_INSUFFICIENT'
    elif isinstance(exc, ValidationError):
        code = 'INVALID_CONFIGURATION'
    elif isinstance(exc, FileNotFoundError):
        code = 'INPUT_NOT_FOUND'
    elif isinstance(exc, PermissionError):
        code = 'PERMISSION_DENIED'
    elif isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        code = 'DISK_SPACE_INSUFFICIENT'
    elif isinstance(exc, TimeoutError):
        code = 'WORKER_TIMEOUT'
    elif isinstance(exc, subprocess.TimeoutExpired):
        code = 'WORKER_TIMEOUT'
    elif isinstance(exc, subprocess.CalledProcessError):
        code = 'WORKER_CRASHED'
    else:
        code = 'UNKNOWN'
    diagnostic = diagnostic or {}
    explanation = explain_error(code, cause_status=diagnostic.get('cause_status', 'unknown'),
                                evidence=diagnostic.get('evidence'))
    if original_code == 'PREFLIGHT_BLOCKED':
        explanation['code'] = original_code
    return explanation
