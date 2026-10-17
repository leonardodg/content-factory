"""C3 — contrato único de resultado dos casos de uso (CLI e worker).

Contrato (`docs/architecture/contrato-envelopes.md`):

- `ok: true, partial: false` — tudo certo (CLI exit 0);
- `ok: true, partial: true` — algo foi gravado e algo falhou; as contagens
  dizem o quê (CLI exit 2);
- `ok: false` — nada útil; `error: {code, message}` obrigatório (CLI exit 1).

A CLI acrescenta só `command`; o worker usa o mesmo dict por segmento e o
agrega com `job_result`. Mensagens nunca carregam dado bruto (R5).
"""

from collections.abc import Callable, Mapping
from typing import Any

from content_factory.app.collect import CollectOutcome
from content_factory.app.remediate import RemediationOutcome
from content_factory.app.research_pipeline import ResearchOutcome
from content_factory.app.score import ScoreOutcome

LOCK_BUSY = "ja_em_execucao"


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "partial": False, "error": {"code": code, "message": message}}


def _success(partial: bool, counts: Mapping[str, Any]) -> dict[str, Any]:
    return {"ok": True, "partial": partial, **counts}


def collect_result(out: CollectOutcome) -> dict[str, Any]:
    if LOCK_BUSY in out.counts:
        return _error(LOCK_BUSY, "coleta já em execução")
    if out.ok or out.partial:
        return _success(out.partial, out.counts)
    code = next((r.error_code for r in out.runs if r.error_code), "internal_error")
    failed = int(out.counts.get("failed", 0))
    return {**_error(code, f"coleta falhou em {failed} plataforma(s)"), **out.counts}


def remediation_result(out: RemediationOutcome) -> dict[str, int]:
    """Sub-envelope `remediation` da coleta (R7) — só contagens, chaves estáveis."""
    return {
        "targets": out.targets,
        "refreshed": out.refreshed,
        "metrics_written": out.metrics_written,
        "removed": out.removed,
        "failed_platforms": out.failed_platforms,
        "quota_used": out.quota_used,
    }


def score_result(out: ScoreOutcome) -> dict[str, Any]:
    counts = out.as_counts()
    if out.ok or out.partial:
        return _success(out.partial, counts)
    return {**_error("internal_error", "falha ao gravar scores"), **counts}


def research_result(out: ResearchOutcome) -> dict[str, Any]:
    if LOCK_BUSY in out.counts:
        return _error(LOCK_BUSY, "pesquisa já em execução")
    if out.ok or out.partial:
        return _success(out.partial, out.counts)
    return {**_error("research_failed", "nenhuma fonte nem rede social concluiu"), **out.counts}


def exception_result(exc: BaseException, redact: Callable[[str], str]) -> dict[str, Any]:
    """Exceção que escapou do caso de uso (lock, banco…): código + tipo, mensagem redigida."""
    out = _error("internal_error", redact(str(exc)))
    out["error"]["type"] = type(exc).__name__
    return out


def job_result(segments: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Agregado do job do worker: `ok` = todos ok; `partial` = algum gravou e algo falhou."""
    values = list(segments.values())
    ok = all(bool(v["ok"]) for v in values)
    partial = any(bool(v.get("partial")) for v in values) or (
        not ok and any(bool(v["ok"]) for v in values)
    )
    return {"ok": ok, "partial": partial, "segments": dict(segments)}
