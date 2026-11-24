# Studio 對話版安裝包

解壓到 RoutineKeel 根目錄，保留 workflow_studio/。先在宿主對話讀 AGENTS.md 及 docs/host-conversation.md。
需要檢查安裝時執行 studio.bat --prepare-only 或 sh studio.sh --prepare-only；之後使用 guide、status、projects。
環境檢查只驗證核心，不要求 Tk、不開視窗。整合安裝時，底座和 Studio 共用 RoutineKeel 根層的 `.venv`，由底座啟動器準備。請使用同一次發行的底座與 Studio ZIP。單獨使用 Studio 時，隨附底座在 Studio 目錄準備唯一的 `.venv`。首次準備缺少 Python 或套件時仍需要網路。舊 `.studio-venv` 可保留供降版。
既有專案與來源須整組備份；安裝包不包含私人資料。
