# Specs

Uma spec por tool (ou grupo pequeno de tools relacionadas), escrita **antes**
do código. A implementação segue a skill `add-mcp-tool`.

## Índice

| # | Spec | Tipo | Status |
|---|---|---|---|
| 001 | [get_selected_elements](001-get-selected-elements.md) | leitura | Implementada (não validada no Revit) |
| 002 | [get_element_parameters](002-get-element-parameters.md) | leitura | Implementada (não validada no Revit) |
| 003 | [bulk_set_parameters](003-bulk-set-parameters.md) | escrita | Proposta |
| 004 | [model_health_report](004-model-health-report.md) | leitura | Proposta |
| 005 | [Fábrica: hub + agentes](005-fleet-hub.md) | arquitetura | Proposta (adiada; primeiro a 006) |
| 006 | [Acesso pela rede (direto)](006-network-access.md) | transporte | Implementada (não validada no Revit) |

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
