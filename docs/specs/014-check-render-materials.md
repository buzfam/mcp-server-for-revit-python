# 014 — check_render_materials

- **Status:** Implementada (não validada no Revit)
- **Tipo:** leitura (sem transação, sem diálogo de aprovação)
- **Rota:** `POST /check_render_materials/` em `revit_mcp/material_checks.py` (módulo novo)
- **Wrapper:** `tools/material_check_tools.py` (módulo novo)

## Problema
No escritório (Carol Vale Arquitetura, Revit 2024 em português) as imagens são
geradas no Lumion a partir de um `.dae` exportado do Revit (espec. §8.4). O
Lumion não tem API: um material mal separado ou mal nomeado só aparece depois
de exportar, e obriga a voltar ao Revit. A única automação possível é checar
**antes** da exportação (proposta, item R3).

A regra a aplicar é a "Regra permanente de materiais e texturas" (espec. §7.3):

> Cada material ou acabamento visualmente identificável deve receber uma
> nomenclatura exclusiva e estável.
>
> - Materiais distintos devem ter nomes distintos.
> - Materiais visualmente semelhantes também devem receber nomes diferentes,
>   mesmo quando parecem ter cor ou aparência próximas.
> - A nomenclatura deve ser fácil de identificar no software de renderização.
> - Os materiais precisam estar realmente separados nas faces ou nos objetos
>   correspondentes; renomear materiais sem separar a geometria não é suficiente.
> - Não se deve aplicar um único material genérico a todo o mobiliário quando há
>   vários acabamentos.
> - A separação deve permitir substituir posteriormente cada material pelo
>   acabamento final desejado.
>
> Exemplos: `POLTRONA_TECIDO_ASSENTO_01`, `POLTRONA_TECIDO_ENCOSTO_02`,
> `POLTRONA_MADEIRA_PE_03`, `POLTRONA_METAL_DETALHE_04`.

## Da regra para as checagens

| Trecho da §7.3 | Regra (`rule`) | Severidade | Como é checado |
|---|---|---|---|
| "fácil de identificar no software de renderização" + exemplos | `name_pattern` | error | Nome, sem acentos, contra a regex (padrão `^[A-Z0-9]+(_[A-Z0-9]+){2,}_[0-9]{2}$`, isto é, `OBJETO_MATERIAL_PARTE_NN` em maiúsculas) |
| "Materiais distintos devem ter nomes distintos" / "nomenclatura exclusiva" | `duplicate_names` | error (`duplicate`) / review (`similar`) | Nomes iguais ignorando maiúsculas, acentos, espaços, `_`, `-` e pontuação → `duplicate`; iguais a menos do número final → `similar` |
| "Não se deve aplicar um único material genérico a todo o mobiliário" | `generic_shared` | error | Um material usado por mais de `max_families_per_material` famílias diferentes |
| "vários acabamentos" num mesmo objeto | `single_material` | review | Tipo de família de categoria de mobiliário com 2+ peças de geometria e menos de `min_materials_per_family` materiais (pode ser intencional) |
| "realmente separados nas faces … renomear sem separar não é suficiente" | `category_material_only` | error | `GetMaterialIds(False)` vazio, ou só com o material da categoria (`Category.Material`): a geometria não foi separada |
| (complemento) | `no_material` | error | Sem material na geometria e categoria sem material (`<Por categoria>` vazio): o Lumion recebe um material padrão |

Faces pintadas com a ferramenta Pintar (`GetMaterialIds(True)`) contam como
material (chegam ao renderizador) e aparecem no campo `painted` do problema;
em `summary.painted_types` está o total de tipos com pintura. Um tipo com
pintura não cai em `category_material_only` nem em `no_material`.

As regras 1–3 olham só os materiais **usados pelos elementos checados**, não
todos os materiais do projeto.

## Parâmetros da tool
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `categories` | list[str] | mobiliário (ver abaixo) | Nomes em português, inglês ou `OST_` |
| `name_pattern` | str | `OBJETO_MATERIAL_PARTE_NN` | Regex aplicada ao nome sem acentos |
| `max_families_per_material` | int | 2 | Famílias que podem dividir um material (≥ 1) |
| `min_materials_per_family` | int | 2 | Materiais esperados por tipo de mobiliário com 2+ peças (≥ 1; 1 desliga a regra 4) |
| `only_visible_in_view` | str \| int | — | Nome ou id da vista (normalmente a 3D exportada para o Lumion); só elementos visíveis nela |
| `limit` | int | 200 | Máximo de problemas devolvidos (1–1000) |

Categorias padrão (e as únicas em que a regra `single_material` vale):
`OST_Furniture`, `OST_FurnitureSystems`, `OST_Casework`, `OST_GenericModel`,
`OST_PlumbingFixtures`, `OST_LightingFixtures`, `OST_SpecialityEquipment`.
Paredes, pisos e forros podem ser incluídos em `categories`, mas
`generic_shared` tende a marcar materiais de acabamento legítimos nelas.

## Requisição para a rota
```json
{"categories": ["Mobiliário", "OST_Casework"], "only_visible_in_view": "3D Render",
 "max_families_per_material": 2, "min_materials_per_family": 2, "limit": 200}
```

## Resposta
```json
{
  "status": "success",
  "document": "Apartamento 302",
  "categories": ["Mobiliário", "Marcenaria"],
  "view": {"id": 12345, "name": "3D Render"},
  "name_pattern": "^[A-Z0-9]+(_[A-Z0-9]+){2,}_[0-9]{2}$",
  "elements_checked": 84, "types_checked": 21, "materials_checked": 37,
  "summary": {"name_pattern": 9, "duplicate_names": 1, "generic_shared": 2,
              "single_material": 3, "category_material_only": 4, "no_material": 0,
              "painted_types": 1},
  "problems_total": 19, "returned": 19, "truncated": false,
  "problems": [
    {"rule": "category_material_only", "severity": "error", "family": "Poltrona Eames",
     "type": "Padrão", "category": "Mobiliário", "element_id": 2745,
     "material": "Mobiliário", "detail": "Only the category material reaches the geometry: ..."},
    {"rule": "generic_shared", "severity": "error", "material": "Madeira",
     "families": ["Mesa", "Rack", "Cadeira"], "detail": "Used by 3 different families (max 2): ..."},
    {"rule": "duplicate_names", "severity": "review",
     "materials": ["TECIDO_SOFA_ASSENTO_01", "TECIDO_SOFA_ASSENTO_02"], "detail": "..."}
  ],
  "method": "one representative instance per family type"
}
```
Erros: sem documento → 503; categoria inexistente, regex inválida ou número
fora da faixa → 400; vista não encontrada → 404; vista modelo → 400.

## Regras e casos de borda
- **Um representante por tipo**: os materiais são lidos da primeira instância
  encontrada de cada tipo (instâncias de um tipo têm a mesma geometria), por
  desempenho. Materiais controlados por parâmetro de **instância** podem
  variar entre instâncias e não são vistos.
- Elementos sem tipo (ex.: DirectShape) contam como um tipo cada.
- Problemas ordenados: primeiro `error`, depois `review`; cortados em `limit`
  com `truncated: true`.
- Peças de geometria = sólidos com volume (descendo em `GeometryInstance`) +
  subcomponentes compartilhados (`GetSubComponentIds`). A regra 4 nunca pede
  mais materiais do que peças: um objeto de uma peça só com um material passa.

## Limitações
- Materiais de famílias aninhadas e das faces da geometria interna da família
  **são** cobertos por `Element.GetMaterialIds(False)`. Famílias aninhadas
  **compartilhadas** são elementos próprios e aparecem como tipos próprios.
- Uma família com parte das faces em `<Por categoria>` e parte com material
  aparece só com os materiais explícitos: a parte "por categoria" não é
  detectada.
- **Semelhança visual dos ativos de aparência não é checada** (dois materiais
  com nomes diferentes e a mesma textura/cor passam). A §7.3 pede nomes
  diferentes até para materiais parecidos, e isso só a pessoa julga.
- Não confere se o nome corresponde ao objeto (ex.: `MESA_*` numa poltrona).

## Revit API
`FilteredElementCollector(doc[, view.Id]).OfCategoryId().WhereElementIsNotElementType()`,
`Element.GetMaterialIds(bool)`, `Element.GetTypeId()`, `Category.GetCategory`,
`Category.Material`, `Element.get_Geometry(Options)`, `Solid.Volume`,
`GeometryInstance.GetInstanceGeometry()`, `FamilyInstance.GetSubComponentIds()`,
`BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM` / `SYMBOL_NAME_PARAM` /
`ALL_MODEL_FAMILY_NAME`. Nada específico de versão (Revit 2024+).

## Testes
- Unitário (wrapper): `tests/unit/test_material_check_tools.py` — endpoint,
  payload sem chaves `None`, validação sem chamar o Revit.
- Unitário (lógica da rota, Revit falso): `tests/unit/test_material_check_route_logic.py`
  — acentos, regex padrão, agrupamento de nomes, cada regra, ordem e corte,
  filtro por vista e erros 400/404/503.
- Integração: não há (precisa de Windows + Revit).

## Validação manual no Revit
- [ ] Rodar no modelo de um projeto real com a vista 3D usada no Lumion.
- [ ] Família com faces em `<Por categoria>` aparece em `category_material_only`.
- [ ] Família sem material e categoria sem material aparece em `no_material`.
- [ ] Material "Madeira" em 3+ famílias aparece em `generic_shared`.
- [ ] Faces pintadas aparecem em `painted`.
- [ ] Nomes com acento (`PÉ`, `AÇO`) passam no padrão.
- [ ] Tempo de resposta aceitável num modelo grande (contagem de sólidos).
