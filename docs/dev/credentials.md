# Credenciais — onde ficam e como rotacionar

!!! danger "Regra de ouro"
    Nenhum valor real em arquivo rastreado. O teste de privacidade
    (`scripts/check_privacy.py`) falha o CI se segredo/path pessoal aparecer
    em arquivo versionado. Gitignorado ≠ revogado: se vazou, **rotacione na
    fonte** — o histórico do git não é a única via de vazamento.

## Onde ficam os segredos (spec §7)

| Camada | Arquivo | Versionado | Quem lê |
|---|---|---|---|
| Segredos do worker | `.devcontainer/secrets/secrets.env` | **não** (`*.example` sim) | só o serviço `worker` (`env_file`) |
| Config do app | `.devcontainer/env/app.env` | **não** (`*.example` sim) | serviço `python` (devcontainer) |
| Override local | `.env` (raiz) | **não** | `Settings` (pydantic-settings, 2 camadas) |

- Em `.devcontainer/secrets/`, só `*.env` é gitignored
  (`!.devcontainer/secrets/` + `.devcontainer/secrets/*.env`); os
  exemplos versionados (`secrets.env.example`, `app.env.example`)
  nascem **vazios**. Backstop: o gate `check_privacy` (CI + `make privacy`)
  falha se um segredo real aparecer em arquivo versionado.
- Segredos nunca entram em `ARG`/`ENV` de Dockerfile nem em logs
  (redação de `sessionid`, `ms_token`, `access_token`, cookies — spec §7).

## Chaves e escopo mínimo

| Variável | Fonte | Escopo mínimo |
|---|---|---|
| `YOUTUBE_API_KEY` | Google Cloud Console → Credentials | só **YouTube Data API v3** habilitada; API key é leitura por natureza — não habilite outras APIs |
| `INSTAGRAM_ACCESS_TOKEN` + `IG_USER_ID` | Meta for Developers (Graph API) | token com **leitura** de hashtag/mídia para os endpoints usados (`hashtag search`, `top_media`); sem permissão de publicação, app pode ficar em modo sandbox até validar |
| `TIKTOK_MS_TOKEN` | cookie `ms_token` do TikTok | cookie de sessão — ver conta secundária abaixo |
| `TIKTOK_PROXY` | opcional | proxy http(s) se a rede local for bloqueada |

## Conta secundária no TikTok (spec §7)

O TikTok-Api usa a API privada com risco conhecido de anti-bot
([ADR-0003](../adr/0003-coletores-fontes.md)). Por isso:

1. Crie/perfie uma **conta secundária** dedicada à coleta — nunca a conta
   principal.
2. Faça login nela no navegador, copie o cookie `ms_token` e coloque no
   `secrets.env` (gitignored).
3. `ms_token` expira: o collector responde `auth_expired` com mensagem
   clara (sem retry cego) — é o sinal para rotacionar (procedimento
   abaixo).

## Rotação — sintoma → ação

| Token | Sintoma de expirar/revogar | Rotacionar |
|---|---|---|
| `YOUTUBE_API_KEY` | 403 de cota/desabilitação, ou chave vazou | Cloud Console → Credentials → criar nova key → **revogar** a antiga → atualizar `secrets.env`/`app.env` → reiniciar `worker` |
| `INSTAGRAM_ACCESS_TOKEN` | 401/400 → `auth_expired` no collector | Meta for Developers → gerar token novo com escopo mínimo → **revogar** o anterior no painel do app → atualizar env → reiniciar `worker` |
| `TIKTOK_MS_TOKEN` | `auth_expired` (cookie expirou) | logar na **conta secundária**, copiar cookie `ms_token` novo → atualizar env → reiniciar `worker` |

Depois de rotacionar, rode um `cf collect` de baixo limite para confirmar
(`collect_runs.status`) e apague o valor antigo de qualquer lugar onde
tenha sido colado temporariamente.

## Verificação

```bash
uv run python scripts/check_privacy.py   # ok (roda também no CI)
```
