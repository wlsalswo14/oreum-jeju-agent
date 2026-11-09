"""제출용 ZIP 생성: 실행에 필요한 코드·문서·공개 데이터만 담고 비밀값·작업 파일은 제외한다.

사용: .venv\\Scripts\\python.exe tools\\package.py [출력경로.zip] [--with-demo]
- 시연·결과는 제주데이터허브 실데이터(data/public)로 만든다. 테스트용 형식 검증 CSV(data/demo_center)는 기본 제외
  (run_tests.bat 이 tools/make_demo_data.py 로 동일하게 재생성 — 고정 시드). --with-demo: 포함
- .env 의 키 값(ANTHROPIC_API_KEY·JEJU_HUB_PROJECT_KEY 등)이 어떤 파일에든 들어 있으면 중단
"""
from __future__ import annotations

import fnmatch
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXCLUDE_DIRS = {".venv", "workspace", "approved_outputs", "__pycache__", ".pytest_cache", ".git", ".idea", ".vscode"}
EXCLUDE_FILES = ["*.pyc", ".env", "*.log", "*.tmp", "Thumbs.db", ".DS_Store"]
EXCLUDE_PATHS = ["data/cache/*"]
# 대회 제공 '제주데이터허브 데이터 목록'은 제3자 배포 금지 → 혹시 있으면 차단
FORBIDDEN = [re.compile(r"데이터\s*목록.*\.xlsx$"), re.compile(r"datalist.*\.xlsx$", re.I)]
SECRET = re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}")


def collect(with_demo: bool) -> list[Path]:
    out = []
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT).as_posix()
        if any(part in EXCLUDE_DIRS for part in p.relative_to(ROOT).parts):
            continue
        if any(fnmatch.fnmatch(p.name, pat) for pat in EXCLUDE_FILES):
            continue
        if any(fnmatch.fnmatch(rel, pat) for pat in EXCLUDE_PATHS):
            continue
        if not with_demo and rel.startswith("data/demo_center/") and p.suffix == ".csv":
            continue
        if any(rx.search(p.name) for rx in FORBIDDEN):
            raise SystemExit(f"[중단] 배포 금지 파일 발견: {rel}")
        out.append(p)
    return out


def _env_secrets() -> list[str]:
    """.env 에 실제로 들어 있는 키 값들(빈 값 제외)."""
    vals = []
    env = ROOT / ".env"
    if env.exists():
        for ln in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            k, _, v = ln.partition("=")
            if k.strip().endswith("_KEY") and len(v.strip()) >= 8:
                vals.append(v.strip())
    return vals


def scan_secrets(files: list[Path]) -> None:
    keys = [k.encode() for k in _env_secrets()]
    for p in files:
        raw = p.read_bytes()
        if any(k in raw for k in keys):
            raise SystemExit(f"[중단] .env 의 키 값이 들어 있는 파일: {p.relative_to(ROOT)}")
        if p.suffix.lower() in {".py", ".md", ".txt", ".json", ".html", ".js", ".css", ".bat", ".example", ".toml", ".yml"}:
            if SECRET.search(raw.decode("utf-8", errors="ignore")):
                raise SystemExit(f"[중단] API 키로 보이는 문자열 발견: {p.relative_to(ROOT)}")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    with_demo = "--with-demo" in sys.argv
    out = Path(args[0]) if args else ROOT.parent / "제출" / "OREUM_최종결과물.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    files = collect(with_demo)
    scan_secrets(files)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in files:
            z.write(p, Path("OREUM") / p.relative_to(ROOT))
        for d in ("workspace", "approved_outputs", "data/demo_center"):
            z.writestr(f"OREUM/{d}/.keep", "")
    print(f"생성: {out}  ({out.stat().st_size / 1e6:.1f} MB, 파일 {len(files)}개)")


if __name__ == "__main__":
    main()
