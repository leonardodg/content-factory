# Evidência — sondas do Suggest e revisão do commit `db3ad2e` (16/10/2026)

Registro do que foi **medido e verificado** em 16/10/2026, para a F1/F4 do
plano [Relevância web e potencial](../ai-plans/2026-10-16-web-relevancia-potencial.md).
Complementa o pré-registro ([validação web](validacao-web.md)): aqui ficam os
números brutos e as análises; lá ficam os critérios congelados.

Nada aqui altera critério pré-registrado. As medições do autocompletar são
**read-only** (nenhuma escrita no banco, nenhum restart do worker).

## 1. Revisão do commit `db3ad2e` — achados e correção

Revisão externa em modo só leitura. Todos os achados foram reproduzidos antes de
corrigir.

| # | Achado | Verificação | Correção |
| --- | --- | --- | --- |
| 1 | O smoke test da imagem **sempre passava** | `docker run` sem `-i` não entrega o stdin: um probe com `sys.exit(3)` saía **0**. Mesmo com `-i`, `--entrypoint python` explode em `ModuleNotFoundError: content_factory` | probe extraído para `scripts/check_worker_segments.py`; o script usa `docker run --rm -i --entrypoint uv "$IMAGE" run python -` |
| 2 | Falta de protocolo caía no legado **em silêncio** | o teste novo fixava o fallback como aceitável | `WEB_QUERIES_REQUIRED=1` (`Settings` → `WebCollector(required=...)`): ausente/inválido → `CollectorError("missing_protocol")`, collect `failed`, legado **não** roda |
| 3 | `COPY segments` + teste de política | corretos | registrado: os YAMLs ficam fixos no build → aprovar protocolo exige **novo build** |
| 4 | `test_queries_for_reloads_after_mtime_change` instável | dependia da resolução do mtime | força o mtime com `os.utime` |
| 5 | commit numa branch cujo PR já foi mergeado | procedente | sem push; branch/PR novo fica com quem está na branch |

### Prova do defeito 1 (executado contra a imagem `content-factory-worker`)

| Comando | Exit | Significado |
| --- | --- | --- |
| probe `sys.exit(3)`, sem `-i` | **0** | nada roda — falso verde |
| probe `sys.exit(3)`, com `-i` | 3 | o probe roda de verdade |
| `--entrypoint python` | 1 | `ModuleNotFoundError: No module named 'content_factory'` |
| `scripts/smoke_worker_segments.sh` (imagem atual) | **1** | falha como esperado: a imagem em produção é anterior até ao módulo web |

O smoke test agora **falha** quando `segments/web-queries/` está ausente — é o
gate que impede subir `WEB_QUERIES_REQUIRED=1` sem protocolo.

## 2. Método da medição das sondas

O Suggest é semeado com as keywords do segmento e devolve variações de produto.
A opção (a) da revisão acrescenta **sondas de intenção**. Medido contra o
autocompletar real, comparando a cobertura **SEM sondas** (só keywords) e **COM
sondas** (keywords + sondas `probe-v2` sobre os termos-cabeça).

Reprodução: `uv run python scripts/measure_suggest_probes.py`.

### Experimento A — a sonda rende na cabeça do termo, não na cauda longa

Consultas de sonda diretas ao autocompletar (`client=firefox`):

| Sonda | Sugestões | Uteis (resolver/tendencia) |
| --- | --- | --- |
| `vela artesanal nao` | 0 | 0 |
| `vela artesanal tendencia` | 0 | 0 |
| `sabonete artesanal nao` | 0 | 0 |
| `vela artesanal vale a pena` | 0 | 0 |
| `vela artesanal derrete` | 0 | 0 |
| `vela nao` | 10 | 10 |
| `velas nao` | 10 | 10 |
| `pavio nao` | 8 | 8 |
| `vela tendencia` | 10 | 10 |
| `sabonete mais vendido` | 10 | 10 |
| `artesanato mais vendido` | 10 | 10 |
| `resina epoxi nao` | 2 | 2 |
| `bolo de cenoura nao` (controle, nicho popular) | 7 | 7 |

**Análise:** o mecanismo da sonda está correto (o controle em nicho popular
rende 7 frases de `resolver`). O limite é o **dado** do nicho: a cauda longa
("vela artesanal") praticamente não tem autocompletar. Por isso os termos
sondados passam a ser `SegmentConfig.probe_terms` (cabeça do termo), não as
keywords do segmento.

### Experimento B — trocar o `client` do endpoint não muda nada

`vela artesanal tendencia`, `vela artesanal nao`, `sabonete artesanal nao`
devolvem **0** em `client=firefox` **e** em `client=chrome`. Confirma que não é
a consulta: é a ausência de dado.

## 3. Resultado — cobertura por segmento e por classe

Contagens por classe (`intent_coverage`) sobre as frases devolvidas pelo
autocompletar. "SEM" = só as keywords do segmento; "COM" = keywords + 15 sondas
(`probe-v2` × 3 termos-cabeça).

| Segmento | Estado | Frases | aprender | resolver | tendencia | comercial | sem_intencao |
| --- | --- | --- | --- | --- | --- | --- | --- |
| velas-artesanais | SEM | 149 | 26 | 7 | **0** | 7 | 109 |
| velas-artesanais | COM | 238 | 43 | **35** | **22** | 28 | 110 |
| sabonetes-artesanais | SEM | 144 | 29 | 5 | **1** | 9 | 100 |
| sabonetes-artesanais | COM | 218 | 49 | **25** | **20** | 24 | 100 |
| resina-artesanal | SEM | 145 | 29 | 2 | **0** | 11 | 103 |
| resina-artesanal | COM | 218 | 54 | **19** | **13** | 29 | 103 |

Com as sondas, **as 4 classes têm ≥ 3 frases em todos os 3 segmentos** — o
insumo do `q1` (3 por classe + 1 controle = 13 consultas) passa a existir.

Termos-cabeça usados (`segments/*.yaml` → `probe_terms`):

| Segmento | `probe_terms` |
| --- | --- |
| velas-artesanais | `vela`, `velas`, `pavio` |
| sabonetes-artesanais | `sabonete`, `sabonetes`, `saboaria` |
| resina-artesanal | `resina`, `resina epoxi`, `artesanato` |

Modelos de sonda (`probe-v2`): `como fazer <termo>` (aprender), `<termo> nao`
(resolver), `<termo> tendencia` + `<termo> mais vendido` (tendencia),
`<termo> preco` (comercial).

## 4. Análise e limitações

**O que a sonda resolve:** `resolver` e `tendencia`, que eram 0–2 e 0–1 frases
por segmento, passam a 19–35 e 13–22. O diagnóstico da revisão (o Suggest é
semeado com o nome do segmento e devolve produto) estava correto; o que faltava
era sondar a cabeça do termo.

**Limitação 1 — `tendencia` é induzido pela sonda.** A sonda de tendência leva
o próprio marcador, então a frase cai na classe por construção. A consulta é
uma busca real de usuário, mas o estrato `tendencia` do `q1` é **induzido**, não
descoberto passivamente. Na leitura da F4, tratar `tendencia` como piso
exploratório.

**Limitação 2 — termos-cabeça são ambíguos em PT.** Exemplos observados:

| Frase | Sentido fora do segmento |
| --- | --- |
| `resina nao clareia` / `resina nao para no dente` | resina odontológica |
| `vela nao sai do motor` / `vela nao da faisca` | vela de ignição |
| `velas tendencia alcista` / `vela martillo en tendencia alcista` | vela de gráfico (candlestick) |
| `vela nao queimou toda umbanda` | vela ritualística |

O `q1` é revisado pelo usuário, que **veta** a frase inadequada
(`cf web-queries --veto/--reason`); o veto e o motivo ficam registrados no YAML.
Decisão de 16/10: **manter `probe-v2` completo** e vetar na revisão.

**Limitação 3 — o léxico `intent-v2` não é o gargalo.** A cobertura do léxico
sobre os sinais **já gravados** (banco real) é 40/154 (velas), 45/150
(sabonetes) e 42/151 (resina) — o resto é `sem_intencao` por não ter marcador,
não por erro de classificação. As frases úteis simplesmente não estavam nos
sinais.

## 5. O que ainda não foi feito (depende do deploy)

- O **novo ciclo de research** com as sondas roda no worker (cron diário), só
  depois do deploy de 21/10 após 19:20 UTC. Antes disso os sinais no banco
  ainda não têm as frases das sondas.
- O **`q1` só é regenerado** depois desse ciclo (`cf web-queries --propose`),
  seguido da revisão/veto do usuário. Os YAMLs provisórios (gerados sem as
  sondas) foram removidos.
- O deploy precisa de `cf segment load` para subir os `probe_terms` novos.
