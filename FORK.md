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

## O que muda em relação ao upstream

| Área | Upstream | Fork |
|---|---|---|
| Tools | 20 | 20 + roadmap abaixo |
| Guia para agentes | — | `AGENTS.md`, `CLAUDE.md`, `.claude/skills/` |
| Acompanhamento | — | `HANDOFF.md`, `CHANGELOG.md`, `docs/specs/` |
| `.gitignore` | ignora `CLAUDE.md` | `CLAUDE.md` versionado |

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
| 005 | `create_sheets` / `place_views_on_sheets` | escrita | Criação e organização de folhas |
| 006 | `apply_view_template` (lote) | escrita | Templates em muitas vistas |
| 007 | `tag_elements` | escrita | Tags por categoria e vista (generaliza `tag_walls`) |
| 008 | `export_schedule` / `export_rooms` | leitura | Dados para CSV/Excel |

Specs detalhadas existem para 001 a 004; as demais são escritas quando entrarem
em desenvolvimento.

## Sincronizar com o upstream

Use a skill `sync-upstream` (`.claude/skills/sync-upstream/SKILL.md`) ou:

```bash
git remote add upstream https://github.com/mcp-servers-for-revit/mcp-server-for-revit-python.git  # uma vez
git fetch upstream
git switch -c sync/upstream-$(date +%Y%m%d)
git merge upstream/master
```
