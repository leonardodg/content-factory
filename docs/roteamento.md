# docs — índice de roteamento

| Se você quer… | Vá para |
|---|---|
| Visão geral do sistema numa página | [index.md](index.md) |
| Operar o serviço / manual completo | [manual/index.md](manual/index.md) |
| Ler a ideia original do produto (verbatim da 1ª mensagem) | [produto/ideia-inicial.md](produto/ideia-inicial.md) |
| Entender o QUE é o produto, frentes e não-objetivos | [produto/prd.md](produto/prd.md) |
| Ver o design completo aprovado (arquitetura, schema, segurança) | [specs/2026-10-07-content-factory-design.md](specs/2026-10-07-content-factory-design.md) |
| Saber POR QUE Postgres/Redis/CLI/coletores foram escolhidos | [adr/0001-postgres-pgvector.md](adr/0001-postgres-pgvector.md) (0001…0004) |
| Requisitos técnicos de uma feature (TRD) | [specs/trd-collectors.md](specs/trd-collectors.md) · [trd-scoring](specs/trd-scoring.md) · [trd-cli](specs/trd-cli.md) |
| Executar o plano de implementação | [ai-plans/2026-10-07-fundacao-coletores-banco.md](ai-plans/2026-10-07-fundacao-coletores-banco.md) |
| Retomar a sessão (estado atual, pendências) | [HANDOFF.md](HANDOFF.md) |
| Entender camadas, pacote e fluxo do sistema | [architecture/README.md](architecture/README.md) · [estado e fases](architecture/estado-e-proximas-fases.md) |
| Ver tabelas campo a campo (gerado do schema) | [data-model/README.md](data-model/README.md) |
| Ver diagramas (ER gerado do schema real) | [diagram/README.md](diagram/README.md) |
| Desenvolver ambiente, estender (novo segmento/collector), credenciais | [dev/index.md](dev/index.md) |
| Auditar qualidade (SOLID, revisão) | `SOLID_AUDIT.md`, `REVISAO_*.md` (gerados na entrega) |

## Interfaces do sistema (superfícies)

| Surface | Port | O que é |
|---|---|---|
| CLI `cf` | terminal/agentes | única interface de escrita; envelope JSON de status |
| Banco PostgreSQL | rede interna | fonte de verdade; agentes leem direto |
