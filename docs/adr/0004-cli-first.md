# ADR-0004 — Interface: CLI-first com envelope JSON de status

**Situação:** Aceita · **Data:** 2026-10-07

## Contexto

O consumidor primário do sistema são agentes de AI e automações (n8n, CLI de
sessões). Eles precisam disparar coleta/scoring e confirmar resultado. Já
existiu a ideia de FastAPI desde o dia 1, mas não há painel nem consumo HTTP
no MVP.

## Decisão

Vamos usar **CLI (`cf`) como única interface**, com **envelope JSON de status**
(`{"ok": true, ...}` / `{"ok": false, "error": {...}}`) e exit codes
`0` sucesso / `2` falha parcial / `1` falha total. **Resultados ficam no
banco** — agentes leem o banco; a CLI não devolve dumps de dados.

## Alternativas consideradas

| Alternativa | Por que não |
|---|---|
| API-first (HTTP) | Exige stack de rede/auth no gate de aceitação sem consumidor real |
| CLI + FastAPI juntos | Duplica superfície de teste/docs; API entra quando houver painel/consulta externa (novo ADR) |
| CLI dumpando JSON de resultados | Volume alto, contrato instável; banco já é a fonte de verdade |

## Consequências

- **Fica mais fácil:** agentes integram com 1 comando + exit code; sem
  servidor para subir; testável sem rede.
- **Fica mais difícil:** consultas analíticas exigem acesso ao banco (ou
  comando de relatório futuro); sem API, consumo remoto direto não existe.

## Como saber que erramos

Um consumidor precisa de dados do sistema sem acesso ao banco (painel web,
consulta remota) → adicionar FastAPI de leitura (novo ADR).
