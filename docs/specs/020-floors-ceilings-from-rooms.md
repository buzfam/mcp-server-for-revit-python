# 020 — create_floors_from_rooms, create_ceilings_from_rooms

- **Status:** Implementada (não validada no Revit)
- **Tipo:** escrita (diálogos: `"Create floors from rooms"`, `"Create ceilings from rooms"`); `dry_run` é leitura
- **Rotas:** `POST /create_floors_from_rooms/`, `POST /create_ceilings_from_rooms/` em `revit_mcp/finish_elements.py` (módulo novo)
- **Wrapper:** `tools/finish_element_tools.py` (módulo novo)
- **Depende de:** `room_finishes` / `quantities_by_rule` (spec 015), que já preferem pisos e forros modelados; reaproveita só `run_batch` (e utilitários públicos) de `revit_mcp/modeling.py` (spec 011) e `collect_hosts` / `_hosts_in_room` de `revit_mcp/quantities.py`. Os demais auxiliares são cópias locais; um teste chama as funções importadas com os argumentos exatos usados aqui, para que uma mudança de assinatura quebre o teste.

## Problema
A proposta (R4) observa que piso e forro modelados medem melhor que o contorno
do ambiente: a área do elemento já desconta pilares e ilhas, e o forro modelado
dá a altura usada nas áreas de parede. Modelar um piso de acabamento e um forro
por ambiente, à mão, é repetitivo. Estas tools desenham um piso (ou forro) por
ambiente sobre o contorno do próprio ambiente.

## Convenções
- **Unidades:** metros (`offset_m`, `height_m`) e m² (`area_m2`); o Revit trabalha em pés.
- **Seleção de ambientes:** `room_numbers` (texto; pode combinar com `level`),
  `room_ids`, ou só `level` (todos os ambientes colocados no nível). Pelo menos
  um dos três; `room_numbers` e `room_ids` não se combinam. Máximo 200
  ambientes por chamada (acima disso → 400 pedindo para filtrar).
- **`dry_run` padrão `true`:** mesmo fluxo da spec 011 (`TransactionGroup` +
  `Transaction`, `Commit` para o Revit processar falhas, `RollBack` do grupo).
  A área de cada elemento (`HOST_AREA_COMPUTED`) é lida depois de `Regenerate`,
  por isso aparece também no `dry_run`. Ids removidos do resultado no `dry_run`.
- **Execução real:** `authorize_request(..., action="Create floors from rooms")`
  (ou `"Create ceilings from rooms"`) só depois de validar e resolver tudo;
  `Assimilate()` → uma etapa de desfazer. Antes do diálogo, a rota acrescenta
  ao payload `_count` (quantos elementos serão criados) e `_rooms` (números dos
  ambientes, até 50), para o diálogo mostrar o que vai ser criado.
- **`expected_count`** (opcional): a contagem do `dry_run`. Se a seleção mudou
  e a contagem atual for diferente → 409, sem diálogo.
- **Fases:** cada elemento novo recebe `CreatedPhaseId` = fase do ambiente
  (`ROOM_PHASE`) quando `ArePhasesModifiable()` e `IsPhaseCreatedValid(id)`;
  `phase` no resultado é a fase do ambiente e `phase_note` aparece se o Revit
  não permitiu.
- **Pulados x tudo ou nada:** antes de abrir transação, cada ambiente que não
  pode receber o elemento vai para `skipped` com `reason`; os demais são criados
  tudo ou nada (erro do Revit em qualquer um → nada é criado, com o número do
  ambiente no erro). Se todos forem pulados → `status: "nothing_to_create"`,
  sem diálogo de aprovação.

## Parâmetros das tools
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `room_numbers` | list[str] | — | Números dos ambientes |
| `room_ids` | list[int] | — | Ids dos ambientes (em vez de números) |
| `level` | str | — | Nome do nível; sozinho, seleciona todos os ambientes do nível |
| `floor_type` / `ceiling_type` | str | tipo padrão do modelo (senão o primeiro por nome; lajes de fundação excluídas) | Nome do tipo |
| `offset_m` (piso) | float | 0 | Deslocamento de altura em relação ao nível (−5 a 5) |
| `height_m` (forro) | float | topo do ambiente (ver regras) | Altura do forro acima do nível (0 a 10). Recomendado informar |
| `boundary_location` | `"finish"` \| `"center"` | `"finish"` | Contorno na face de acabamento ou no eixo das paredes |
| `skip_existing` | bool | true | Pular ambientes que já têm piso/forro |
| `expected_count` | int | — | Contagem do `dry_run`; 409 se mudou |
| `dry_run` | bool | true | Só valida e mostra o plano |

## Requisição para a rota
```json
{"dry_run": true, "level": "Térreo", "room_numbers": ["101", "102"],
 "floor_type": "Porcelanato 1cm", "offset_m": 0.0,
 "boundary_location": "finish", "skip_existing": true}
```
```json
{"dry_run": false, "room_ids": [123456], "ceiling_type": "Forro gesso", "height_m": 2.6}
```

## Resposta
```json
{"status": "dry_run", "created": 0, "type": "Porcelanato 1cm", "boundary_location": "finish",
 "message": "Dry run: Revit accepted 1 floors; 1 rooms skipped. ...",
 "total_area_m2": 18.4,
 "results": [{"index": 0, "room_id": 123456, "number": "101", "name": "Sala", "level": "Térreo",
              "phase": "Nova construção", "type": "Porcelanato 1cm", "loops": 1, "room_area_m2": 18.52, "offset_m": 0.0,
              "area_m2": 18.4}],
 "skipped": [{"room_id": 123457, "number": "102", "name": "Cozinha", "level": "Térreo",
              "reason": "existing_floor", "existing_ids": [222]}],
 "warnings": []}
```
Execução real: `status: "success"`, `created`, `element_ids` e `element_id` em
cada resultado. Forro: `height_m` e `height_source` (`"height_m"` ou
`"room_top"`) em vez de `offset_m`. `phase` (fase do ambiente) e, se for o
caso, `phase_note`. `repaired_segments` aparece quando o
contorno precisou de reparo. `numbers_not_found` lista números sem ambiente.
Erro do Revit: `{"status": "error", "created": 0, "errors": [{"index", "error", "number", ...}]}`.

Motivos em `skipped`: `not_found`, `not_a_room`, `other_level` (só com
`room_ids` + `level`), `unplaced`, `not_enclosed`, `existing_floor` /
`existing_ceiling`, `invalid_boundary` (com `detail`), `no_ceiling_height`
(com `detail`).

## Regras e casos de borda
- Sem documento aberto → 503. Formato inválido, tipo inexistente (com
  `available_types`), mais de 200 ambientes ou Revit sem `Floor.Create` /
  `Ceiling.Create` (anterior a 2022) → 400.
- **Contorno:** `Room.GetBoundarySegments` com `SpatialElementBoundaryOptions`
  (`Finish` ou `Center`). O primeiro laço é o contorno externo; os demais são
  furos (pilares, ilhas) e vão todos para o `Create`, que os trata como
  aberturas. Segmentos com menos de 1 mm são descartados; a folga deixada (até
  10 mm) é fechada movendo a ponta de um segmento reto vizinho. Folga maior,
  ou entre dois arcos, ou laço aberto → `invalid_boundary`. Se a API tiver
  `BoundaryValidation.IsValidHorizontalBoundary`, ela também é consultada.
- **Elemento existente:** mesmo critério de `room_finishes`: piso/forro do mesmo
  nível cujo centro da caixa envolvente está no ambiente. Elemento maior que a
  área do ambiente × 1,05 + 0,5 m² (por exemplo, a laje estrutural sob vários
  ambientes) **não** conta como existente. Só contam elementos que existem na
  fase do ambiente: criados nessa fase ou antes, e não demolidos nessa fase ou
  antes (posição em `doc.Phases`). `skip_existing=false` cria mesmo assim.
- **Ambientes sobrepostos em fases diferentes** (reforma: o mesmo espaço como
  ambiente existente e como ambiente novo): cada um é tratado na sua fase. O
  piso existente pula o ambiente da fase existente, mas não o da fase nova se
  for demolido nela. Podem surgir dois pisos no mesmo lugar, um por fase; isso
  é esperado, e a vista filtrada por fase mostra cada um.
- **Altura do forro:** `height_m` vale para todos os ambientes e é medida a
  partir do nível (como `CEILING_HEIGHTABOVELEVEL_PARAM`). Sem ela, o padrão
  só vale quando o limite superior do ambiente (`ROOM_UPPER_LEVEL`) é o
  próprio nível: altura = deslocamento inferior (`ROOM_LOWER_OFFSET`) +
  `UnboundedHeight` (que é medida a partir da base do ambiente), e só se
  ficar entre 0 e 6 m. Nos outros casos o ambiente é pulado com
  `no_ceiling_height` pedindo `height_m`.
- **Piso:** criado no nível do ambiente, com `FLOOR_HEIGHTABOVELEVEL_PARAM = offset_m`.
- **Forro:** `CEILING_HEIGHTABOVELEVEL_PARAM = height_m`.
- `Parameter.Set` que devolve `false` (piso ou forro) → erro do item, nada é criado.
- Avisos do Revit (por exemplo, sobreposição com a laje) voltam em `warnings`,
  sem diálogo.

## Revit API
- `Floor.Create(doc, IList<CurveLoop>, floorTypeId, levelId)` e
  `Ceiling.Create(doc, IList<CurveLoop>, ceilingTypeId, levelId)` (Revit 2022+).
- `Room.GetBoundarySegments`, `SpatialElementBoundaryOptions`,
  `SpatialElementBoundaryLocation.Finish/Center`, `CurveLoop.Append/IsOpen`,
  `Line.CreateBound`, `BoundaryValidation.IsValidHorizontalBoundary` (se existir).
- `Document.GetDefaultElementTypeId(ElementTypeGroup.FloorType/CeilingType)`,
  `FloorType.IsFoundationSlab`.
- `Element.ArePhasesModifiable`, `IsPhaseCreatedValid`, `CreatedPhaseId`,
  `DemolishedPhaseId`, `Document.Phases`.
- `BuiltInParameter.ROOM_PHASE`, `ROOM_UPPER_LEVEL`, `ROOM_LOWER_OFFSET`,
  `FLOOR_HEIGHTABOVELEVEL_PARAM`, `CEILING_HEIGHTABOVELEVEL_PARAM`,
  `HOST_AREA_COMPUTED`, `ROOM_NUMBER`, `ROOM_NAME`, `SYMBOL_NAME_PARAM`.
- `TransactionGroup`, `Transaction`, `IFailuresPreprocessor` (via `modeling.run_batch`).

## Testes
- Unitário (wrapper): `tests/unit/test_finish_element_tools.py`: endpoint,
  payload sem `None`, validação local.
- Unitário (lógica da rota, API falsa): `tests/unit/test_finish_element_route_logic.py`:
  validação, reparo de contorno, seleção e motivos de pulo, elemento existente
  e laje grande, fase do elemento e existentes por fase, topo do ambiente,
  `_rooms`/`_count` no diálogo, `expected_count` (409), `Set` recusado,
  `dry_run` com rollback, aprovação só na execução real, erro do Revit desfaz
  tudo, e assinaturas de `run_batch` / `collect_hosts` / `_hosts_in_room`.
- Integração (Windows + Revit): pendente.

## Validação manual no Revit
- [ ] `create_floors_from_rooms(level="Térreo")` em `dry_run`: áreas próximas às dos ambientes, nada criado, sem diálogo.
- [ ] Execução real: um diálogo de aprovação, um Ctrl+Z desfaz todos os pisos.
- [ ] Piso fica no nível + `offset_m` (não na cota das curvas do contorno).
- [ ] Ambiente com pilar no meio: piso com furo; `room_finishes` passa a usar `modeled_elements`.
- [ ] Ambiente com parede curva: contorno com arco aceito.
- [ ] Laje estrutural sob todo o pavimento não faz pular os ambientes; piso de acabamento já existente faz.
- [ ] `boundary_location="center"` usa o eixo das paredes.
- [ ] `create_ceilings_from_rooms(height_m=2.6)`: forro na altura certa; `room_finishes` mostra `height.source = "ceiling_height"`.
- [ ] Sem `height_m`: ambiente com limite superior no próprio nível e deslocamento inferior 0,10 m → forro em 0,10 + altura; ambiente com limite superior no nível de cima → pulado com `no_ceiling_height`.
- [ ] Ambiente na fase "Existente": o piso novo fica com "Fase de criação" = Existente (`phase` no resultado).
- [ ] Reforma: piso existente demolido na fase nova não pula o ambiente da fase nova.
- [ ] Diálogo de aprovação mostra `_count` e `_rooms`; `expected_count` diferente → 409 sem diálogo.
- [ ] Nomes de tipo em português (Revit 2024 pt-BR) encontrados sem diferenciar maiúsculas.
- [ ] Avisos (sobreposição de pisos) voltam em `warnings`, sem diálogo bloqueando.
