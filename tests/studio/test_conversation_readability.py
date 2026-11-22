import pytest
from workflow_studio.conversation.session import StudioSession

pytest.importorskip("PIL")


def test_settings_failure_does_not_change_active_profile(tmp_path):
    from workflow_studio.contracts import StudioError

    model = StudioSession(tmp_path)
    model.create_project("設定驗證", "保留原設定", "失敗不變更")
    model.configure_provider("https://first.invalid/chat", "first", 1, 1, 1)
    original = model.provider
    with pytest.raises(StudioError):
        model.configure_provider(
            "https://second.invalid/chat",
            "second",
            1,
            1,
            1,
            runtime_enabled=True,
            runtime_budget_usd=0,
        )
    assert model.provider is original
    assert model.provider.model == "first"
