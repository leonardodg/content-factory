# Anti-ban — segurança operacional

Este capítulo cobre o que **já te protege** (sem você fazer nada), o que **o
operador precisa fazer** para não ser banido, o que o sistema **ainda não
faz** (limitações honestas) e o que fazer quando um sinal de ban/limite
aparecer. Detalhe técnico das janelas, erros e do caminho do `collect`:
[Fluxo de busca](fluxo-busca.md). Rotina de serviços e logs:
[Operação](operacao.md).

## 1. O que o sistema já faz por você

Nada abaixo exige configuração — é o comportamento padrão do `cf collect`:

| Proteção | Comportamento real | Onde |
|---|---|---|
| Janela YouTube | 100 descobertas / 24 h (a cota da API é 10.000 unidades/dia e cada `search` custa 100 — a janela espelha a cota) | `DEFAULT_WINDOWS` em `infra/ratelimit.py` |
| Janela TikTok | 6 descobertas / 60 s | idem |
| Janela Instagram | 20 descobertas / 60 s | idem |
| Janela fallback | 10 / 60 s para plataforma sem janela própria (ex.: `web`) | idem |
| Backoff em janela estourada | exponencial `2**attempt` com jitter ±20%, espera o reset parcial da janela | `backoff_delay()` |
| Teto de espera | máx. 5 tentativas; se a espera necessária passaria de 60 s, falha na hora com `rate_limited` (sem segurar o worker) | `acquire()` |
| Lock de execução única | lock Redis por segmento com token de posse (Lua CAS) — uma coleta por vez; a segunda vira `ja_em_execucao` sem bater na API (TTL 30 min cobre processo morto) | `infra/lock.py` |
| Retry anti-bot do TikTok | só `EmptyResponseException` é retried: `2**attempt + jitter(0..2 s)`, máx. 5 tentativas → `blocked` no fim | `infra/collectors/tiktok.py` |
| Fail-fast de `auth_expired` | token ausente/expirado falha na hora, **sem retry cego** | coletores TikTok/Instagram |
| Redação de tokens | as mensagens de erro de `collect`/`ingest`/`score`/`status` passam por `redact` antes de imprimir — segredo não vaza em log/stdout | `cli/main.py` |

Janelas por plataforma e vocabulário de erros (`rate_limited`, `blocked`,
`auth_expired`…): [Fluxo de busca §2](fluxo-busca.md#2-uma-secao-por-plataforma).

## 2. Regras do operador

As proteções da seção 1 limitam o *sistema*; estas regras limitam *você*:

1. **TikTok sempre na conta secundária** — nunca na principal. O TikTok-Api
   usa a API privada com anti-bot conhecido
   ([conta secundária](../dev/credentials.md)); conta principal é banimento
   garantido a médio prazo.
2. **Rotacionar o token no primeiro `auth_expired`** — não insista. O
   procedimento por plataforma está em
   [Chaves de API](chaves-api.md). Depois de rotacionar, rode um `cf collect`
   com limite baixo para confirmar.
3. **`--limit` gradual** — comece com 3–10; o default da CLI é 10 e 50 é o
   **teto** (`--limit` max), não o default. Suba devagar observando
   `collect_runs`.
4. **YouTube: veja a cota antes de subir `COLLECT_LIMIT`** — cada descoberta
   custa 102 unidades (100 do `search` + 1 de `videos` + 1 de `channels`) e a
   cota padrão é 10.000/dia ≈ 98 descobertas (**≈** a janela do limiter).
   Dobrar o `--limit` não cria cota: só esgota a janela diária duas vezes
   mais rápido.
5. **Não rode coleta manual durante o cron do worker** — o lock evita
   duplicata (a segunda vira `ja_em_execucao`), mas o resultado confuso na
   saída só polui a leitura. Horários do cron:
   [Operação](operacao.md).

## 3. Limitações honestas (backlogs)

O que **não** está implementado hoje — saiba disto antes de confiar demais:

1. **Circuit breaker não está ligado no `collect`** — o limiter tem
   breaker (3 falhas consecutivas → `blocked` por 15 min, sem bater na API),
   mas o use-case de coleta **não chama `record_failure`**: 3 falhas seguidas
   não bloqueiam automaticamente. Existe em código e em testes, não no fluxo
   de produção.
2. **`INCR` + `EXPIRE` do rate-limit não é atômico** — se o processo morrer
   entre os dois comandos, a janela pode ficar **sem TTL** (a chave nunca
   expira e a cota trava até alguém apagar). Backlog §6 da
   [REVISAO](../REVISAO_2026-10-07.md).
3. **Pausa 2–8 s entre requests não existe** — o design
   ([§7](../specs/2026-10-07-content-factory-design.md#7-seguranca-e-anti-bloqueio-aprovado))
   previa pausa aleatória entre chamadas; hoje só há backoff **em falha**.
   Requests dentro da janela saem em rajada.
4. **Mascaramento de erro no `finally` do collect** — um erro raiz (ex.:
   transação abortada) às vezes some sob o `runs.record` do `finally` e a
   CLI mostra `internal_error` sem a causa. Backlog #1 da
   [REVISAO](../REVISAO_2026-10-07.md).

## 4. Sintomas de ban/limite → ação

| Sintoma (`collect_runs` / envelope da CLI) | Leitura | Ação |
|---|---|---|
| `rate_limited` repetido no YouTube | cota diária esgotada | esperar o **reset diário da cota (meia-noite PT do Google)**; não adianta retry — a janela do limiter só destrava com o tempo |
| `blocked` no TikTok | anti-bot respondeu vazio 5× seguidas | pausar **15+ min**; se persistir, trocar sessão (`ms_token` novo) e/ou `TIKTOK_PROXY`; confira também a [conta secundária](../dev/credentials.md) |
| `auth_expired` (Instagram 400/401, TikTok sem token) | credencial expirou/revogada | rotacionar o token conforme [Chaves de API](chaves-api.md) — não reenviar a coleta com o token velho |
| Muitos `network` | problema **local** (DNS, VPN, proxy, rede), não da plataforma | checar conectividade/VPN/`TIKTOK_PROXY` antes de culpar a API |
| `ja_em_execucao` | worker e manual disputando o mesmo segmento | deixar o cron terminar; rode o manual noutro horário |

Auditoria durável de tudo isso: tabela `collect_runs` (uma linha por
plataforma por execução) — como ler: [Operação](operacao.md) ·
[Análise](analise.md).
