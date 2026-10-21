# Specs

Uma spec por tool (ou grupo pequeno de tools relacionadas), escrita **antes**
do código. A implementação segue a skill `add-mcp-tool`.

## Índice

| # | Spec | Tipo | Status |
|---|---|---|---|
| 001 | [get_selected_elements](001-get-selected-elements.md) | leitura | Implementada (não validada no Revit) |
| 002 | [get_element_parameters](002-get-element-parameters.md) | leitura | Implementada (não validada no Revit) |
| 003 | [bulk_set_parameters](003-bulk-set-parameters.md) | escrita | Implementada (não validada no Revit) |
| 004 | [model_health_report](004-model-health-report.md) | leitura | Implementada (não validada no Revit) |
| 005 | [Fábrica: hub + agentes](005-fleet-hub.md) | arquitetura | Proposta (adiada; primeiro a 006) |
| 006 | [Acesso pela rede (direto)](006-network-access.md) | transporte | Implementada (não validada no Revit) |
| 007 | [create_sheets, place_views_on_sheets](007-sheets.md) | escrita | Implementada (não validada no Revit) |
| 008 | [apply_view_template](008-apply-view-template.md) | escrita | Implementada (não validada no Revit) |
| 009 | [tag_elements](009-tag-elements.md) | escrita | Implementada (não validada no Revit) |
| 010 | [export_schedule, export_rooms](010-export-data.md) | leitura | Implementada (não validada no Revit) |
| 011 | [create_walls, create_rooms, place_hosted_families](011-modeling.md) | escrita | Implementada (não validada no Revit) |
| 012 | [audit_template](012-audit-template.md) | leitura | Implementada (não validada no Revit) |
| 013 | [check_sheets](013-check-sheets.md) | leitura | Implementada (não validada no Revit) |
| 014 | [check_render_materials](014-check-render-materials.md) | leitura | Implementada (não validada no Revit) |
| 015 | [room_finishes, quantities_by_rule](015-quantities.md) | leitura | Implementada (não validada no Revit) |
| 016 | [phases_report, set_element_phases](016-phases.md) | leitura + escrita | Implementada (não validada no Revit) |
| 017 | [export_sheets_pdf](017-export-sheets-pdf.md) | arquivo | Implementada (não validada no Revit) |
| 018 | [renumber_elements](018-renumber-elements.md) | escrita | Implementada (não validada no Revit) |
| 019 | [import_parameters_from_csv](019-import-parameters-csv.md) | escrita | Implementada (não validada no Revit) |
| 020 | [create_floors_from_rooms, create_ceilings_from_rooms](020-floors-ceilings-from-rooms.md) | escrita | Implementada (não validada no Revit) |

Status: **Proposta** → **Em desenvolvimento** → **Implementada (não validada no Revit)** → **Validada no Revit <ano>**.

## Template

Copie para `docs/specs/NNN-nome-da-tool.md`:

```markdown
# NNN — nome_da_tool

- **Status:** Proposta
- **Tipo:** leitura | escrita (diálogo de aprovação: "<texto do action>")
- **Rota:** `GET|POST /nome_da_rota/` em `revit_mcp/<modulo>.py`
- **Wrapper:** `tools/<modulo>_tools.py`

## Problema
Qual dor do usuário resolve, em uma ou duas frases.

## Parâmetros da tool
| Nome | Tipo | Padrão | Descrição |

## Requisição para a rota
JSON de exemplo.

## Resposta
JSON de exemplo (sucesso e erro).

## Regras e casos de borda
- Sem documento aberto → 503.
- ...

## Revit API
Classes e métodos usados; diferenças entre versões do Revit.

## Testes
- Unitário (wrapper): endpoint e payload.
- Integração (Windows + Revit): ...

## Validação manual no Revit
- [ ] ...
```
