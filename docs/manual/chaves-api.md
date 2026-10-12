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
    arquivos gitignored: `secrets.env` (worker) **e** `app.env` (CLI manual
    — camadas em [§4](#4-onde-colar)). O gate
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
   [lugar certo do ambiente](#4-onde-colar) (camada `secrets.env` **e**
   também `.devcontainer/env/app.env` — é o único arquivo que a verificação
   do passo 6 enxerga, ver [§4](#4-onde-colar)).
6. Verifique — **o comando roda em `cf-python`, que lê apenas `app.env`**:
   com a chave só em `secrets.env` o comando falha (YouTube devolve erro) e a
   chave parece quebrada. Confirme o valor em `.devcontainer/env/app.env` e,
   se acabou de editar o arquivo, recrie o container com
   `docker compose -f .devcontainer/docker-compose.yml up -d python`.

   ```bash
   docker exec cf-python uv run cf collect --segment candles --platform youtube --limit 1
   ```

   Esperado: exit `0`.
   `rate_limited` com 403 logo de cara = chave mal habilitada ou restrição
   errada (origem/IP), não cota de verdade.

**Cota:** 10.000 unidades/dia; cada `search` custa 100 U e cada `list` 1 U —
o limiter interno janela 100 descobertas/24 h para espelhar a cota (detalhe em
[Anti-ban](anti-ban.md) e [Fluxo de busca](fluxo-busca.md)). Rotação em caso de
vazamento ou 403 persistente: [Credenciais — rotação](../dev/credentials.md#rotacao-sintoma-acao).

## 2. Instagram — Graph API (`INSTAGRAM_ACCESS_TOKEN` + `IG_USER_ID`)

!!! danger "Existem DOIS produtos Instagram diferentes no app Meta — use o certo"
    O painel do app em developers.facebook.com oferece dois fluxos com nomes
    parecidos e permissões **incompatíveis**:

    - **"API do Instagram com login do Instagram"** (`instagram_business_basic`,
      `instagram_business_manage_*`) — produto novo, **não é o que o código
      usa**, e o `IG_USER_ID` desse fluxo tem um namespace diferente.
    - **"API do Instagram com login do Facebook"** (`instagram_basic`,
      `pages_show_list`, `pages_read_engagement`, `business_management`) —
      **é este** que `infra/collectors/instagram.py` espera
      (`graph.facebook.com/v21.0`, `ig_hashtag_search`/`{user}/media`).

    Gerar o token pelo produto errado é a causa real de um bug encontrado em
    2026-10-12: o token tinha permissão, mas do produto errado — o Graph
    devolvia `403 "(#10) Requires instagram_basic permission"`, sintoma
    diferente do gate #10 documentado (ver `docs/PLANO_TESTE_E2E.md`, Bug 5).
    **Siga os passos abaixo com o produto "login do Facebook", não o outro.**

1. Em [developers.facebook.com](https://developers.facebook.com/), em **My
   Apps → Create App**, escolha o tipo **Business**.
2. No app criado, em **Casos de uso → Gerenciar mensagens e conteúdo no
   Instagram → Personalizar**, use a aba **"Configuração da API com login do
   Facebook"** (não a aba "...com login do Instagram").
3. A conta do Instagram precisa ser **Business ou Creator** e estar **vinculada
   a uma Page** do Facebook (configuração no app do Instagram: Conta →
   Profissional/Configurações de negócios).
4. Em **Permissões e recursos**, adicione (Advanced Access fica para depois —
   em modo de desenvolvimento essas 4 já funcionam para admins/testers do app):
   `instagram_basic`, `pages_show_list`, `pages_read_engagement`,
   `business_management`.
5. Gere o token no **Graph API Explorer**
   (developers.facebook.com/tools/explorer, selecione o app certo):
   - `Usuário ou Página` → **"Obter token de acesso do usuário"**;
   - marque as 4 permissões do passo 4;
   - clique **Generate Access Token** — se o popup do OAuth for bloqueado
     pelo navegador, use a URL direta (substitua `APP_ID`):
     `https://www.facebook.com/v21.0/dialog/oauth?client_id=APP_ID&redirect_uri=https://developers.facebook.com/tools/explorer/&response_type=token&scope=instagram_basic,business_management,pages_read_engagement,pages_show_list`;
   - autorize, escolhendo a Page vinculada à conta do Instagram.
   - `IG_USER_ID`: com o token gerado, no próprio Explorer rode
     `GET /{page-id}?fields=instagram_business_account` (ou veja em
     Configurações da Página → Instagram) para pegar o id da conta
     Business/Creator vinculada.
6. **Verifique o token ANTES de colar no projeto** — no Graph API Explorer,
   rode `GET /{IG_USER_ID}?fields=id,username` com o token gerado. Resposta
   `{"id": ..., "username": ...}` = token certo. Erro `403`/`"missing
   permissions"` = token do produto errado (volte ao passo 2) ou sem a Page
   selecionada no passo 5.
7. Preencha `INSTAGRAM_ACCESS_TOKEN` e `IG_USER_ID` no
   [lugar certo do ambiente](#4-onde-colar).
8. Verifique de novo, agora via CLI (mesma camada do passo 6 do YouTube: o
   `cf-python` lê só `app.env` — confirme `INSTAGRAM_ACCESS_TOKEN` e
   `IG_USER_ID` também em `.devcontainer/env/app.env` antes de rodar,
   [§4](#4-onde-colar)):

   ```bash
   docker exec cf-python uv run cf collect --segment candles --platform instagram --limit 1
   ```

   Esperado: exit `0`. Com o token certo mas sem *App Review* da feature
   "Instagram Public Content Access" aprovado, o log mostra `instagram: gate
   #10 (App Review pendente) — usando mídia própria` e o `collect` ainda
   assim retorna `ok` (fallback automático — ver
   `docs/ai-plans/2026-10-10-fix-instagram-collector.md`). Isso é esperado,
   **não é o mesmo bug do passo 6**: aqui o token está correto, só falta a
   aprovação da Meta para hashtag de terceiros.

9. **Estenda para token de longa duração (60 dias)** antes de usar no
   worker/cron — o token do passo 5 (`Usuário`) expira em 1-2h:
   - vá em [Access Token Debugger](https://developers.facebook.com/tools/debug/accesstoken/)
     (ou, no Graph API Explorer, clique no ícone **ⓘ** ao lado do campo
     "Token de acesso" → **"Abrir na ferramenta Token de Acesso"**);
   - cole o token (ou deixe pré-preenchido pelo link acima) e clique
     **Depurar**;
   - clique **Estender token de acesso** — a Meta pode pedir sua senha de
     novo para confirmar (só você digita, nunca cole a senha em
     automação/terceiros);
   - o token novo (60 dias) aparece em destaque na própria página, com a
     data de expiração. Cole esse valor em `INSTAGRAM_ACCESS_TOKEN`, nos
     dois arquivos de novo ([§4](#4-onde-colar)), e recrie `python` e
     `worker` para o valor valer.
10. **Renovação**: 60 dias é o máximo que um *User Access Token* aceita essa
    extensão — repita o passo 9 antes de vencer (`debug_token` mostra
    `Expira` com a data). Para não depender de repetir isso a cada ~2 meses
    em produção, o caminho correto é um **System User token** (Business
    Settings → System Users → gerar token **sem expiração**, com as mesmas
    4 permissões do passo 4) — fica fora do escopo de "destravar a primeira
    coleta" deste capítulo; procedimento completo em
    [Credenciais — rotação](../dev/credentials.md#rotacao-sintoma-acao).

!!! warning "Token de teste expira em 1-2 h (User Access Token), 24 h, ou 60 dias (estendido)"
    Token de usuário comum dura 1-2h; estendido (passo 9) dura 60 dias;
    token de teste de app dura 24h. Para rodar de verdade (worker/cron) sem
    repetir a extensão manual, use System User token (passo 10) e entre na
    rotina de rotação — tabela e procedimento em
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
6. Verifique (idem: `cf-python` lê só `app.env` — confirme
   `TIKTOK_MS_TOKEN` também em `.devcontainer/env/app.env` antes de rodar,
   [§4](#4-onde-colar)):

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

Camadas na prática (o que cada serviço lê, conforme
`.devcontainer/docker-compose.yml`):

- **Worker** lê `app.env` **+** `secrets.env` (Docker secret `app_secrets`);
- **CLI manual (`cf-python`)** lê **apenas** `app.env` (`env_file`, sem
  `secrets:`) — por isso as chaves usadas na verificação dos passos 6 dos
  capítulos 1–3 **também** vão em `.devcontainer/env/app.env` (mesmo nome de
  variável, arquivo igualmente gitignored; `app.env.example` já traz o
  campo). Só em `secrets.env`, o `docker exec cf-python …` falha e a chave
  parece quebrada;
- `TIKTOK_PROXY` vive só em `app.env` (não é segredo e não consta de
  `secrets.env.example`).

Forma segura de preencher (placeholders nunca no git):

```bash
cp .devcontainer/secrets/secrets.env.example .devcontainer/secrets/secrets.env
cp .devcontainer/env/app.env.example .devcontainer/env/app.env
# edite os dois com um editor local; valores tipo SUA_CHAVE_AQUI
# apenas como lembrete, nunca o valor real em arquivo rastreado
docker compose -f .devcontainer/docker-compose.yml restart worker
docker compose -f .devcontainer/docker-compose.yml up -d python
```

O worker carrega os segredos via Docker secret `app_secrets`
(`.devcontainer/docker-compose.yml`); após editar `secrets.env`, reinicie o
worker para o novo valor valer. O `cf-python` recebe as envs do `env_file`
na **criação** do container: após editar `app.env`, recrie-o com
`docker compose -f .devcontainer/docker-compose.yml up -d python` (um simples
`restart` não re-lê o `env_file`).

## 5. Sintoma → ação

Resumo só do essencial; procedimento completo e tabela ampla em
[Credenciais — rotação](../dev/credentials.md#rotacao-sintoma-acao).

| Token | Sintoma (`collect_runs.error_code`) | Ação |
|---|---|---|
| `YOUTUBE_API_KEY` | `rate_limited` (403 logo na primeira busca) | conferir se a API está habilitada e as restrições da chave; se vazou, criar nova e **revogar** a antiga no Cloud Console |
| `INSTAGRAM_ACCESS_TOKEN` / `IG_USER_ID` | `auth_expired` (401/400) | token errado/expirado: gerar novo com escopo mínimo, **revogar** o anterior, atualizar env, reiniciar worker |
| `INSTAGRAM_ACCESS_TOKEN` | `upstream_changed` (403 `"Requires instagram_basic permission"`) | token do produto **errado** ("login do Instagram" em vez de "login do Facebook") — gere de novo seguindo [§2](#2-instagram--graph-api-instagram_access_token--ig_user_id) passo a passo, **verificando no passo 6 antes de colar** |
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
