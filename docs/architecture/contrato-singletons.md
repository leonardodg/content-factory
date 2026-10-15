# Contrato de estado dos componentes compartilhados

_Auditoria externa 2026-10-15, ponto 4.2 (bloco C1 do plano de revisão
sênior)._

O worker único cria estes componentes **uma vez no startup**
(`infra/queue/composition.py`) e os compartilha entre os segmentos e as
plataformas de um tick. O event loop é único: código **sem `await` no
meio** de uma mutação é atômico. A topologia suportada é **1 processo
worker**; mais de um processo exige rever as linhas marcadas com ⚠️.

| Componente | Estado | Escopo | Mutação concorrente | Ordem de término | Após erro / cancelamento | Com > 1 processo |
| --- | --- | --- | --- | --- | --- | --- |
| `YoutubeCollector` | chave + `httpx.AsyncClient` | processo | não muta por chamada; o cliente httpx aceita uso concorrente | irrelevante | nada a resetar; `aclose()` no shutdown | ok (cota é do Redis) |
| `InstagramCollector` | token + id do usuário + `httpx.AsyncClient` | processo | **não muta por chamada** (teste `test_discover_does_not_mutate_shared_instance_state`); o gate #10 é contado por chamada em `call_counters` | irrelevante | nada a resetar; `aclose()` no shutdown | ok |
| `TikTokCollector` | token + proxy | processo | sessão Playwright **por chamada**; teto `tiktok=1` em `PlatformSlots` | irrelevante | sessão fechada no `finally` | ⚠️ o teto de 1 sessão é por processo |
| `WebCollector` | nenhum | — | `DDGS()` novo por chamada, em thread; teto `web=1` | irrelevante | — | ⚠️ `web=1` é por processo |
| `WebSuggestProvider` / `TrendsGoogleProvider` | `CooldownBreaker` (falhas consecutivas, `open_until`) | processo, compartilhado entre segmentos | métodos sem `await` ⇒ atômicos. Falhas de segmentos diferentes **somam** no mesmo contador (é a intenção: o provider é o mesmo) | um sucesso de outro segmento zera a sequência de falhas: "consecutivas" é por provider, não por segmento | `check()` não muta; o estado sobrevive ao erro (é o objetivo) | ⚠️ **em memória**: cada processo teria o seu breaker. Mover para `cf:cb:*` no Redis antes de escalar |
| `PlatformSlots` | 1 `asyncio.Semaphore` por plataforma | processo | seguro (primitiva do asyncio) | irrelevante | `async with` libera a vaga em erro e cancelamento | ⚠️ teto por processo |
| `RedisRateLimiter` (+ breaker por plataforma) | nenhum local; janelas e breaker no Redis (`cf:rl:*`, `cf:cb:*`) | global (Redis) | scripts Lua atômicos (A1) | irrelevante | TTL cura chave órfã | ok |
| `RedisLock` | token de posse por chave | **instância por bundle de segmento** | uma instância = um dono por chave (`acquire_once` levanta se reusar) | `release` só apaga com o próprio token (Lua CAS) | TTL de 30 min cobre processo morto | ok |
| `registry` de coletores | `dict` global | processo | escrito só no startup (`register_defaults`), lido depois | — | — | ok (cada processo registra o seu) |
| `CallCounters` (`gate_hits`, `quota_used`) | `ContextVar` | **por chamada** | isolado por task | — | descartado ao sair do `counting()` | ok |

## Regras

1. Estado que muda por chamada vai em `ContextVar` (`app/call_counters.py`)
   ou no retorno da função, nunca em atributo do coletor.
2. Recurso de rede criado no `__init__` expõe `aclose()` idempotente;
   `compose_app_deps` fecha todos no shutdown (auditoria 4.1).
3. Breaker e tetos **em memória** são uma restrição de topologia (1
   processo). Subir um 2º worker exige antes: breaker dos providers no Redis
   e tetos de `PlatformSlots` revistos.
4. Atributo mutável sem leitor não fica no coletor: `app_usage`/`gate_hits`
   do Instagram foram removidos na segunda auditoria (2026-10-15).
