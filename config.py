"""Configuration loading + hard validation.

All secrets are validated at process start: a missing key fails early,
before any agent runs, listing *every* missing variable at once.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no external dependency).

    Existing real environment variables always win over the file.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


# Required secret/identity variables. (name -> human description)
_REQUIRED = {
    "ANTHROPIC_API_KEY": "Anthropic API key",
    "GSC_SERVICE_ACCOUNT_JSON": "path to GSC service-account JSON",
    "GSC_SITE_URL": "Search Console property URL",
    "DATAFORSEO_LOGIN": "DataForSEO login",
    "DATAFORSEO_PASSWORD": "DataForSEO password",
    "TELEGRAM_BOT_TOKEN": "Telegram bot token",
    "TELEGRAM_CHAT_ID": "Telegram chat/channel id",
    "BLOG_REPO_URL": "blog git repo URL",
    "GIT_DEPLOY_KEY": "path to git deploy SSH key",
    "EVIDENCE_DIR": "evidence corpus directory",
}

# Variables that must point to an existing file/dir on disk.
_PATH_VARS = {
    "GSC_SERVICE_ACCOUNT_JSON": "file",
    "GIT_DEPLOY_KEY": "file",
    "EVIDENCE_DIR": "dir",
}


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str
    gsc_service_account_json: Path
    gsc_site_url: str
    dataforseo_login: str
    dataforseo_password: str
    telegram_bot_token: str
    telegram_chat_id: str
    blog_repo_url: str
    git_deploy_key: Path
    blog_branch: str
    blog_posts_dir: str
    blog_base_url: str
    evidence_dir: Path

    # tunables
    max_stage: int = 4
    agent_max_tokens: int = 8000
    model_sonnet: str = "claude-sonnet-4-6"
    model_opus: str = "claude-opus-4-7"
    timezone: str = "Europe/Lisbon"
    site_name: str = "Glasgow Research"
    author_name: str = "Vadim Glazkov"
    author_slug: str = "vadim"
    default_category: str = "Research"
    backlog_score_floor: float = 0.4
    backlog_max_size: int = 50

    # Conversion footer (#11) — appended to every post body.
    cta_text: str = ("Glasgow Research helps B2B SaaS teams turn customer "
                     "and market research into product decisions.")
    cta_url: str = "https://blog.glasgow.works/services/"

    # FTC paid-tool disclosure (#16).
    tool_disclosure: str = ("Disclosure: this article may mention paid tools. "
                            "We receive no compensation for any mention; "
                            "recommendations are based on hands-on use.")

    # Author / org identity for richer JSON-LD (#12). Real, confirmed profile
    # URLs (never fabricated); CSV env vars override the sameAs tuples.
    author_url: str = "https://blog.glasgow.works/authors/vadim/"
    author_same_as: tuple = ("https://www.linkedin.com/in/vadim-glazkov/",)
    org_same_as: tuple = ("https://www.linkedin.com/company/glasgow-research",)
    default_og_image: str = ""

    # Internal-link floor for a sufficiently-built corpus (#13).
    internal_link_floor: int = 3
    internal_link_min_corpus: int = 4

    # Uniqueness / near-duplicate guard (#37). Internal MinHash check needs no
    # external service; the provider/key are reserved for an optional future
    # external plagiarism API and default to internal-only.
    uniqueness_threshold: float = 0.55
    uniqueness_provider: str = "internal"   # "internal" = local MinHash only
    uniqueness_api_key: str = ""

    # Where llms.txt is written in the blog repo (Astro serves public/ at root).
    blog_llms_path: str = "public/llms.txt"

    # Per-million-token USD prices for cost accounting (#5):
    # (model_id, input_price_per_mtok, output_price_per_mtok).
    model_prices: tuple = (
        ("claude-opus-4-7", 15.0, 75.0),
        ("claude-opus-4-8", 15.0, 75.0),
        ("claude-sonnet-4-6", 3.0, 15.0),
        ("claude-haiku-4-5-20251001", 1.0, 5.0),
    )

    # IndexNow (post-publish indexation ping). Empty key disables it.
    indexnow_key: str = ""
    indexnow_endpoint: str = "https://api.indexnow.org/indexnow"
    indexnow_site_url: str = ""  # origin; defaults to BLOG_BASE_URL origin

    # paths (derived)
    project_root: Path = field(default=PROJECT_ROOT)

    @property
    def runs_dir(self) -> Path:
        return self.project_root / "runs"

    @property
    def backlog_dir(self) -> Path:
        return self.project_root / "backlog"

    @property
    def themes_dir(self) -> Path:
        return self.project_root / "themes"

    @property
    def style_guide_path(self) -> Path:
        return self.project_root / "style_guide.md"

    @staticmethod
    def load(strict_paths: bool = True) -> "Config":
        """Load + validate. Raises ConfigError listing *all* problems.

        strict_paths=False skips on-disk file/dir existence checks
        (used by unit tests that only provide env values).
        """
        _load_dotenv(PROJECT_ROOT / ".env")

        problems: list[str] = []
        for name, desc in _REQUIRED.items():
            if not os.environ.get(name, "").strip():
                problems.append(f"missing {name} ({desc})")

        if strict_paths:
            for name, kind in _PATH_VARS.items():
                val = os.environ.get(name, "").strip()
                if not val:
                    continue  # already reported as missing above
                p = Path(val)
                ok = p.is_file() if kind == "file" else p.is_dir()
                if not ok:
                    problems.append(f"{name}={val} is not an existing {kind}")

        if problems:
            raise ConfigError(
                "Configuration invalid — fix .env before running:\n  - "
                + "\n  - ".join(problems)
            )

        def _int(name: str, default: int) -> int:
            try:
                return int(os.environ.get(name, "").strip() or default)
            except ValueError:
                return default

        def _float(name: str, default: float) -> float:
            try:
                return float(os.environ.get(name, "").strip() or default)
            except ValueError:
                return default

        def _csv(name: str) -> tuple:
            raw = os.environ.get(name, "").strip()
            return tuple(p.strip() for p in raw.split(",") if p.strip())

        blog_base_url = os.environ.get(
            "BLOG_BASE_URL", "https://blog.glasgow.works/blog"
        ).strip().rstrip("/")

        from urllib.parse import urlsplit
        _origin = urlsplit(blog_base_url)
        _default_site = (f"{_origin.scheme}://{_origin.netloc}"
                         if _origin.scheme and _origin.netloc else blog_base_url)
        indexnow_site_url = os.environ.get(
            "INDEXNOW_SITE_URL", _default_site).strip().rstrip("/")

        return Config(
            anthropic_api_key=os.environ["ANTHROPIC_API_KEY"].strip(),
            gsc_service_account_json=Path(os.environ["GSC_SERVICE_ACCOUNT_JSON"].strip()),
            gsc_site_url=os.environ["GSC_SITE_URL"].strip(),
            dataforseo_login=os.environ["DATAFORSEO_LOGIN"].strip(),
            dataforseo_password=os.environ["DATAFORSEO_PASSWORD"].strip(),
            telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"].strip(),
            telegram_chat_id=os.environ["TELEGRAM_CHAT_ID"].strip(),
            blog_repo_url=os.environ["BLOG_REPO_URL"].strip(),
            git_deploy_key=Path(os.environ["GIT_DEPLOY_KEY"].strip()),
            blog_branch=os.environ.get("BLOG_BRANCH", "main").strip(),
            blog_posts_dir=os.environ.get("BLOG_POSTS_DIR", "src/content/blog").strip(),
            blog_base_url=blog_base_url,
            evidence_dir=Path(os.environ["EVIDENCE_DIR"].strip()),
            max_stage=_int("MAX_STAGE", 4),
            agent_max_tokens=_int("AGENT_MAX_TOKENS", 8000),
            model_sonnet=os.environ.get("MODEL_SONNET", "claude-sonnet-4-6").strip(),
            model_opus=os.environ.get("MODEL_OPUS", "claude-opus-4-7").strip(),
            timezone=os.environ.get("TIMEZONE", "Europe/Lisbon").strip(),
            site_name=os.environ.get("SITE_NAME", "Glasgow Research").strip(),
            author_name=os.environ.get("AUTHOR_NAME", "Vadim Glazkov").strip(),
            author_slug=os.environ.get("AUTHOR_SLUG", "vadim").strip(),
            default_category=os.environ.get("DEFAULT_CATEGORY", "Research").strip(),
            backlog_score_floor=_float("BACKLOG_SCORE_FLOOR", 0.4),
            backlog_max_size=_int("BACKLOG_MAX_SIZE", 50),
            cta_text=os.environ.get(
                "CTA_TEXT",
                "Glasgow Research helps B2B SaaS teams turn customer and "
                "market research into product decisions.").strip(),
            cta_url=os.environ.get("CTA_URL", "https://glasgow.works").strip(),
            tool_disclosure=os.environ.get(
                "TOOL_DISCLOSURE",
                "Disclosure: this article may mention paid tools. We receive "
                "no compensation for any mention; recommendations are based "
                "on hands-on use.").strip(),
            author_url=os.environ.get(
                "AUTHOR_URL", "https://blog.glasgow.works/authors/vadim/").strip(),
            author_same_as=_csv("AUTHOR_SAME_AS")
            or ("https://www.linkedin.com/in/vadim-glazkov/",),
            org_same_as=_csv("ORG_SAME_AS")
            or ("https://www.linkedin.com/company/glasgow-research",),
            default_og_image=os.environ.get("DEFAULT_OG_IMAGE", "").strip(),
            uniqueness_threshold=_float("UNIQUENESS_THRESHOLD", 0.55),
            uniqueness_provider=os.environ.get(
                "UNIQUENESS_PROVIDER", "internal").strip() or "internal",
            uniqueness_api_key=os.environ.get("UNIQUENESS_API_KEY", "").strip(),
            blog_llms_path=os.environ.get(
                "BLOG_LLMS_PATH", "public/llms.txt").strip(),
            indexnow_key=os.environ.get(
                "INDEXNOW_KEY",
                "129ebf08-3db2-4d2f-bf33-9ea41ef4cc90").strip(),
            indexnow_endpoint=os.environ.get(
                "INDEXNOW_ENDPOINT",
                "https://api.indexnow.org/indexnow").strip(),
            indexnow_site_url=indexnow_site_url,
        )
