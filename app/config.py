"""오름(OREUM) 설정.

모든 비밀값(API 키)은 환경변수 또는 프로젝트 루트의 `.env` 파일에서만 읽는다.
제출물·코드에는 어떤 키도 포함하지 않는다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # oreum/
APP_DIR = ROOT / "app"
WEB_DIR = ROOT / "web"
DATA_DIR = ROOT / "data"
PUBLIC_DIR = DATA_DIR / "public"
DEMO_DIR = DATA_DIR / "demo_center"
CACHE_DIR = DATA_DIR / "cache"
WORK_DIR = ROOT / "workspace"          # 세션별 작업 공간 (퇴실 시 삭제 대상)
KEEP_DIR = ROOT / "approved_outputs"   # 반출 승인 결과 보존 폴더(센터 운영 정책상 보존 대상)


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except Exception:  # python-dotenv 미설치 등: 간단 파서로 대체
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass
class Settings:
    # LLM — 기본은 OpenAI(gpt-6-luna, 추론 강도 high). 센터가 발급한 키를 .env 의 OPENAI_API_KEY 로 설정.
    #   OREUM_LLM_PROVIDER: openai(기본) | anthropic | codex(개발·검증용: 로컬 Codex CLI 로그인으로 같은 모델 실행)
    llm_enabled: bool = field(default_factory=lambda: _bool("OREUM_LLM_ENABLED", True))
    llm_provider_env: str = field(default_factory=lambda: os.environ.get("OREUM_LLM_PROVIDER", "").strip().lower())
    llm_model_env: str = field(default_factory=lambda: os.environ.get("OREUM_LLM_MODEL", "").strip())
    llm_effort_plan: str = field(default_factory=lambda: os.environ.get("OREUM_LLM_EFFORT_PLAN", "high"))
    llm_effort_write: str = field(default_factory=lambda: os.environ.get("OREUM_LLM_EFFORT_WRITE", "high"))
    codex_home: str = field(default_factory=lambda: os.environ.get("OREUM_CODEX_HOME", os.environ.get("CODEX_HOME", "")).strip())
    llm_fallback: bool = field(default_factory=lambda: _bool("OREUM_LLM_FALLBACK", True))
    llm_timeout_sec: float = field(default_factory=lambda: float(os.environ.get("OREUM_LLM_TIMEOUT", "120")))
    # 제주데이터허브 Open API 프로젝트 키(선택)
    hub_project_key: str = field(default_factory=lambda: os.environ.get("JEJU_HUB_PROJECT_KEY", "").strip())
    # 서버
    host: str = field(default_factory=lambda: os.environ.get("OREUM_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("OREUM_PORT", "8765")))
    # 반출 기준
    min_cell: int = field(default_factory=lambda: int(os.environ.get("OREUM_MIN_CELL", "10")))
    # 보고서 PDF 변환에 쓸 브라우저(미지정 시 Edge→Chrome 자동 탐색)
    browser_path: str = field(default_factory=lambda: os.environ.get("OREUM_BROWSER", ""))

    @property
    def llm_provider(self) -> str:
        if self.llm_provider_env in ("openai", "anthropic", "codex"):
            return self.llm_provider_env
        if os.environ.get("OPENAI_API_KEY"):
            return "openai"
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return "anthropic"
        return "openai"

    @property
    def llm_model(self) -> str:
        return self.llm_model_env or DEFAULT_MODELS[self.llm_provider]

    @property
    def codex_exe(self) -> str:
        import shutil

        return shutil.which("codex") or ""

    @property
    def has_llm_key(self) -> bool:
        p = self.llm_provider
        if p == "openai":
            return bool(os.environ.get("OPENAI_API_KEY"))
        if p == "anthropic":
            return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        home = Path(self.codex_home) if self.codex_home else Path.home() / ".codex"
        return bool(self.codex_exe) and (home / "auth.json").exists()

    @property
    def llm_active(self) -> bool:
        return self.llm_enabled and self.has_llm_key


# 모델별 가격(USD / 1M tokens) — 비용 추정 표시용. 2026-10 기준 공개 가격.
DEFAULT_MODELS = {"openai": "gpt-6-luna", "codex": "gpt-6-luna", "anthropic": "claude-opus-5-5"}

MODEL_PRICES = {
    "gpt-6-luna": (0.10, 0.50),
    "gpt-6.1-terra": (1.00, 5.00),
    "gpt-6.1-sol": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.00, 50.00),
}

settings = Settings()

for _d in (WORK_DIR, KEEP_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)
