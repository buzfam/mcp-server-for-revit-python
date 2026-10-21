# 002 — get_element_parameters

- **Status:** Validada parcialmente no Revit (2026-10-20, Revit 2024 + pyRevit 7.0)
- **Tipo:** leitura (`authorize_request(request, action=None)`)
- **Rota:** `POST /element_parameters/` em `revit_mcp/selection.py`
- **Wrapper:** `tools/selection_tools.py`

## Problema
Para editar parâmetros (003) ou responder perguntas sobre elementos, o LLM
precisa ver nomes exatos, valores, unidades e se o parâmetro é editável.

## Parâmetros da tool
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `element_ids` | list[int] \| None | None | IDs; se vazio, usa a seleção atual |
| `include_type` | bool | False | Incluir também os parâmetros do tipo |
| `name_filter` | str \| None | None | Só parâmetros cujo nome contém o texto (sem diferenciar maiúsculas) |

## Requisição para a rota
```json
{"element_ids": [123456, 123457], "include_type": true, "name_filter": "fire"}
```

## Resposta
```json
{
  "status": "success",
  "elements": [
    {
      "element_id": 123456, "name": "80x210", "category": "Doors", "type_id": 9876,
      "instance": [
        {"name": "Mark", "value": "P01", "display": "P01", "storage": "String",
         "read_only": false, "shared": false, "builtin": "ALL_MODEL_MARK"}
      ],
      "type": [
        {"name": "Width", "value": 0.2624, "display": "80 cm", "storage": "Double",
         "read_only": false, "shared": false, "builtin": "DOOR_WIDTH"}
      ]
    }
  ],
  "not_found": [999]
}
```
- `value`: valor bruto (Double em unidades internas, pés); `display`: `AsValueString()`
  nas unidades do projeto. O LLM deve **ler `display`** e **escrever com texto
  em unidades do projeto** (ver 003).
- `storage`: `String` | `Integer` | `Double` | `ElementId` | `None`.
- `ElementId`: `value` = id numérico; `display` = nome do elemento referenciado.

## Regras e casos de borda
- Máximo 50 elementos por chamada → 400 acima disso (proteger o contexto do LLM).
- Sem `element_ids` e seleção vazia → 400 `{"error": "No element_ids and nothing selected"}`.
- IDs inexistentes vão para `not_found`, não derrubam a chamada.
- `builtin`: nome do `BuiltInParameter` quando houver, senão `null`.
- Parâmetros com o mesmo nome (ex.: compartilhado + projeto) aparecem os dois.

## Revit API
- IDs recebidos como `int` viram `ElementId` via `element_id_from_int`
  (`revit_mcp/utils.py`): construtor Int64 no Revit 2024+, Int32 nos anteriores.
- `el.Parameters` (instância), `doc.GetElement(el.GetTypeId()).Parameters` (tipo)
- `p.Definition.Name`, `p.StorageType`, `p.IsReadOnly`, `p.IsShared`,
  `p.AsString()`, `p.AsInteger()`, `p.AsDouble()`, `p.AsElementId()`, `p.AsValueString()`
- `p.Definition.BuiltInParameter` (via `InternalDefinition`)

## Testes
- Unitário (`tests/unit/test_selection_tools.py`): payload com e sem `element_ids`;
  argumentos `None` não são enviados; mais de 50 IDs é rejeitado no wrapper.
- Integração: ler parâmetros de um elemento do `tests/test.rvt`.

## Validação manual no Revit
- [x] Parede: instância + tipo, `display` nas unidades do projeto
- [ ] Porta com parâmetro compartilhado
- [x] Sem IDs + seleção de 2 elementos
- [ ] 51 IDs → 400
