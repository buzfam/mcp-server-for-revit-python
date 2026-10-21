# Fork buzfam

Este repositório é o fork da org **buzfam** de
[`mcp-servers-for-revit/mcp-server-for-revit-python`](https://github.com/mcp-servers-for-revit/mcp-server-for-revit-python).

## Por que existe

O upstream está sendo preparado para o Autodesk Marketplace, o que limita o
que entra lá (revisão de segurança, privacidade, escopo). Aqui adicionamos as
tools que resolvem as dores reais do dia a dia no Revit, **sem enviar as
mudanças para o upstream**.

O Revit 2027 trouxe um servidor MCP nativo (technical preview) que, pelos
relatos, só **lê** o modelo. O diferencial deste fork é:

1. **Ações em lote que alteram o modelo**, sempre com o diálogo de aprovação no Revit.
2. **Relatórios prontos** (saúde do modelo, seleção, parâmetros) que poupam o
   LLM de escrever IronPython via `execute_revit_code`.
3. Compatibilidade com **Revit 2026 e anteriores**, que não têm o MCP nativo.
4. **Modo fábrica** (spec 005): vários Revits em vários PCs controlados por um
   hub central, com aprovação e auditoria.

## O que muda em relação ao upstream

| Área | Upstream | Fork |
|---|---|---|
| Tools | 20 | 44 (+ specs 001–004 e 007–020: seleção, parâmetros, saúde, folhas, templates, tags, exportação, modelagem, auditoria, conferências, quantitativos, fases, PDF, renumeração, pisos e forros) |
| Guia para agentes | — | `AGENTS.md`, `CLAUDE.md`, `.claude/skills/` |
| Acompanhamento | — | `HANDOFF.md`, `CHANGELOG.md`, `docs/specs/` |
| `.gitignore` | ignora `CLAUDE.md` | `CLAUDE.md` versionado |
| Rede | só loopback | também modo remoto com token próprio (`--http --host`), ponte com o Revit segue loopback |

Segurança herdada e mantida: pareamento DPAPI, só loopback, diálogo de
aprovação para tudo que altera o modelo.

## Roadmap

Priorizado a partir da pesquisa de dores dos usuários de Revit (folhas,
parâmetros em massa, saúde do modelo, tags, templates). Status em `docs/specs/README.md`.

| # | Tool | Tipo | Dor que resolve |
|---|---|---|---|
| 001 | `get_selected_elements` | leitura | Base: "o que eu selecionei?" |
| 002 | `get_element_parameters` | leitura | Base: ver parâmetros de elementos |
| 003 | `bulk_set_parameters` | escrita | Edição de parâmetros em massa (Excel/CSV → Revit) |
| 004 | `model_health_report` | leitura | Avisos, CAD importado, in-place, vistas soltas |
| 007 | `create_sheets` / `place_views_on_sheets` (feito) | escrita | Criação e organização de folhas |
| 008 | `apply_view_template` (lote, feito) | escrita | Templates em muitas vistas |
| 009 | `tag_elements` (feito) | escrita | Tags por categoria e vista (generaliza `tag_walls`) |
| 010 | `export_schedule` / `export_rooms` (feito) | leitura | Dados para CSV/Excel |
| 011 | `create_walls` / `create_rooms` / `place_hosted_families` (feito) | escrita | Modelar sem o agente escrever código solto |
| 012 | `audit_template` (feito) | leitura | Auditoria do template (19 itens) |
| 013 | `check_sheets` (feito) | leitura | Itens faltando nas pranchas antes da emissão |
| 014 | `check_render_materials` (feito) | leitura | Materiais prontos para o Lumion |
| 015 | `room_finishes` / `quantities_by_rule` (feito) | leitura | Quantitativos com regra de medição explícita |
| 016 | `phases_report` / `set_element_phases` (feito) | leitura + escrita | Existente, demolir e construir em reforma |
| 017 | `export_sheets_pdf` (feito) | arquivo | Emissão em PDF com nome e manifesto |
| 018 | `renumber_elements` (feito) | escrita | Numeração de portas, janelas, ambientes e folhas |
| 019 | `import_parameters_from_csv` (em revisão) | escrita | Planilha → parâmetros |
| 020 | `create_floors_from_rooms` / `create_ceilings_from_rooms` (feito) | escrita | Piso e forro modelados para quantitativos |
| 006 | **Acesso pela rede direto** (feito) | transporte | Agente em outra máquina controla o Revit pelo IP do PC ([guia](docs/acesso-remoto.md)) |
| 005 | **Fábrica**: hub central + agentes nos PCs | arquitetura | Vários PCs num ponto só, com aprovação e auditoria centrais |

Specs detalhadas existem para 001 a 020; as próximas são escritas quando entrarem
em desenvolvimento.

## Sincronizar com o upstream

Use a skill `sync-upstream` (`.claude/skills/sync-upstream/SKILL.md`) ou:

```bash
git remote add upstream https://github.com/mcp-servers-for-revit/mcp-server-for-revit-python.git  # uma vez
git fetch upstream
git switch -c sync/upstream-$(date +%Y%m%d)
git merge upstream/master
```
