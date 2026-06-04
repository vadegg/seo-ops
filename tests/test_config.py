import pytest

from config import Config, ConfigError

_REQUIRED = ["ANTHROPIC_API_KEY", "GSC_SERVICE_ACCOUNT_JSON", "GSC_SITE_URL",
             "DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD", "TELEGRAM_BOT_TOKEN",
             "TELEGRAM_CHAT_ID", "BLOG_REPO_URL", "GIT_DEPLOY_KEY",
             "EVIDENCE_DIR"]


def test_missing_secrets_fails_listing_all(monkeypatch):
    for k in _REQUIRED:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("config._load_dotenv", lambda p: None)
    with pytest.raises(ConfigError) as e:
        Config.load(strict_paths=False)
    msg = str(e.value)
    # Every missing var reported in one shot, not just the first.
    for k in _REQUIRED:
        assert k in msg


def test_valid_config_loads(monkeypatch, tmp_path):
    key = tmp_path / "k"
    key.write_text("x")
    env = {
        "ANTHROPIC_API_KEY": "a", "GSC_SERVICE_ACCOUNT_JSON": str(key),
        "GSC_SITE_URL": "sc-domain:x", "DATAFORSEO_LOGIN": "l",
        "DATAFORSEO_PASSWORD": "p", "TELEGRAM_BOT_TOKEN": "t",
        "TELEGRAM_CHAT_ID": "c", "BLOG_REPO_URL": "g", "GIT_DEPLOY_KEY": str(key),
        "EVIDENCE_DIR": str(tmp_path), "MAX_STAGE": "3"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("config._load_dotenv", lambda p: None)
    cfg = Config.load(strict_paths=True)
    assert cfg.max_stage == 3
    assert cfg.runs_dir.name == "runs"
