# 001 — get_selected_elements

- **Status:** Validada parcialmente no Revit (2026-10-20, Revit 2024 + pyRevit 7.0)
- **Tipo:** leitura (`authorize_request(request, action=None)`)
- **Rota:** `POST /selection/` em `revit_mcp/selection.py` (POST com corpo JSON, como `/current_view_elements/`; o repositório não usa query string)
- **Wrapper:** `tools/selection_tools.py` (módulo novo)

## Problema
O usuário seleciona elementos no Revit e quer perguntar ao LLM sobre eles
("quais são esses?", "mude o parâmetro desses"). Hoje o LLM não sabe o que
está selecionado sem escrever IronPython. É a base para 002 e 003.

## Parâmetros da tool
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `limit` | int | 200 | Máximo de elementos detalhados na resposta (1–1000) |

## Requisição para a rota
```json
{"limit": 200}
```

## Resposta
```json
{
  "status": "success",
  "document": "Projeto.rvt",
  "total": 3,
  "returned": 3,
  "truncated": false,
  "elements": [
    {"element_id": 123456, "category": "Doors", "family": "Porta Simples", "type": "80x210",
     "name": "80x210", "level": "Térreo", "view_specific": false}
  ]
}
```
Seleção vazia: `"total": 0, "elements": []` com status 200 (não é erro).

## Regras e casos de borda
- Sem documento ou `uidoc` → 503 `{"error": "No active Revit document"}`.
- `element_id` via `element_id_value` (`revit_mcp/utils.py`), que trata `ElementId.Value`
  (2024+) e `IntegerValue` (anteriores).
- `family`/`type` `null` quando o elemento não tiver tipo.
- `level`: `doc.GetElement(el.LevelId).Name` se `LevelId` válido; senão `null`.
- `limit` fora de 1–1000 → 400.
- `category` pode ser `null` (alguns elementos internos não têm categoria).

## Revit API
- `uidoc.Selection.GetElementIds()`
- `doc.GetElement(id)`, `el.Category.Name`, `el.GetTypeId()`, `el.LevelId`,
  `el.ViewSpecific`, `ElementType.FamilyName` (família de qualquer tipo, não só `FamilyInstance`)
- Handler recebe `uidoc` injetado pelo pyRevit Routes (como em `views.py`).

## Testes
- Unitário (`tests/unit/test_selection_tools.py`): wrapper chama `/selection/` com
  `{"limit": 200}`; `limit` fora de 1–1000 é rejeitado no wrapper sem chamar a rota.
- Integração: cobre a seleção vazia (`total == 0`). Para seleção não vazia,
  o teste pode definir a seleção com `uidoc.Selection.SetElementIds(...)` via
  `execute_revit_code` antes de chamar a tool.

## Validação manual no Revit
- [x] Seleção vazia → `total: 0`
- [ ] 3 portas selecionadas → 3 elementos com família/tipo/nível corretos
- [ ] Seleção com 1500 elementos e `limit=200` → `truncated: true`, `returned: 200`
- [ ] Revit 2024+ e uma versão anterior (IDs corretos)
