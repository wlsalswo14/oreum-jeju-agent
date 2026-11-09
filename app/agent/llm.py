"""LLM 호출 래퍼 — 제공자(OpenAI · Codex CLI · Anthropic)를 바꿔도 같은 프롬프트·같은 JSON 스키마로 동작한다.

- openai   : 기본(센터 운영). OpenAI Responses API, 모델 gpt-6-luna, 추론 강도 high. 키는 .env 의 OPENAI_API_KEY.
- codex    : 개발·검증용. 로컬 Codex CLI(ChatGPT 로그인)로 **같은 모델·같은 추론 강도·같은 프롬프트·같은 스키마**를 실행.
             센터 운영에는 쓰지 않는다(개인 계정 로그인 필요).
- anthropic: 선택. Claude(Messages API). 키는 ANTHROPIC_API_KEY.

모든 호출은 세션 감사 로그(llm_audit.jsonl)에 '보낸 내용·받은 내용·토큰·추정 비용'을 남긴다
→ 센터 담당자가 "AI에게 무엇을 보냈는가"를 화면 「AI 전송 기록」에서 확인할 수 있다.
API 키는 환경변수(.env)에서만 읽고, 로그·화면·예외 메시지에 남기지 않는다.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import MODEL_PRICES, settings

FALLBACK_BETA = "server-side-fallback-2026-07-01"   # Anthropic 거절 시 서버측 대체 모델


class LLMUnavailable(RuntimeError):
    pass


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    log: list[dict] = field(default_factory=list)

    def add(self, model: str, inp: int, out: int, purpose: str):
        pin, pout = MODEL_PRICES.get(model, (0.10, 0.50))
        cost = inp / 1e6 * pin + out / 1e6 * pout
        self.calls += 1
        self.input_tokens += inp
        self.output_tokens += out
        self.cost_usd += cost
        self.log.append({"purpose": purpose, "model": model, "in": inp, "out": out, "usd": round(cost, 5)})
        return cost


@dataclass
class Reply:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    stop: str = "end"          # end | max_tokens | refusal


class LLM:
    def __init__(self, session=None):
        self.session = session
        self.provider = settings.llm_provider
        self.model = settings.llm_model
        self.usage = Usage()
        self._client = None
        self._fallback_ok = settings.llm_fallback

    @property
    def available(self) -> bool:
        return settings.llm_active

    # ================================================================== 제공자별 호출
    def _get_client(self):
        if not self.available:
            raise LLMUnavailable("LLM 비활성 또는 API 키 미설정")
        if self._client is None:
            if self.provider == "openai":
                from openai import OpenAI

                self._client = OpenAI(timeout=settings.llm_timeout_sec, max_retries=2)
            elif self.provider == "anthropic":
                import anthropic

                self._client = anthropic.Anthropic(timeout=settings.llm_timeout_sec, max_retries=2)
        return self._client

    def _complete(self, system: str, user: str, schema: dict | None, effort: str, max_tokens: int) -> Reply:
        if self.provider == "openai":
            return self._openai(system, user, schema, effort, max_tokens)
        if self.provider == "codex":
            return self._codex(system, user, schema, effort)
        return self._anthropic(system, user, schema, effort, max_tokens)

    # ---------------------------------------------------------------- OpenAI(Responses API)
    def _openai(self, system, user, schema, effort, max_tokens) -> Reply:
        import openai

        client = self._get_client()
        kw: dict[str, Any] = dict(model=self.model, instructions=system, input=user,
                                  reasoning={"effort": effort}, max_output_tokens=max(max_tokens, 16000))
        if schema is not None:
            kw["text"] = {"format": {"type": "json_schema", "name": "oreum_output", "schema": schema, "strict": True}}
        try:
            r = client.responses.create(**kw)
        except openai.AuthenticationError as e:
            raise LLMUnavailable("API 키 인증 실패") from e
        except openai.RateLimitError as e:
            raise LLMUnavailable("요청 한도 초과(잠시 후 재시도)") from e
        except openai.APIConnectionError as e:
            raise LLMUnavailable("네트워크 연결 실패(센터 보안정책·방화벽 확인)") from e
        except openai.APIStatusError as e:
            msg = str(getattr(e, "message", "") or "")[:200]
            raise LLMUnavailable(f"API 오류({e.status_code}){': ' + msg if msg else ''}") from e
        text = getattr(r, "output_text", None)
        refused = False
        if text is None or text == "":
            parts = []
            for item in getattr(r, "output", []) or []:
                for c in getattr(item, "content", None) or []:
                    if getattr(c, "type", "") == "refusal":
                        refused = True
                    elif getattr(c, "type", "") == "output_text":
                        parts.append(getattr(c, "text", ""))
            text = "".join(parts)
        stop = "end"
        if refused:
            stop = "refusal"
        elif getattr(r, "status", "") == "incomplete":
            stop = "max_tokens"
        u = getattr(r, "usage", None)
        return Reply(text or "", getattr(r, "model", None) or self.model, getattr(u, "input_tokens", 0) or 0,
                     getattr(u, "output_tokens", 0) or 0, stop)

    # ---------------------------------------------------------------- Codex CLI(개발·검증용)
    def _codex(self, system, user, schema, effort) -> Reply:
        exe = settings.codex_exe
        if not exe:
            raise LLMUnavailable("Codex CLI를 찾을 수 없음")
        prompt = (
            "다음 [지시]를 시스템 지시로 따르고 [입력]에 대한 답만 출력하세요. 파일을 읽거나 명령을 실행하지 마세요.\n\n"
            f"[지시]\n{system}\n\n[입력]\n{user}\n"
        )
        env = dict(os.environ)
        if settings.codex_home:
            env["CODEX_HOME"] = settings.codex_home
        with tempfile.TemporaryDirectory(prefix="oreum_codex_") as td:
            out = Path(td) / "last.txt"
            args = [exe, "exec", "-m", self.model, "-c", f"model_reasoning_effort={effort}", "-s", "read-only",
                    "--skip-git-repo-check", "--ephemeral", "--ignore-rules", "-C", td, "--json", "-o", str(out)]
            if schema is not None:
                sp = Path(td) / "schema.json"
                sp.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
                args += ["--output-schema", str(sp)]
            args.append("-")
            try:
                p = subprocess.run(args, input=prompt.encode("utf-8"), capture_output=True, env=env,
                                   timeout=max(settings.llm_timeout_sec, 600),
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired as e:
                raise LLMUnavailable("Codex 응답 시간 초과") from e
            inp = outp = 0
            err = ""
            for ln in p.stdout.decode("utf-8", "replace").splitlines():
                try:
                    ev = json.loads(ln)
                except ValueError:
                    continue
                if ev.get("type") == "turn.completed":
                    u = ev.get("usage") or {}
                    inp, outp = int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0)
                elif ev.get("type") in ("error", "turn.failed"):
                    err = json.dumps(ev, ensure_ascii=False)[:300]
            text = out.read_text(encoding="utf-8").strip() if out.exists() else ""
            if p.returncode != 0 or not text:
                tail = p.stderr.decode("utf-8", "replace")[-300:]
                raise LLMUnavailable(f"Codex 실행 실패({p.returncode}): {err or tail}")
        return Reply(text, self.model, inp, outp, "end")

    # ---------------------------------------------------------------- Anthropic(선택)
    def _anthropic(self, system, user, schema, effort, max_tokens) -> Reply:
        import anthropic

        client = self._get_client()
        oc: dict[str, Any] = {"effort": effort}
        if schema is not None:
            oc["format"] = {"type": "json_schema", "schema": schema}
        kw: dict[str, Any] = dict(model=self.model, max_tokens=max_tokens, system=system,
                                  messages=[{"role": "user", "content": user}], output_config=oc)
        try:
            resp = None
            if self._fallback_ok:
                try:
                    resp = client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kw)
                except anthropic.BadRequestError as e:
                    if "fallback" in str(e).lower() or "beta" in str(e).lower():
                        self._fallback_ok = False   # 대체 모델 옵션 미지원 → 끄고 재시도
                    else:
                        raise
            if resp is None:
                resp = client.messages.create(**kw)
        except anthropic.AuthenticationError as e:
            raise LLMUnavailable("API 키 인증 실패") from e
        except anthropic.RateLimitError as e:
            raise LLMUnavailable("요청 한도 초과(잠시 후 재시도)") from e
        except anthropic.APIConnectionError as e:
            raise LLMUnavailable("네트워크 연결 실패(센터 보안정책·방화벽 확인)") from e
        except anthropic.APIStatusError as e:
            raise LLMUnavailable(f"API 오류({e.status_code})") from e
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        stop = {"refusal": "refusal", "max_tokens": "max_tokens"}.get(resp.stop_reason, "end")
        u = resp.usage
        return Reply(text, resp.model or self.model, u.input_tokens or 0, u.output_tokens or 0, stop)

    # ================================================================== 공통(감사 로그·비용)
    def _call(self, purpose: str, system: str, user: str, schema: dict | None, effort: str, max_tokens: int) -> Reply:
        t0 = time.time()
        rep, err = None, None
        try:
            rep = self._complete(system, user, schema, effort, max_tokens)
            return rep
        except LLMUnavailable as e:
            err = str(e)
            raise
        finally:
            cost = 0.0
            if rep is not None:
                cost = self.usage.add(rep.model, rep.input_tokens, rep.output_tokens, purpose)
            if self.session is not None:
                self.session.audit({
                    "purpose": purpose, "provider": self.provider, "model": self.model, "effort": effort,
                    "sent_system": system, "sent_user": user,
                    "received": (rep.text if rep else "")[:20000], "error": err,
                    "stop_reason": rep.stop if rep else None,
                    "input_tokens": rep.input_tokens if rep else None, "output_tokens": rep.output_tokens if rep else None,
                    "usd": round(cost, 5), "seconds": round(time.time() - t0, 1),
                })

    def json(self, purpose: str, system: str, user: str, schema: dict, effort: str | None = None,
             max_tokens: int = 4000) -> dict:
        rep = self._call(purpose, system, user, schema, effort or settings.llm_effort_plan, max_tokens)
        if rep.stop == "refusal":
            raise LLMUnavailable("모델이 응답을 거절함")
        if rep.stop == "max_tokens":
            raise LLMUnavailable("응답이 길이 제한에 걸림")
        try:
            return json.loads(rep.text)
        except ValueError as e:
            raise LLMUnavailable("AI 응답이 JSON 형식이 아님") from e

    def text(self, purpose: str, system: str, user: str, effort: str | None = None, max_tokens: int = 8000) -> str:
        rep = self._call(purpose, system, user, None, effort or settings.llm_effort_write, max_tokens)
        if rep.stop == "refusal":
            raise LLMUnavailable("모델이 응답을 거절함")
        return rep.text

    def usage_summary(self) -> dict:
        u = self.usage
        return {"calls": u.calls, "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                "cost_usd": round(u.cost_usd, 4), "cost_krw": int(round(u.cost_usd * 1400)), "model": self.model,
                "provider": self.provider, "log": u.log}
