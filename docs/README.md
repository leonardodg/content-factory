# docs — índice de roteamento

| Se você quer… | Vá para |
|---|---|
| Ler a ideia original do produto (verbatim da 1ª mensagem) | [produto/ideia-inicial.md](produto/ideia-inicial.md) |
| Entender o QUE é o produto, frentes e não-objetivos | [produto/prd.md](produto/prd.md) |
| Ver o design completo aprovado (arquitetura, schema, segurança) | [specs/2026-10-07-content-factory-design.md](specs/2026-10-07-content-factory-design.md) |
| Saber POR QUE Postgres/Redis/CLI/coletores foram escolhidos | [adr/](adr/) (0001…0004) |
| Executar o plano de implementação | [ai-plans/](ai-plans/) |
| Retomar a sessão (estado atual, pendências) | [HANDOFF.md](HANDOFF.md) |
| Entender camadas, pacote e fluxo do sistema | [architecture/](architecture/) |
| Ver tabelas campo a campo | [data-model/](data-model/) |
| Ver diagramas (ER/arquitetura) | [diagram/](diagram/) |
| Desenvolver ambiente, estender (novo segmento/collector), credenciais | [dev/](dev/) |
| Requisitos técnicos de uma feature (TRD) | [specs/](specs/) |
| Auditar qualidade (SOLID, revisão) | `SOLID_AUDIT.md`, `REVISAO_*.md` (gerados na entrega) |

## Interfaces do sistema (superfícies)

| Surface | Port | O que é |
|---|---|---|
| CLI `cf` | terminal/agentes | única interface de escrita; envelope JSON de status |
| Banco PostgreSQL | rede interna | fonte de verdade; agentes leem direto |
