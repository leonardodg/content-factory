"""Opcao (a) da revisao 16/10: semear o Suggest pela INTENCAO.

O autocompletar, semeado so com o nome do segmento ("vela artesanal"),
devolve variacoes de produto — quase nada de "resolver" (pergunta de problema)
nem de "tendencia". Sondar a intencao faz o Google completar com frases REAIS
dessas classes: o texto continua vindo do autocompletar, nada e inventado.

MEDIDO (16/10, autocompletar real): a sonda so rende na **cabeca do termo**
("vela", "velas", "pavio"), nao na cauda longa ("vela artesanal" -> 0
sugestoes). Por isso os termos sondados sao `SegmentConfig.probe_terms`, e nao
as keywords do segmento. Exemplos medidos por segmento (resolver+tendencia):
`vela nao`/`vela tendencia`; `sabonete mais vendido`; `artesanato mais vendido`
(resina, cujo termo "resina" e ambiguo demais).

Estas sondas alimentam SO a chamada ao Suggest (`web.expand`). Os termos das
redes sociais continuam sendo as keywords do segmento — ver
`app.research_pipeline.run_research_cycle`.

LIMITES CONHECIDOS (registrar na interpretacao da F4):
- a sonda de tendencia leva o proprio marcador, entao o estrato `tendencia` do
  protocolo e **induzido**, nao "descoberto" passivamente;
- a cabeca do termo e ambigua em PT ("resina" = odontologica; "vela" = vela de
  ignicao), entao entram frases fora do segmento — o usuario veta no `q1`.
"""

from collections.abc import Sequence

SUGGEST_PROBE_VERSION = "probe-v2"

# Modelos de sonda por intencao (`{kw}` = termo-cabeca). Sonda e um PREFIXO
# plausivel: o autocompletar completa o que vem depois. Cada modelo rende 1
# requisicao por termo-cabeca. Mudar as sondas sobe a versao (`probe-v3`).
INTENT_PROBES: dict[str, tuple[str, ...]] = {
    "aprender": ("como fazer {kw}",),
    "resolver": ("{kw} nao",),
    # "mais vendido" e o que rende em sabonetes/resina; "tendencia" rende em
    # velas (e traz 1-2 frases de vela de grafico, vetaveis no `q1`)
    "tendencia": ("{kw} tendencia", "{kw} mais vendido"),
    "comercial": ("{kw} preco",),
}


def suggest_probes(probe_terms: Sequence[str]) -> list[str]:
    """Sondas de intencao, deterministicas e sem duplicatas, para o Suggest.

    Ordem estavel (termo, classe, modelo). `probe_terms` vazio -> sem sondas
    (comportamento anterior: o Suggest recebe so as keywords do segmento).
    """
    out: list[str] = []
    seen_terms: set[str] = set()
    for term in probe_terms:
        base = term.strip()
        key = base.casefold()
        if not base or key in seen_terms:
            continue
        seen_terms.add(key)
        for templates in INTENT_PROBES.values():
            for template in templates:
                probe = template.format(kw=base).strip()
                if probe and probe not in out:
                    out.append(probe)
    return out
