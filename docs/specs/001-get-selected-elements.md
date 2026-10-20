# 001 — get_selected_elements

- **Status:** Proposta
- **Tipo:** leitura (`authorize_request(request, action=None)`)
- **Rota:** `GET /selection/` em `revit_mcp/selection.py` (módulo novo)
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
`GET /selection/?limit=200` (via `revit_get("/selection/", ctx, params={"limit": limit})`)

## Resposta
```json
{
  "status": "success",
  "document": "Projeto.rvt",
  "total": 3,
  "returned": 3,
  "truncated": false,
  "elements": [
    {"id": 123456, "category": "Doors", "family": "Porta Simples", "type": "80x210",
     "name": "80x210", "level": "Térreo", "view_specific": false}
  ]
}
```
Seleção vazia: `"total": 0, "elements": []` com status 200 (não é erro).

## Regras e casos de borda
- Sem documento ou `uidoc` → 503 `{"error": "No active Revit document"}`.
- `id` via `element_id_value` (`revit_mcp/utils.py`), que trata `ElementId.Value`
  (2024+) e `IntegerValue` (anteriores).
- `family`/`type` vazios quando o elemento não for `FamilyInstance` ou não tiver tipo.
- `level`: `doc.GetElement(el.LevelId).Name` se `LevelId` válido; senão `null`.
- `limit` fora de 1–1000 → 400.
- `category` pode ser `null` (alguns elementos internos não têm categoria).

## Revit API
- `uidoc.Selection.GetElementIds()`
- `doc.GetElement(id)`, `el.Category.Name`, `el.GetTypeId()`, `el.LevelId`,
  `el.ViewSpecific`, `FamilyInstance.Symbol.Family.Name`
- Handler recebe `uidoc` injetado pelo pyRevit Routes (como em `views.py`).

## Testes
- Unitário: wrapper chama `/selection/` com `params={"limit": 200}`; `limit`
  inválido não chega na rota se validado no wrapper.
- Integração: cobre a seleção vazia (`total == 0`). Para seleção não vazia,
  o teste pode definir a seleção com `uidoc.Selection.SetElementIds(...)` via
  `execute_revit_code` antes de chamar a tool.

## Validação manual no Revit
- [ ] Seleção vazia → `total: 0`
- [ ] 3 portas selecionadas → 3 elementos com família/tipo/nível corretos
- [ ] Seleção com 1500 elementos e `limit=200` → `truncated: true`, `returned: 200`
- [ ] Revit 2024+ e uma versão anterior (IDs corretos)
