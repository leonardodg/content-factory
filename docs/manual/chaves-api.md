# Chaves de API — YouTube, Instagram, TikTok

Este capítulo final do manual cobre como **criar as chaves** das três
plataformas com escopo mínimo, **onde colá-las** no ambiente e **como verificar**
que cada uma funciona. Regras de rotação, camadas de env e a tabela completa de
sintoma→ação ficam em
[Credenciais](../dev/credentials.md) — aqui só o essencial para destravar a
primeira coleta; o fonte da verdade é sempre o credentials.md.

CLI roda dentro do container: `docker exec cf-python uv run cf …` (envelope
JSON de uma linha, exit codes `0`/`2`/`1` — contrato em
[TRD CLI](../specs/trd-cli.md)). Índice do manual: [Manual de
operação](index.md).

!!! danger "Regra de ouro"
    Nenhum valor real em arquivo rastreado. As chaves vão apenas em
    `.devcontainer/secrets/secrets.env` (gitignored). O gate
    `scripts/check_privacy.py` falha o CI se um segredo real aparecer em
    arquivo versionado — rode-o antes de subir qualquer serviço (ver
    [Verificação final](#6-verificacao-final)).

## 1. YouTube — Data API v3 (`YOUTUBE_API_KEY`)

1. Acesse [console.cloud.google.com](https://console.cloud.google.com/) e
   **crie um projeto** novo (ou use um existente dedicado à coleta).
2. Em **APIs & Services → Library**, busque **YouTube Data API v3** e clique
   em **Enable**.
3. Em **APIs & Services → Credentials → Create credentials → API key**. Anote
   a chave (formato `AIza…`).
4. **Restrinja a chave** (escopo mínimo):
   - *Application restrictions*: IP ou app que vai usá-la, se aplicável;
   - *API restrictions*: **só** YouTube Data API v3 — não habilite outras APIs.
5. Copie o valor para `YOUTUBE_API_KEY` no
   [lugar certo do ambiente](#4-onde-colar) (camada `secrets.env`).
6. Verifique:

   ```bash
   docker exec cf-python uv run cf collect --segment candles --platform youtube --limit 1
   ```

   Esperado: exit `0` (ou `2` parcial se outras plataformas do lote falharem).
   `rate_limited` com 403 logo de cara = chave mal habilitada ou restrição
   errada (origem/IP), não cota de verdade.

**Cota:** 10.000 unidades/dia; cada `search` custa 100 U e cada `list` 1 U —
o limiter interno janela 100 descobertas/24 h para espelhar a cota (detalhe em
[Anti-ban](anti-ban.md) e [Fluxo de busca](fluxo-busca.md)). Rotação em caso de
vazamento ou 403 persistente: [Credenciais — rotação](../dev/credentials.md#rotacao-sintoma-acao).

## 2. Instagram — Graph API (`INSTAGRAM_ACCESS_TOKEN` + `IG_USER_ID`)

1. Em [developers.facebook.com](https://developers.facebook.com/), em **My
   Apps → Create App**, escolha o tipo **Business**.
2. No app criado, adicione o produto **Instagram Graph API**.
3. A conta do Instagram precisa ser **Business ou Creator** e estar **vinculada
   a uma Page** do Facebook (configuração no app do Instagram: Conta →
   Profissional/Configurações de negócios).
4. Obtenha os dois valores:
   - `IG_USER_ID`: o id da conta Business/Creator (Graph API Explorer ou
     painel da conta vinculada);
   - access token de **longa duração**: via System User token com permissão
     `instagram_basic` (produção) — ou token de teste de 24 h só para o
     primeiro `collect`.
5. Preencha `INSTAGRAM_ACCESS_TOKEN` e `IG_USER_ID` no
   [lugar certo do ambiente](#4-onde-colar).
6. Verifique:

   ```bash
   docker exec cf-python uv run cf collect --segment candles --platform instagram --limit 1
   ```

   Esperado: exit `0`. `auth_expired` = token errado, expirado ou sem
   permissão — confira os dois valores e repita o passo 4.

!!! warning "Token de teste expira em 24 h"
    Para rodar de verdade (worker/cron) o token precisa ser duradouro e entrar
    na rotina de rotação — tabela e procedimento em
    [Credenciais](../dev/credentials.md). Escopo mínimo (leitura de
    hashtag/mídia, sem publicação): idem.

## 3. TikTok — sem API oficial, cookie `ms_token` (`TIKTOK_MS_TOKEN`)

O TikTok-Api usa a API privada com risco conhecido de anti-bot
([ADR-0003](../adr/0003-coletores-fontes.md)). Por isso:

1. Crie/perfie uma **conta secundária** dedicada à coleta — **nunca** a conta
   principal (regra da casa).
2. Faça login nessa conta no navegador.
3. Abra o DevTools → **Application → Cookies** e copie o valor do cookie
   `ms_token`.
4. Cole em `TIKTOK_MS_TOKEN` no [lugar certo do ambiente](#4-onde-colar).
5. `TIKTOK_PROXY` é opcional (proxy http(s) se a rede local for bloqueada).
6. Verifique:

   ```bash
   docker exec cf-python uv run cf collect --segment candles --platform tiktok --limit 1
   ```

   Esperado: exit `0`. `auth_expired` = token ausente/expirado — rotacionar
   (passos 2–4 de novo). `blocked` = anti-bot pegou a sessão — **pausar** a
   coleta TikTok, não insistir; ver [Anti-ban](anti-ban.md).

!!! warning "Coleta via sessão Playwright = risco de bloqueio"
    A conta descartável é o seguro. `ms_token` expira e é rotacionado do mesmo
    jeito: [Credenciais — conta secundária e rotação](../dev/credentials.md).

## 4. Onde colar

As três chaves ficam na **camada de segredos do worker**:
`.devcontainer/secrets/secrets.env` (arquivo real gitignored; o exemplo
versionado `secrets.env.example` nasce vazio). As camadas completas
(`secrets.env` worker / `app.env` do python / `.env` raiz), quem lê cada uma
e o que nunca commitar estão na tabela de
[Credenciais — onde ficam os segredos](../dev/credentials.md#onde-ficam-os-segredos-spec-7)
— não repita aqui.

Forma segura de preencher (placeholders nunca no git):

```bash
cp .devcontainer/secrets/secrets.env.example .devcontainer/secrets/secrets.env
# edite secrets.env com um editor local; valores tipo SUA_CHAVE_AQUI
# apenas como lembrete, nunca o valor real em arquivo rastreado
docker compose -f .devcontainer/docker-compose.yml restart worker
```

O worker carrega os segredos via Docker secret `app_secrets`
(`.devcontainer/docker-compose.yml`); após editar `secrets.env`, reinicie o
worker para o novo valor valer.

## 5. Sintoma → ação

Resumo só do essencial; procedimento completo e tabela ampla em
[Credenciais — rotação](../dev/credentials.md#rotacao-sintoma-acao).

| Token | Sintoma (`collect_runs.error_code`) | Ação |
|---|---|---|
| `YOUTUBE_API_KEY` | `rate_limited` (403 logo na primeira busca) | conferir se a API está habilitada e as restrições da chave; se vazou, criar nova e **revogar** a antiga no Cloud Console |
| `INSTAGRAM_ACCESS_TOKEN` / `IG_USER_ID` | `auth_expired` (401/400) | token errado/expirado: gerar novo com escopo mínimo, **revogar** o anterior, atualizar env, reiniciar worker |
| `TIKTOK_MS_TOKEN` | `auth_expired` | logar na conta secundária, copiar `ms_token` novo, atualizar env, reiniciar worker |
| `TIKTOK_MS_TOKEN` | `blocked` | anti-bot: pausar a coleta TikTok (não retry cego), esperar e rotacionar se necessário |

Depois de qualquer rotação: um `cf collect` de baixo limite para confirmar e
apagar o valor antigo de onde quer que tenha sido colado temporariamente
([Credenciais](../dev/credentials.md)).

## 6. Verificação final

Antes de subir qualquer serviço com as chaves preenchidas:

```bash
uv run python scripts/check_privacy.py
```

Esperado: exit `0` e nenhuma saída suspeita. É o mesmo gate do CI
(`make privacy`); ele falha se um valor real aparecer em arquivo versionado.
Com ele verde, as chaves estão só nos arquivos gitignored e é seguro seguir
para [Operação](operacao.md).
