# 015 — room_finishes e quantities_by_rule

- **Status:** Implementada (não validada no Revit)
- **Tipo:** leitura (as duas; sem transação, sem diálogo de aprovação)
- **Rotas:** `POST /room_finishes/` e `POST /quantities_by_rule/` em `revit_mcp/quantities.py` (módulo novo)
- **Wrapper:** `tools/quantity_tools.py` (módulo novo)
- **Origem:** especificação CVARQ §12 (quantitativos e conferência de orçamentos), proposta item **R4** (P1)

## Problema
O escritório recebe orçamentos de engenharia (material + mão de obra) e precisa
conferir os quantitativos contra o projeto. Hoje isso é feito à mão, tabela por
tabela. Pior: quando os números divergem, ninguém sabe se a diferença é de
modelo, de critério (desconto de vãos, perdas, arredondamento) ou de escopo.

Princípio da §12.3: **nenhum número sai sem a regra de medição que o gerou.**
Toda resposta devolve, no topo, o bloco `rules` com as regras aplicadas e um
texto de critério em pt-BR para cada grandeza; cada valor traz o seu `source`.
Assim a comparação com a engenharia (§12.4: serviço, unidade, quantitativo,
critério) é rastreável.

## Cobertura dos grupos da §12.2

| Grupo (§12.2) | Agora | Depois |
|---|---|---|
| Alvenaria | `quantities_by_rule` grupo `alvenaria`: por tipo de parede, comprimento (m), área bruta e líquida (m², regra de vãos) e volume (m³) | Separar por material/camada; descontar encontros de paredes |
| Revestimentos | Área de piso por ambiente (`room_finishes.floor`) e área de parede por ambiente (`walls.net_m2`, base para revestimento de parede); pisos por tipo (`pisos`) | **Paginação** e recortes (dependem de modelagem de peças/partes): depois |
| Pintura | `room_finishes.painting` e grupo `pintura` (paredes líquidas + forro/teto) | Distinguir faces de paredes externas; tinta por material |
| Forros | Área por ambiente (`room_finishes.ceiling`) e por tipo (`forros`) | Sancas, tabicas e recortes |
| Rodapés | Comprimento por ambiente (`room_finishes.skirting`) e total (`rodapes`) | Por material (precisa de parâmetro de acabamento no ambiente) |
| Esquadrias | Grupo `esquadrias`: quantidade por família, tipo e dimensões, largura × altura e área | Ferragens, vidros, peitoris |
| Louças e metais | Grupo `loucas_metais`: quantidade por família/tipo de Peças hidrossanitárias (+ Equipamentos hidráulicos opcional) | Metais sem família própria (registros, torneiras embutidas) |
| Marcenaria | — | **Depois**: depende do nível de modelagem do escritório (famílias de mobiliário/marcenaria) |
| Instalações | — | **Depois**: pontos e componentes por disciplina, com critérios da disciplina |

`quantities_snapshot`/`quantities_diff` (histórico entre versões, §12.6, R10) e a
importação de orçamentos (§12.5, MCP SINAPI/planilhas) ficam para specs próprias.

## Regras de medição (entrada `rules`, comum às duas tools)

> **Os padrões abaixo são provisórios e precisam ser confirmados com o
> escritório** (ficha de critérios da skill `quantitativos-regras-medicao`)
> antes de qualquer comparação com orçamento. Se a engenharia usa outro
> critério, passe a regra dela em `rules` para comparar maçã com maçã.

| Regra | Padrão | Valores | Efeito |
|---|---|---|---|
| `deduct_openings` | `"over_threshold"` | `"over_threshold"`, `"excess_over_threshold"`, `"all"`, `"none"` | Quais vãos de portas/janelas saem da área de parede, e quanto |
| `deduct_openings_over_m2` | `2.0` | número ≥ 0 | Limiar dos dois modos com limiar. `over_threshold`: vão com área **acima** do limiar é descontado inteiro (vão de exatamente 2,00 m² não é descontado). `excess_over_threshold`: desconta só **o que excede** o limiar em cada vão (vão de 3,50 m² com limiar 2,00 desconta 1,50 m²), à moda de SINAPI/TCPO |
| `skirting_subtract_doors` | `true` | bool | Rodapé = contorno com parede menos a largura das portas |
| `wall_height_m` | `null` | 0 < h ≤ 20 | Altura fixa das paredes de todos os ambientes; sem ela: altura do forro modelado, senão altura do ambiente |
| `use_modeled_elements` | `true` | bool | Usar pisos/forros modelados dentro do ambiente; `false` usa sempre a área do ambiente |
| `rounding` | `2` | 0–4 | Casas decimais, aplicadas só no valor final (somas usam valores sem arredondar) |
| `waste_percent` | `0` | 0–100 | Perdas: nunca alteram `quantity`; aparecem à parte em `quantity_with_waste` (não aplicadas a `un`) |

O padrão continua `over_threshold`; **qual modo e qual limiar o escritório
usa ainda precisa ser confirmado** (o `excess_over_threshold` existe para
comparar com orçamentos que seguem SINAPI/TCPO).

A resposta ecoa `rules.applied` (valores finais), `rules.criteria` (texto pt-BR
por grandeza: `paredes`, `rodape`, `piso`, `forro`, `pintura`, `alvenaria`,
`esquadrias`, `loucas_metais`, `arredondamento`, `perdas`, `unidades` e `fase`),
`rules.units` e `rules.phase` (nome da fase medida). Cada vão de
`room_finishes` traz `deducted` e `deducted_m2`.

### Fase medida (entrada `phase`, comum às duas tools)

`phase` (nome ou id; padrão: a **última** fase do projeto) define o que entra:

- **Ambientes:** só os de `ROOM_PHASE` igual à fase (ambiente sem fase legível
  entra); os de outras fases contam em `skipped.other_phase`.
- **Demais elementos** (portas/janelas, paredes, pisos, forros, peças
  sanitárias): só os com `Element.GetPhaseStatus(fase)` = `New` ou `Existing`.
  Demolidos, temporários e futuros ficam de fora; se o status não puder ser
  lido, o elemento entra.
- `rules.criteria.fase`: "ambientes da fase X; demais elementos novos e
  existentes na fase X; demolidos excluídos". Fase inexistente → 404 com a
  lista de fases; formato inválido → 400.

### Como cada grandeza é medida

- **Contorno do ambiente:** `Room.GetBoundarySegments` com
  `SpatialElementBoundaryLocation.Finish` (face de acabamento). Trechos que são
  linhas separadoras de ambiente (`CurveElement`) **não** contam como parede,
  nem para pintura nem para rodapé (aparecem em `notes`). Trechos de **parede
  cortina** (`WallType.Kind == Curtain`) também não: vão para
  `walls.curtain_length_m`, com nota. Pilares delimitadores contam como parede.
- **Altura:** `rules.wall_height_m` → altura do forro modelado de maior área no
  ambiente (`CEILING_HEIGHTABOVELEVEL_PARAM`, mesmo nível do ambiente) →
  `Room.UnboundedHeight`. `height.source` diz qual: `rule:wall_height_m`,
  `ceiling_height`, `room_unbounded_height` ou `unknown`.
- **Parede bruta** = comprimento do contorno com parede × altura. **Líquida** =
  bruta − vãos descontados pela regra (nunca negativa).
- **Vãos do ambiente:** portas e janelas cujo `FromRoom`/`ToRoom` **na fase do
  ambiente** (`ROOM_PHASE`) é o ambiente. Uma porta entre dois ambientes conta
  nos dois (cada face é pintada). Vão cujo `FromRoom`/`ToRoom` dá erro conta em
  `notes` ("N vãos sem ambiente legível"). Tamanho: `DOOR_WIDTH/DOOR_HEIGHT` ou
  `WINDOW_WIDTH/WINDOW_HEIGHT` → `FAMILY_WIDTH_PARAM/FAMILY_HEIGHT_PARAM` →
  `FAMILY_ROUGH_WIDTH_PARAM/FAMILY_ROUGH_HEIGHT_PARAM`, cada um na instância e
  depois no tipo; `size_source` diz de onde veio. Vão sem tamanho legível nunca
  é descontado.
- **Piso/forro:** soma dos pisos/forros do mesmo nível cujo centro (caixa
  envolvente) está no ambiente (`Room.IsPointInRoom`, testado 1 pé acima do
  ponto de localização do ambiente — coordenadas internas —, ou acima de
  `Level.ProjectElevation` se o ambiente não tiver ponto; centros fora da caixa
  envolvente do ambiente nem são testados). **Pisos estruturais**
  (`FLOOR_PARAM_IS_STRUCTURAL = 1`, lajes) não são piso de acabamento: ficam
  fora da busca por ambiente e do grupo `pisos` (contados em `notes`). Se não há elemento, ou a
  soma passa de 105% da área do ambiente + 0,5 m² (laje que atravessa vários
  ambientes), usa a área do ambiente e explica em `note`. `source`:
  `modeled_elements` ou `room_area`.
- **Pintura** = paredes líquidas + área de forro/teto do ambiente.
- **Alvenaria (por tipo):** comprimento = `CURVE_ELEM_LENGTH` (linha de
  localização; encontros podem contar em dobro); altura =
  `WALL_USER_HEIGHT_PARAM`; área de uma face; vãos = portas/janelas hospedadas
  na parede, pela mesma regra; volume = `HOST_VOLUME_COMPUTED` (o Revit já
  desconta todas as aberturas). Paredes cortina e paredes no local ficam de fora
  (`notes`). **Parede empilhada** é medida uma vez: as subparedes
  (`IsStackedWallMember`) são puladas (contadas em `notes`), os vãos hospedados
  nelas vão para a empilhada e, se a empilhada não tiver volume, soma-se o das
  subparedes (`GetStackedWallMemberIds`).
- **Pisos/forros por tipo:** `HOST_AREA_COMPUTED`.

## Parâmetros da tool

### room_finishes
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `level` | str | todos | Só ambientes desse nível |
| `room_numbers` | list[str] | todos | Só esses números de ambiente |
| `phase` | str \| int | última fase | Fase medida (nome ou id) |
| `rules` | dict | padrões acima | Regras de medição |
| `limit` | int | 200 | Máximo de ambientes (1–1000) |

### quantities_by_rule
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `groups` | list[str] | todos | `esquadrias`, `loucas_metais`, `alvenaria`, `pisos`, `forros`, `rodapes`, `pintura` |
| `level` | str | todos | Só elementos/ambientes desse nível |
| `include_plumbing_equipment` | bool | False | Contar também Equipamentos hidráulicos em `loucas_metais` |
| `phase` | str \| int | última fase | Fase medida (nome ou id) |
| `rules` | dict | padrões acima | Regras de medição |
| `limit` | int | 200 | Máximo de linhas (1–1000) |
| `format` | str | `"json"` | `"json"` ou `"csv"` |

## Requisição para a rota
```json
{"level": "Térreo", "room_numbers": ["101"], "phase": "Construção nova",
 "rules": {"deduct_openings_over_m2": 2.0}, "limit": 200}
```
```json
{"groups": ["esquadrias", "pintura"], "include_plumbing_equipment": false,
 "rules": {"waste_percent": 10}, "limit": 200, "format": "csv"}
```

## Resposta
`room_finishes`:
```json
{
  "status": "success",
  "rules": {
    "applied": {"deduct_openings": "over_threshold", "deduct_openings_over_m2": 2.0,
                "skirting_subtract_doors": true, "wall_height_m": null,
                "use_modeled_elements": true, "rounding": 2, "waste_percent": 0.0},
    "criteria": {"paredes": "área de parede = comprimento do contorno ... vãos: descontados só os vãos com área acima de 2.00 m² ...",
                 "rodape": "rodapé = comprimento do contorno com parede menos a largura das portas do ambiente", "...": "..."},
    "units": {"length": "m", "area": "m2", "volume": "m3", "count": "un"}
  },
  "total": 1, "returned": 1, "truncated": false,
  "skipped": {"unplaced": 0, "not_enclosed": 0},
  "totals_of_returned": {"floor_m2": 12.0, "ceiling_m2": 12.0, "walls_gross_m2": 35.0,
                         "walls_net_m2": 32.6, "skirting_m": 13.2, "painting_m2": 44.6, "perimeter_m": 14.0},
  "rooms": [{
    "element_id": 1001, "number": "101", "name": "Sala", "level": "Térreo", "phase": "Nova construção",
    "area_m2": 12.0, "perimeter_m": 14.0,
    "height": {"value_m": 2.5, "source": "ceiling_height"},
    "floor": {"area_m2": 12.0, "source": "room_area", "element_ids": [], "note": "nenhum elemento modelado no ambiente"},
    "ceiling": {"area_m2": 12.0, "source": "modeled_elements", "element_ids": [2002], "note": null},
    "walls": {"boundary_length_m": 14.0, "height_m": 2.5, "gross_m2": 35.0, "net_m2": 32.6,
              "deducted_m2": 2.4, "openings_deducted": 1, "openings_not_deducted": 1,
              "source": "boundary_length x height - openings by rule"},
    "skirting": {"length_m": 13.2, "doors_subtracted_m": 0.8, "source": "wall boundary length - door widths"},
    "painting": {"area_m2": 44.6, "source": "walls.net_m2 + ceiling.area_m2"},
    "openings": [{"element_id": 3003, "kind": "door", "family": "Porta", "type": "80x210",
                  "width_m": 0.8, "height_m": 2.1, "area_m2": 1.68,
                  "size_source": "width=type:DOOR_WIDTH; height=type:DOOR_HEIGHT", "deducted": false}],
    "notes": []
  }]
}
```
Filtro `room_numbers` com número inexistente → `numbers_not_found`. Ambiente
que falha na medição → `failed`.

`quantities_by_rule` (JSON): mesmo bloco `rules`, mais `groups`, `total`,
`returned`, `truncated`, `notes` e `lines`:
```json
{"group": "esquadrias", "item": "Porta - Porta de giro: 80x210", "measure": "count", "unit": "un",
 "quantity": 6, "rule": "contagem por família, tipo e dimensões; ...",
 "width_m": 0.8, "height_m": 2.1, "area_m2": 10.08, "size_source": "width=type:DOOR_WIDTH; height=type:DOOR_HEIGHT"}
```
```json
{"group": "pintura", "item": "Pintura de paredes", "measure": "area", "unit": "m2",
 "quantity": 210.4, "quantity_with_waste": 231.44, "rule": "área de parede = ...", "count": 12}
```
Com `format="csv"`, `lines` dá lugar a `csv` (colunas `group, item, measure,
unit, quantity, quantity_with_waste, rule`). Erros de entrada → 400 (`rules`,
`groups`, `limit`, `format`); sem documento → 503.

## Regras e casos de borda
- Só ambientes posicionados e fechados são medidos; os outros entram em `skipped`.
- Altura desconhecida → paredes e pintura `null` no ambiente, com nota pedindo
  `rules.wall_height_m`; no total, `rooms_without_height`.
- Fase: a de `phase` (padrão: a última); vãos pelo `FromRoom`/`ToRoom` na fase
  do ambiente (que é a medida). Demolidos nunca entram.
- `quantities_by_rule` só lê o que os grupos pedidos usam (sem `pisos`, não
  coleta pisos; sem `forros`/`rodapes`/`pintura`, não coleta forros).
- `room_finishes` totaliza só os ambientes devolvidos (`totals_of_returned`);
  `quantities_by_rule` (rodapes/pintura) mede todos os ambientes do filtro.
- Respostas sem `json.dumps` de texto acentuado no IronPython; textos via
  `normalize_string`.

## Revit API
`Room.GetBoundarySegments` + `SpatialElementBoundaryOptions` (`Finish`),
`BoundarySegment.ElementId/GetCurve`, `Room.IsPointInRoom`, `Room.UnboundedHeight`,
`FamilyInstance.get_FromRoom/get_ToRoom(Phase)`, `FamilyInstance.Host`,
`BuiltInParameter` de largura/altura de portas/janelas, `HOST_AREA_COMPUTED`,
`HOST_VOLUME_COMPUTED`, `CURVE_ELEM_LENGTH`, `WALL_USER_HEIGHT_PARAM`,
`CEILING_HEIGHTABOVELEVEL_PARAM`, `WallType.Kind`, `Level.ProjectElevation`,
`Room.Location.Point`, `Element.get_BoundingBox`, `Document.Phases`,
`ROOM_PHASE`, `Element.GetPhaseStatus` (`ElementOnPhaseStatus`),
`Wall.IsStackedWallMember`, `Wall.GetStackedWallMemberIds`,
`FLOOR_PARAM_IS_STRUCTURAL`.
`OST_PlumbingEquipment` é lida por `getattr` (se não existir na versão, vira nota).

## Testes
- Unitário (wrapper, `tests/unit/test_quantity_tools.py`): endpoint, payload sem
  `None`, validação de `rules`, `groups`, `limit`, `format`, `room_numbers`.
- Unitário (lógica, `tests/unit/test_quantity_route_logic.py`): regras padrão e
  validação, desconto de vãos (limiar, todos, nenhum, ruído de ponto flutuante),
  rodapé, altura, área modelada × ambiente, arredondamento, perdas, agrupamento,
  linhas por grupo, `FromRoom/ToRoom` e contorno com linhas separadoras; modo
  `excess_over_threshold`, resolução e filtro de fase, paredes cortina no
  contorno, pré-filtro por caixa envolvente, pisos estruturais, paredes
  empilhadas, vãos sem ambiente legível e coletas puladas por grupo.
- Integração (Windows + Revit): pendente.

## Validação manual no Revit
- [ ] Ambiente retangular com 1 porta 0,80×2,10 e 1 janela 2,00×1,20: paredes líquidas = bruta − 2,40 m²; rodapé = perímetro − 0,80.
- [ ] `deduct_openings="all"` e `"none"` mudam só as paredes.
- [ ] Ambiente com forro modelado: `height.source = "ceiling_height"` e área do forro modelado.
- [ ] Laje única para vários ambientes: piso cai para `room_area` com nota.
- [ ] Linha separadora de ambiente: fora de parede e rodapé, com nota.
- [ ] Porta entre dois ambientes aparece nos dois; fase do ambiente respeitada.
- [ ] Projeto de reforma (Existente / Construção nova): sem `phase`, mede a última fase; ambientes da fase existente vão para `skipped.other_phase`; paredes/portas demolidas não entram; `rules.phase` e `rules.criteria.fase` corretos.
- [ ] `phase` por nome em português e por id; fase inexistente → 404 com a lista.
- [ ] Parede empilhada: aparece uma vez em `alvenaria` (subparedes em `notes`), volume igual à soma das subparedes, vãos das subparedes descontados.
- [ ] Ambiente com trecho de parede cortina: fora de paredes/pintura/rodapé, `walls.curtain_length_m` e nota.
- [ ] Laje estrutural sob vários ambientes + piso de acabamento: só o acabamento conta; laje em `notes` de `pisos`.
- [ ] Modelo com base point deslocado / níveis com "Base de elevação" = ponto de levantamento: pisos e forros continuam achados (ponto de teste em coordenadas internas).
- [ ] `excess_over_threshold`: janela 2,00×1,75 (3,50 m²) desconta 1,50 m² (`deducted_m2`).
- [ ] Tempo de `quantities_by_rule` num projeto grande do escritório (antes e depois do pré-filtro).
- [ ] `size_source` coerente nas famílias de porta/janela do escritório (Revit 2024 pt-BR).
- [ ] `quantities_by_rule` com `format="csv"` abre no Excel com acentos.
- [ ] Conferir os padrões da tabela de regras com o escritório (ficha `quantitativos-regras-medicao`).
