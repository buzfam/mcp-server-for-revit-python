# 002 — get_element_parameters

- **Status:** Proposta
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
      "id": 123456, "category": "Doors", "type_id": 9876,
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
- `el.Parameters` (instância), `doc.GetElement(el.GetTypeId()).Parameters` (tipo)
- `p.Definition.Name`, `p.StorageType`, `p.IsReadOnly`, `p.IsShared`,
  `p.AsString()`, `p.AsInteger()`, `p.AsDouble()`, `p.AsElementId()`, `p.AsValueString()`
- `p.Definition.BuiltInParameter` (via `InternalDefinition`)

## Testes
- Unitário: payload enviado com e sem `element_ids`; `None` não é enviado como `null`
  (ver `tests/unit/test_null_defaults.py`).
- Integração: ler parâmetros de um elemento do `tests/test.rvt`.

## Validação manual no Revit
- [ ] Parede: instância + tipo, `display` nas unidades do projeto
- [ ] Porta com parâmetro compartilhado
- [ ] Sem IDs + seleção de 2 elementos
- [ ] 51 IDs → 400
