"""세션(이용자 1회 방문) 작업 공간 관리.

- 업로드한 센터 CSV, 중간 결과, 보고서는 모두 `workspace/<세션ID>/` 아래에만 저장된다.
- '퇴실 정리'(cleanup)는 이 폴더를 통째로 삭제한다(센터 퇴실 시 삭제 정책 준수).
- 반출 승인을 받은 결과물만 이용자가 명시적으로 `approved_outputs/` 로 옮길 수 있다.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import KEEP_DIR, WORK_DIR


@dataclass
class Session:
    sid: str
    root: Path
    created: float = field(default_factory=time.time)
    state: dict[str, Any] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def upload_dir(self) -> Path:
        p = self.root / "uploads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def out_dir(self) -> Path:
        p = self.root / "outputs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def chart_dir(self) -> Path:
        p = self.out_dir / "charts"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def audit_path(self) -> Path:
        return self.root / "llm_audit.jsonl"

    def audit(self, record: dict[str, Any]) -> None:
        record = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **record}
        with self.lock, self.audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read_audit(self) -> list[dict[str, Any]]:
        if not self.audit_path.exists():
            return []
        out = []
        for line in self.audit_path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


class SessionStore:
    def __init__(self, base: Path = WORK_DIR) -> None:
        self.base = base
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self) -> Session:
        sid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        s = Session(sid=sid, root=self.base / sid)
        s.root.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._sessions[sid] = s
        return s

    def get(self, sid: str) -> Session:
        with self._lock:
            s = self._sessions.get(sid)
        if s is None:
            root = self.base / sid
            if not root.exists() or not _safe_sid(sid):
                raise KeyError(sid)
            s = Session(sid=sid, root=root)
            with self._lock:
                self._sessions[sid] = s
        return s

    def cleanup(self, sid: str) -> dict[str, Any]:
        """세션 폴더 삭제(퇴실 정리)."""
        if not _safe_sid(sid):
            raise KeyError(sid)
        root = self.base / sid
        n_files = sum(1 for p in root.rglob("*") if p.is_file()) if root.exists() else 0
        shutil.rmtree(root, ignore_errors=True)
        with self._lock:
            self._sessions.pop(sid, None)
        return {"deleted_session": sid, "deleted_files": n_files}

    def cleanup_all(self) -> dict[str, Any]:
        """모든 세션 작업 공간 삭제(하루 운영 종료 시 담당자용)."""
        n = 0
        for d in self.base.iterdir() if self.base.exists() else []:
            if d.is_dir() and _safe_sid(d.name):
                shutil.rmtree(d, ignore_errors=True)
                n += 1
        with self._lock:
            self._sessions.clear()
        return {"deleted_sessions": n}

    def keep(self, sid: str, filename: str) -> Path:
        """반출 승인된 결과물을 보존 폴더로 복사."""
        s = self.get(sid)
        src = (s.out_dir / filename).resolve()
        if s.out_dir.resolve() not in src.parents or not src.exists():
            raise FileNotFoundError(filename)
        dst_dir = KEEP_DIR / sid
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / src.name
        shutil.copy2(src, dst)
        return dst


def _safe_sid(sid: str) -> bool:
    return bool(sid) and all(c.isalnum() or c == "-" for c in sid) and len(sid) < 64


store = SessionStore()
