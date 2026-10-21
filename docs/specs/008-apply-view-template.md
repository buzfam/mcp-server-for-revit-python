# 008 — apply_view_template

- **Status:** Implementada (não validada no Revit)
- **Tipo:** escrita (diálogo: `"Apply view template"`); `dry_run` é leitura
- **Rota:** `POST /apply_view_template/` em `revit_mcp/sheets.py`
- **Wrapper:** `tools/sheet_tools.py`
- **Depende de:** `list_revit_views` (nomes das vistas)

## Problema
Padronizar vistas é trabalho de clique: abrir as propriedades de cada planta e
escolher o modelo de vista. Com esta tool o LLM aplica um modelo a dezenas de
vistas de uma vez (por nome/ID ou por tipo de vista), mostrando antes qual
modelo cada vista tinha.

## Parâmetros da tool
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `template` | str | — | Nome do modelo de vista (sem diferenciar maiúsculas) |
| `views` | list[str \| int] \| null | null | Nomes ou IDs das vistas (1–200). Use isto **ou** `view_type` |
| `view_type` | str \| null | null | Nome do `ViewType` do Revit (`"FloorPlan"`, `"CeilingPlan"`, `"Section"`, `"Elevation"`, `"ThreeD"`...). Todas as vistas desse tipo que não são modelos (até 200) |
| `dry_run` | bool | True | Só valida e mostra antes/depois, sem alterar |

## Requisição para a rota
```json
{"dry_run": true, "template": "Planta - Executivo", "views": ["Térreo", 123456]}
```
```json
{"dry_run": false, "template": "Planta - Executivo", "view_type": "FloorPlan"}
```

## Resposta
```json
{"status": "success", "applied": 2,
 "template": {"name": "Planta - Executivo", "id": 777},
 "results": [{"index": 0, "view": "Térreo", "view_id": 2222, "view_type": "FloorPlan",
              "before": null, "after": "Planta - Executivo"},
             {"index": 1, "view": "Cobertura", "view_id": 123456, "view_type": "FloorPlan",
              "before": "Planta - Estudo", "after": "Planta - Executivo"}]}
```
Com erro por item (nada é aplicado, HTTP 200):
```json
{"status": "error", "applied": 0, "error": "1 of 2 views are invalid; nothing was applied",
 "errors": [{"index": 1, "view": "Forro",
             "error": "Template cannot be applied to this view (view type mismatch)"}]}
```

## Decisões de implementação (2026-10-21)
- Autentica sem diálogo; com `dry_run=false` chama
  `authorize_request(..., action="Apply view template")`.
- Exatamente um de `views` ou `view_type` (os dois ou nenhum → 400).
- `view_type` compara com `str(view.ViewType)` sem diferenciar maiúsculas
  (nome da API, igual em qualquer idioma). Mais de 200 vistas do tipo → erro
  pedindo lotes explícitos; nenhuma → erro.
- Erros por item: vista não encontrada, nome ambíguo (pede o ID), a vista é um
  modelo de vista, vista repetida no lote, `View.IsValidViewTemplate(template.Id)`
  falso (tipo de vista diferente). Com qualquer erro nada é aplicado.
- Aplica com `view.ViewTemplateId = template.Id` numa única
  `DB.Transaction(doc, "MCP: apply view template")`, desfeita no `dry_run`.
  `before`/`after` = nome do modelo (ou `null`).
- Modelo de vista com nome repetido → erro.

## Regras e casos de borda
- Sem documento aberto → 503.
- Vista que já usa o modelo: aparece com `before == after` (não é erro).
- Vistas dependentes herdam do modelo da vista principal: o Revit pode recusar → erro do item, nada aplicado.

## Revit API
- `View.IsTemplate`, `View.ViewType`, `View.ViewTemplateId`, `View.IsValidViewTemplate`
- `FilteredElementCollector.OfClass(View)`, `DB.Transaction`

## Testes
- Unitário (wrapper): payload por `views` e por `view_type`, sem chaves `None`,
  validação de exclusividade e limites.
- Unitário (rota): antes/depois, `dry_run` desfaz, erros por item, ordem da autorização.
- Integração (Windows + Revit): pendente.

## Validação manual no Revit
- [ ] `dry_run` mostra antes/depois e não marca o documento como modificado
- [ ] Modelo aplicado a todas as plantas de piso com um diálogo só; Ctrl+Z desfaz
- [ ] Modelo de planta numa vista de forro → erro, nada aplicado
- [ ] Nome de modelo com acento
