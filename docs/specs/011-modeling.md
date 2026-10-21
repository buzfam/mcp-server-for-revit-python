# 011 — create_walls, create_rooms, place_hosted_families

- **Status:** Implementada (não validada no Revit)
- **Tipo:** escrita (diálogos: `"Create walls"`, `"Create rooms"`, `"Place doors/windows in walls"`); `dry_run` é leitura
- **Rotas:** `POST /create_walls/`, `POST /create_rooms/`, `POST /place_hosted_families/` em `revit_mcp/modeling.py` (módulo novo)
- **Wrapper:** `tools/modeling_tools.py` (módulo novo)
- **Depende de:** `list_levels` e `list_families` (o LLM descobre níveis e famílias antes)

## Problema
Numa sessão real o agente desenhou uma planta (12 paredes, aberturas de
portas/janelas, 8 ambientes com etiquetas) escrevendo IronPython ad hoc em
`execute_revit_code`. Isso funciona, mas o diálogo de aprovação mostra código,
difícil de revisar, e cada erro vira depuração de script. Com tools dedicadas o
diálogo mostra um payload legível (coordenadas, nomes, tipos), o agente não
escreve código e o lote inteiro é validado pelo próprio Revit no `dry_run`.

## Convenções comuns às três tools
- **Unidades:** metros (coordenadas, alturas, offsets) e m² (áreas). A rota
  converte para pés (`m / 0.3048`); áreas voltam como `ft² × 0.09290304`.
- **Sistema de coordenadas:** coordenadas internas do projeto (origem interna;
  X para leste/direita, Y para norte/cima numa planta sem rotação). Pontos são
  listas `[x, y]` de dois números.
- **`dry_run` padrão `true`:** valida tudo, executa o lote dentro de uma
  `Transaction` envolvida por uma `TransactionGroup`, faz `Commit` da transação
  (o Revit roda o processamento de falhas de verdade) e depois `RollBack` do
  grupo. Assim o `dry_run` pega erros que só o Revit detecta (ambiente não
  fechado, família que não corta a parede, paredes sobrepostas). Nada persiste
  e não há diálogo.
- **Execução real:** `authorize_request(..., action=<verbo>)` mostra o lote no
  diálogo; mesma `TransactionGroup` + `Transaction`, com `Assimilate()` no fim:
  **uma etapa de desfazer** (Ctrl+Z desfaz o lote inteiro).
- **Tudo ou nada:** primeiro resolve todos os itens (níveis, tipos, famílias,
  paredes) sem abrir transação; qualquer erro → nada é criado. Erro do Revit
  durante a transação → `RollBack` de tudo.
- **Avisos e erros do Revit:** um `IFailuresPreprocessor` coleta os avisos
  (`FailureSeverity.Warning`), apaga-os para que nenhum diálogo bloqueie o lote
  e devolve-os em `warnings`; qualquer erro → `ProceedWithRollBack` e o texto
  do erro em `errors`.
- **Limite:** 1–200 itens por chamada (400 acima disso; o wrapper também recusa).
- **Nomes informados pelo usuário** (nível, tipo de parede, família, tipo) são
  comparados sem diferenciar maiúsculas/minúsculas nem espaços repetidos. O
  Revit roda em português: nada é comparado com nomes de exibição em inglês;
  categorias por `BuiltInCategory` e parâmetros por `BuiltInParameter`.
- Erros de formato → HTTP 400 com `errors: [{index, error}]`. Erros por item
  (nome não encontrado, Revit recusou) → HTTP 200 com
  `{"status": "error", "created": 0, "errors": [{index, error}]}`.
- No `dry_run`, os ids (`element_id`, `tag_id`, `separation_line_ids`) são
  removidos do resultado, porque os elementos foram desfeitos.

## create_walls

### Parâmetros da tool
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `walls` | list[{start:[x,y], end:[x,y], ...}] | — | 1–200 paredes retas, em metros |
| `level` | str | nível mais baixo | Nível base |
| `height_m` | float | 2.8 | Altura desconectada (ignorada com `top_level`) |
| `top_level` | str | — | Nível de topo (restrição superior) em vez de altura |
| `wall_type` | str | tipo padrão do modelo | Nome do tipo de parede |
| `structural` | bool | False | Parede estrutural |
| `location_line` | str | — (padrão do Revit: eixo) | `wall_centerline`, `core_centerline`, `finish_exterior`, `finish_interior`, `core_exterior`, `core_interior` |
| `join` | bool | True | Deixar o Revit unir as extremidades |
| `dry_run` | bool | True | Só valida |

Cada item pode sobrepor `level`, `height_m`, `top_level`, `wall_type`,
`structural`, `location_line` e `join`. `height_m` ou `top_level` do próprio
item vencem os dois valores do lote.

### Requisição para a rota
```json
{"dry_run": true, "level": "Térreo", "height_m": 2.8, "structural": false, "join": true,
 "walls": [{"start": [0, 0], "end": [8, 0]},
           {"start": [8, 0], "end": [8, 6], "wall_type": "Tijolo 15"}]}
```

### Resposta
```json
{"status": "success", "created": 2, "element_ids": [345001, 345002],
 "results": [{"index": 0, "element_id": 345001, "length_m": 8.0, "level": "Térreo",
              "wall_type": "Genérico - 200mm", "height_m": 2.8}],
 "warnings": []}
```

### Regras
- `Wall.Create(doc, Line, wallTypeId, levelId, alturaPés, 0, False, structural)`;
  a linha fica na elevação do nível em coordenadas internas
  (`Level.ProjectElevation`, helper `level_z`). `Level.Elevation` é medido a
  partir da base de elevação (Ponto Base do Projeto ou Ponto de Levantamento) e
  só é usado para ordenar níveis e comparar base/topo.
- `location_line`: grava `WALL_KEY_REF_PARAM` (0–5) e reatribui
  `wall.Location.Curve = linha`, para que a referência escolhida fique sobre a
  linha pedida (mudar o parâmetro sozinho não move a parede).
- `top_level`: grava `WALL_HEIGHT_TYPE`; precisa estar acima do nível base.
- `join=false`: `WallUtils.DisallowWallJoinAtEnd(wall, 0/1)`.
- Parede com menos de 0,01 m → 400.

## create_rooms

### Parâmetros da tool
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `rooms` | list[{point \| boundary, name?, number?, level?, tag?}] | — | 1–200 ambientes |
| `level` | str | nível mais baixo | Nível dos ambientes sem nível próprio |
| `tag` | bool | True | Etiquetar na vista ativa |
| `dry_run` | bool | True | Só valida (mostra a área real) |

- `{"point": [x, y]}`: ambiente na região fechada (paredes ou linhas
  separadoras) que contém o ponto.
- `{"boundary": [[x, y], ...]}`: cria linhas separadoras de ambiente no
  polígono fechado (3+ pontos, sem repetir o primeiro) e coloca o ambiente no
  centróide; para polígono côncavo cujo centróide cai fora, informe `point`
  dentro dele (senão 400).

### Requisição para a rota
```json
{"dry_run": true, "level": "Térreo", "tag": true,
 "rooms": [{"point": [2.5, 2.0], "name": "Sala", "number": "01"},
           {"boundary": [[8, 0], [11, 0], [11, 3], [8, 3]], "name": "Varanda"}]}
```

### Resposta
```json
{"status": "dry_run", "message": "Dry run: Revit accepted all 2 rooms. ...",
 "created": 0,
 "results": [{"index": 0, "level": "Térreo", "point": [2.5, 2.0],
              "area_m2": 17.64, "name": "Sala", "number": "01"}],
 "warnings": []}
```

### Regras
- `doc.Create.NewRoom(level, UV)`; nome e número via
  `room.get_Parameter(BuiltInParameter.ROOM_NAME/ROOM_NUMBER).Set(...)`
  (`room.Name` dá `AttributeError` no IronPython).
- Fronteira: `SketchPlane.Create(doc, Plane.CreateByNormalAndOrigin(BasisZ, (0,0,level_z)))`
  + `doc.Create.NewRoomBoundaryLines(sketch, CurveArray, vista)`; a vista é a
  ativa se for planta de piso (`ViewType.FloorPlan`, não forro/área/estrutural,
  não modelo) do nível, senão a primeira planta de piso do nível; sem planta de
  piso do nível → erro do item. `GetRoomAtPoint` também usa `level_z`.
- Depois de `doc.Regenerate()`, `room.Area == 0` → erro do item ("não está numa
  região fechada"), e o lote todo é desfeito. Área informada em m² com 2 casas.
- Já existe ambiente no ponto (`doc.GetRoomAtPoint`) → erro do item.
- Etiqueta: `doc.Create.NewRoomTag(LinkElementId(room.Id), UV, view.Id)` só se a
  vista ativa for uma planta de piso (não modelo) gerada pelo nível do ambiente;
  senão o resultado traz `"tag": "skipped: ..."` (não é erro). **Decisão:** se o
  Revit recusar a etiqueta, o ambiente é criado mesmo assim e o resultado traz
  `"tag": "failed: <mensagem>"` — a etiqueta é conveniência e não derruba o lote.

## place_hosted_families

### Parâmetros da tool
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `instances` | list[{family, type?, wall_id? , point?, offset_m?, sill_height_m?, flip_hand?, flip_facing?, level?}] | — | 1–200 instâncias |
| `dry_run` | bool | True | Só valida |

Posição:
- `wall_id` + `offset_m`: distância a partir do início da linha de localização da parede;
- `point`: a parede mais próxima cuja linha de localização está a até 0,5 m
  (filtrada por `level`, se informado); a instância vai para o ponto mais
  próximo sobre ela (`curve.Project`). Sem `level`, se paredes de **mais de um
  nível** estiverem à mesma distância (paredes empilhadas em pavimentos), é erro
  do item pedindo `level` ou `wall_id` (não escolhe um pavimento ao acaso);
- `wall_id` + `point`: aquela parede, ponto mais próximo (até 0,5 m).

### Requisição para a rota
```json
{"dry_run": true,
 "instances": [{"family": "Porta de giro", "type": "80x210", "wall_id": 345001, "offset_m": 1.2},
               {"family": "Janela de correr", "point": [8.0, 3.0], "sill_height_m": 1.1}]}
```

### Resposta
```json
{"status": "success", "created": 2, "element_ids": [345101, 345102],
 "results": [{"index": 1, "element_id": 345102, "family": "Janela de correr",
              "type": "120x100", "wall_id": 345002, "offset_m": 3.0,
              "point": [8.0, 3.0], "distance_to_wall_m": 0.0, "sill_height_m": 1.1}],
 "warnings": []}
```
Sem família de porta/janela carregada (era o caso do modelo de teste):
```json
{"status": "error", "created": 0,
 "errors": [{"index": 0, "error": "Family 'Porta' is not loaded. No door or window families are loaded in this model. Load one via Insert > Load Family ..."}],
 "loaded_door_window_families": [],
 "hint": "No door or window families are loaded in this model. Load one via Insert > Load Family ..."}
```

### Regras
- Família/tipo: primeiro entre `FamilySymbol` de `OST_Doors`/`OST_Windows`,
  depois entre qualquer `FamilySymbol` (outras famílias hospedadas em parede).
  Nomes por `SYMBOL_FAMILY_NAME_PARAM` e `SYMBOL_NAME_PARAM`. Sem `type` → o
  primeiro tipo em ordem alfabética. Família não encontrada → erro listando as
  famílias de porta/janela carregadas.
- `symbol.Activate()` se não estiver ativo; `doc.Create.NewFamilyInstance(XYZ,
  symbol, wall, level, StructuralType.NonStructural)` com o nível base da parede.
- `instance.Host is None` → "família não é hospedada em parede" (erro do item).
- `sill_height_m` → `INSTANCE_SILL_HEIGHT_PARAM`; sem parâmetro editável → erro.
  Sem `sill_height_m` nada é gravado (a família mantém o peitoril padrão); o
  resultado traz o `sill_height_m` que o Revit aplicou, para que um peitoril 0
  inesperado fique visível. O ponto passado ao Revit está na base da parede
  (Z da linha de localização). O resultado traz também o `level` da parede.
- `flip_hand`/`flip_facing` → `flipHand()`/`flipFacing()` se `CanFlipHand`/`CanFlipFacing`.
- `offset_m` maior que o comprimento da parede → erro do item. Abertura que não
  cabe na parede é recusada pelo Revit → rollback com o texto do erro.

## Revit API
- `Wall.Create`, `WallUtils.DisallowWallJoinAtEnd`, `WallType`,
  `Document.GetDefaultElementTypeId(ElementTypeGroup.WallType)`,
  `BuiltInParameter.WALL_KEY_REF_PARAM`, `WALL_HEIGHT_TYPE`
- `Document.Create.NewRoom(Level, UV)`, `NewRoomBoundaryLines`, `NewRoomTag(LinkElementId, UV, ElementId)`,
  `SketchPlane.Create`, `Plane.CreateByNormalAndOrigin`, `CurveArray`,
  `Document.GetRoomAtPoint`, `Room.Area`, `ROOM_NAME`, `ROOM_NUMBER`, `ViewPlan.GenLevel`
- `Document.Create.NewFamilyInstance(XYZ, FamilySymbol, Element host, Level, StructuralType)`,
  `FamilySymbol.Activate`, `Curve.Project`, `Curve.Evaluate`, `Curve.Tessellate`,
  `INSTANCE_SILL_HEIGHT_PARAM`, `FamilyInstance.flipHand/flipFacing`
- `TransactionGroup` (`Assimilate`/`RollBack`), `Transaction`,
  `FailureHandlingOptions.SetFailuresPreprocessor`, `IFailuresPreprocessor`,
  `FailuresAccessor.DeleteWarning`, `FailureProcessingResult.ProceedWithRollBack`
- Ids com `element_id_from_int`/`element_id_value` (Revit 2024–2026).

## Testes
- Unitário (wrapper, `tests/unit/test_modeling_tools.py`): endpoints, payload
  padrão (`dry_run=True`, sem chaves `None`), `timeout=120.0`, validação de
  formato sem chamar o Revit, limite de 200.
- Unitário (rota com Revit simulado, `tests/unit/test_modeling_route_logic.py`):
  centróide/área/ponto-no-polígono, metros→pés, parede mais próxima, escolha de
  família/tipo, precedência de padrões, validação de itens, `dry_run` desfaz o
  grupo e remove ids, commit assimila (1 desfazer), aviso coletado, erro do
  Revit faz rollback, ambiente não fechado é erro do item, sem família de
  porta/janela → mensagem com Insert > Load Family, diálogo só no modo real.
- Integração (Windows + Revit): ainda não escrito.

## Validação manual no Revit
- [ ] `create_walls` com `dry_run`: não marca o documento como modificado; avisos aparecem em `warnings`
- [ ] 12 paredes de uma planta com um diálogo só; Ctrl+Z desfaz todas de uma vez
- [ ] `location_line=finish_interior`: face interna sobre a linha pedida
- [ ] `top_level` com dois níveis
- [ ] `create_rooms` por ponto em região fechada: área correta em m²; nome/número em português aparecem
- [ ] Ponto fora de região fechada → erro do item e nada criado (sem diálogo de aviso travando)
- [ ] `boundary` cria linhas separadoras + ambiente; etiqueta só em planta do nível
- [ ] `place_hosted_families` sem família carregada → mensagem com Insert > Load Family
- [ ] Porta por `wall_id` + `offset_m` e janela por `point` com `sill_height_m`
- [ ] Abertura maior que a parede → erro do Revit vira rollback, sem diálogo travado
- [ ] Usuário recusa o diálogo → 403, nada criado
- [ ] Modelo com Elevação Base = Ponto de Levantamento e cota ≠ 0: paredes, linhas separadoras e ambientes ficam no nível certo (`ProjectElevation`)
- [ ] Janela sem `sill_height_m` mantém o peitoril padrão da família (conferir o `sill_height_m` devolvido)
- [ ] `point` sem `level` em projeto com paredes empilhadas em dois pavimentos → erro pedindo `level`/`wall_id`
- [ ] Vista ativa = planta de forro do nível: etiqueta do ambiente é pulada e separadoras vão para a planta de piso
- [ ] Etiqueta de ambiente recusada pelo Revit → ambiente criado, `tag: failed: ...`
