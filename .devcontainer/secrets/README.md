# secrets/ — camada sensível (gitignored)

Só os arquivos `*.example` são versionados. O arquivo real `secrets.env`
**nunca entra no git** (regra `.gitignore`: `.devcontainer/secrets/*.env`).

## Bootstrap (obrigatório antes do `docker compose up`)

```bash
cp .devcontainer/secrets/secrets.env.example .devcontainer/secrets/secrets.env
```

Sem esse passo o `env_file` do serviço `worker` quebra ("env file not found").

## Chaves

| Variável | Fonte | Uso |
|---|---|---|
| `YOUTUBE_API_KEY` | Google Cloud Console (Data API v3) | coletor YouTube |
| `INSTAGRAM_ACCESS_TOKEN` | Meta for Developers (Graph API) | coletor Instagram |
| `IG_USER_ID` | Graph API (conta de destino) | coletor Instagram |
| `TIKTOK_MS_TOKEN` | cookie `ms_token` do TikTok | TikTok-Api v6 (ADR-0003) |

## Regras

- Nenhum valor real em arquivo rastreado — só `=` vazio no `.example`.
- Quem vazar um token: revogar na fonte e regerar; o histórico do git não
  é a única via de vazamento (`.env` local também conta).
- O serviço `python` (devcontainer) **não** recebe `secrets.env`; só o
  `worker` (coleta), conforme o plano.
