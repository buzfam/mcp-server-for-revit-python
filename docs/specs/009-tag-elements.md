# 009 — tag_elements

- **Status:** Implementada (não validada no Revit)
- **Tipo:** escrita (diálogo: `"Tag elements in a view"`); `dry_run` é leitura
- **Rota:** `POST /tag_elements/` em `revit_mcp/annotation.py` (módulo novo)
- **Wrapper:** `tools/annotation_tools.py` (módulo novo)

## Problema
Etiquetar à mão todas as portas, janelas ou ambientes de uma planta é
repetitivo e fácil de esquecer um elemento. Com esta tool o usuário pede
"etiquete todas as portas desta planta" e o LLM cria as etiquetas de uma vez,
com **um único diálogo de aprovação** e um único Ctrl+Z.

## Parâmetros da tool
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `category` | str | — | Categoria: nome no idioma do Revit ("Portas"), em inglês ("Doors") ou da API ("OST_Doors") |
| `view_name` | str | vista ativa | Vista onde etiquetar |
| `view_id` | int | vista ativa | Alternativa a `view_name` (não use os dois) |
| `tag_type` | str | padrão da categoria | Tipo de etiqueta: "Tipo", "Família" ou "Família: Tipo" |
| `add_leader` | bool | False | Linha de chamada (ignorado em etiquetas de ambiente/área) |
| `skip_tagged` | bool | True | Pular elementos que já têm etiqueta nessa vista |
| `limit` | int | 200 | Máximo de elementos a etiquetar (1–1000) |
| `dry_run` | bool | True | Só testa e conta, sem alterar o modelo |

## Requisição para a rota
```json
{
  "category": "Portas",
  "view_name": "Térreo",
  "tag_type": "Etiqueta de porta: Padrão",
  "add_leader": false,
  "skip_tagged": true,
  "limit": 200,
  "dry_run": true
}
```

## Resposta
```json
{
  "status": "success",
  "view": {"id": 312, "name": "Térreo"},
  "category": "Portas",
  "tag_type": "Etiqueta de porta: Padrão",
  "found": 42,
  "tagged": 38,
  "skipped": {"already_tagged": 3},
  "failed_count": 1,
  "failed": [{"element_id": 123456, "error": "..."}],
  "truncated": false,
  "tag_ids": [987001, 987002]
}
```
Com `dry_run=true`, `status` é `"dry_run"`, há uma `message` e não há
`tag_ids` (as etiquetas foram desfeitas). Erros de entrada (categoria ou vista
inexistente, vista que não aceita etiquetas, nenhum tipo de etiqueta carregado,
`tag_type` inexistente com a lista `available`) voltam como 400.

## Regras e casos de borda
- Sem documento aberto → 503.
- `authorize_request(action=None)` primeiro; o diálogo de aprovação
  (`"Tag elements in a view"`) só aparece quando `dry_run=false` e há algo a
  etiquetar.
- Uma transação (`"MCP: tag elements"`) por requisição. O dry run cria as
  etiquetas de verdade e faz `RollBack`, então as contagens refletem o que o
  Revit aceita.
- **Sucesso parcial:** um elemento que o Revit recusa vai para `failed` e os
  outros são etiquetados (diferente do `bulk_set_parameters`, que é tudo ou
  nada). Se nenhum for etiquetado, a transação é desfeita.
- Vistas aceitas: plantas (piso, forro, área, estrutural), cortes, elevações,
  detalhes e 3D (o 3D precisa estar travado; senão o Revit recusa e o
  elemento cai em `failed`). Templates e tabelas → 400.
- Ambientes: `NewRoomTag` no ponto de locação do ambiente; ambientes não
  posicionados ou não fechados são pulados (`skipped.not_placed`).
- Áreas: `NewAreaTag`, só em planta de área.
- Demais categorias: `IndependentTag.Create` no ponto de locação, no meio da
  curva de locação ou no centro da caixa envolvente.
- Tipo de etiqueta: o pedido pelo nome; senão o padrão do projeto para a
  categoria de etiqueta (`GetDefaultFamilyTypeId`); senão o primeiro carregado.
  Se a categoria não tem etiqueta própria carregada, usa etiqueta multicategoria.
- `failed` lista no máximo 50 itens; `failed_count` tem o total.

## Revit API
- `DB.IndependentTag.Create(doc, tagTypeId, viewId, Reference, addLeader,
  TagOrientation.Horizontal, XYZ)` — sobrecarga com tipo existe desde o
  Revit 2019.
- `doc.Create.NewRoomTag(LinkElementId, UV, viewId)`,
  `doc.Create.NewAreaTag(ViewPlan, Area, UV)`, `Element.ChangeTypeId`.
- `Document.GetDefaultFamilyTypeId(ElementId(BuiltInCategory))`.
- Etiquetas já existentes: `IndependentTag.GetTaggedLocalElementIds()`
  (Revit 2022+, com fallback para `TaggedLocalElementId`),
  `RoomTag.TaggedLocalRoomId`, `AreaTag.Area`.
- Categoria da etiqueta derivada do `BuiltInCategory` (`OST_Doors` →
  `OST_DoorTags`), com exceções em `TAG_CATEGORY_OVERRIDES`.

## Testes
- Unitário (wrapper): `tests/unit/test_annotation_export_tools.py` — endpoint,
  payload padrão, campos opcionais omitidos, validação sem chamar o Revit.
- Unitário (rota, API falsa): `tests/unit/test_annotation_export_route_logic.py`
  — categorias de etiqueta, nome do tipo, filtro de candidatos, commit,
  rollback no dry run, sucesso parcial, commit recusado.

## Validação manual no Revit
- [ ] Dry run em planta com portas: contagens corretas e nenhuma etiqueta criada.
- [ ] Execução real: diálogo único, etiquetas criadas, um único Ctrl+Z.
- [ ] Segunda execução com `skip_tagged=true` não duplica etiquetas.
- [ ] Ambientes (planta) e áreas (planta de área) com nome em português.
- [ ] `tag_type` por nome em português; tipo inexistente devolve `available`.
- [ ] Categoria sem etiqueta carregada → erro claro.
- [ ] Revit 2024 e uma versão 2025+ (`GetTaggedLocalElementIds`, `ElementId` Int64).
