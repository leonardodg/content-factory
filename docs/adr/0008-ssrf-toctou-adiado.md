# ADR-0008 — SSRF por DNS rebinding (TOCTOU) no `cf ingest`: adiado com gatilho

**Situação:** Aceita · **Data:** 2026-10-15

## Contexto

`cf ingest <url>` baixa uma página para virar candidato `web`. A guarda
`infra/ssrf.assert_public_http` (injetada pela CLI; sem ela o ingest falha
fechado) exige `http`/`https`, resolve o host e bloqueia IP privado,
loopback, link-local (inclui o metadata `169.254.169.254`), multicast,
reservado, não especificado e CGNAT. O `_fetch` não segue redirects, corta
em 2 MB e tem timeout de 10 s.

Resta uma janela **TOCTOU**: a guarda resolve o DNS e o `httpx` resolve de
novo ao conectar. Um domínio controlado por atacante com TTL curto pode
responder um IP público na validação e um IP interno na conexão (DNS
rebinding). Registrado na revisão de 2026-10-07 e como R13 na revisão de
2026-10-14; as duas auditorias externas de 2026-10-15 pediram a decisão
formal.

## Decisão

**Adiar** a correção enquanto as três condições valerem:

1. `cf ingest` é **CLI local**, executada pelo operador;
2. a URL vem do operador (não de fonte externa automática, como resultado
   de busca ou fila);
3. o processo não roda em nuvem com endpoint de metadata nem em rede com
   serviços internos sem autenticação.

Nessas condições, explorar o rebinding exige que o próprio operador cole uma
URL maliciosa contra a própria máquina.

## Gatilho de reabertura

Corrigir **antes** de qualquer um destes acontecer:

- o ingest ser exposto por API, webhook, bot ou interface remota;
- o ingest receber URLs automaticamente (worker, research, resultados do
  `web`);
- o worker ou a CLI passarem a rodar em nuvem ou em rede compartilhada.

## Correção prevista (quando reabrir)

- Resolver o DNS **uma vez**, validar todos os IPs e **conectar no IP
  validado** (transporte com resolução fixada, mantendo o `Host`/SNI do
  nome original);
- manter redirects desligados ou revalidar cada salto pela mesma guarda;
- teste com resolvedor fake que troca o IP entre a validação e a conexão.

## Consequências

- Nenhuma mudança de código agora; o risco fica nomeado e com condição de
  saída verificável.
- Quem abrir o ingest para automação ou rede deve ler este ADR primeiro (ver
  o docstring de `infra/ssrf.py`).
