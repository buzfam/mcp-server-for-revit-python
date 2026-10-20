# 003 — bulk_set_parameters

- **Status:** Proposta
- **Tipo:** escrita (diálogo: `"Set element parameters"`); `dry_run` é leitura
- **Rota:** `POST /bulk_set_parameters/` em `revit_mcp/parameters.py` (módulo novo)
- **Wrapper:** `tools/parameter_tools.py` (módulo novo)
- **Depende de:** 002 (o LLM descobre nomes e tipos antes de escrever)

## Problema
A dor nº 1 da pesquisa: atualizar parâmetros e tabelas à mão, elemento por
elemento. Com esta tool o usuário cola uma planilha (ou descreve a regra) e o
LLM aplica tudo de uma vez, com **um único diálogo de aprovação**.

## Parâmetros da tool
| Nome | Tipo | Padrão | Descrição |
|---|---|---|---|
| `changes` | list[{element_id:int, parameter:str, value:str\|int\|float\|bool}] | — | Alterações (1–500) |
| `dry_run` | bool | True | Só valida e mostra antes/depois, sem alterar |
| `use_type` | bool | False | Aplicar no tipo do elemento em vez da instância |

`dry_run` é **True por padrão**: o LLM deve mostrar o plano ao usuário e só
então chamar com `dry_run=False`.

## Requisição para a rota
```json
{
  "dry_run": false,
  "use_type": false,
  "changes": [
    {"element_id": 123456, "parameter": "Mark", "value": "P01"},
    {"element_id": 123457, "parameter": "Comments", "value": "Revisar"},
    {"element_id": 123458, "parameter": "Sill Height", "value": "110 cm"}
  ]
}
```

## Resposta
```json
{
  "status": "success",
  "dry_run": false,
  "applied": 3,
  "results": [
    {"index": 0, "element_id": 123456, "parameter": "Mark",
     "before": "P1", "after": "P01", "ok": true}
  ],
  "errors": []
}
```
Com erro de validação (nada é aplicado):
```json
{"status": "error", "applied": 0, "errors": [
  {"index": 1, "element_id": 123457, "parameter": "Comments", "error": "Parameter is read-only"}
]}
```

## Regras e casos de borda
- **Tudo ou nada**: primeiro valida todas as alterações; se houver qualquer
  erro, não abre transação e devolve `errors`. Só então uma única
  `DB.Transaction(doc, "MCP: bulk set parameters")`.
- Se `SetValueString`/`Set` falhar durante a transação → `t.RollBack()` e erro 500
  com o índice que falhou.
- Conversão por `StorageType`:
  - `String` → `p.Set(str(value))`
  - `Integer` → `int`; Sim/Não aceita `true/false`, `"sim"/"não"`, `"yes"/"no"`, `1/0`
  - `Double` → texto com unidade (`"110 cm"`, `"2.40"`) via `p.SetValueString(text)`
    (usa as unidades do projeto); número puro também via `SetValueString(str(value))`
  - `ElementId` → **não suportado na v1** (erro claro)
- `dry_run` → `authorize_request(request, action=None)` (sem diálogo); senão
  `action="Set element parameters"` (diálogo mostra o payload completo).
- Limite: 500 alterações por chamada → 400 acima disso.
- Parâmetro ambíguo (dois com o mesmo nome no elemento) → erro pedindo
  desambiguação; v2 pode aceitar `builtin` ou GUID.
- Elementos em grupos ou de vínculos podem falhar no `Set`: reportar o erro do Revit.
- Workshared: elemento emprestado por outro usuário falha no commit → erro claro.

## Revit API
- `el.LookupParameter(name)` (e `GetParameters(name)` para detectar ambiguidade)
- `p.IsReadOnly`, `p.StorageType`, `p.Set(...)`, `p.SetValueString(...)`
- `DB.Transaction`, `t.Start()`, `t.Commit()`, `t.RollBack()`
- `WorksharingUtils.GetCheckoutStatus` (opcional para pré-validação)

## Testes
- Unitário: wrapper envia `dry_run=True` por padrão; >500 alterações é
  rejeitado no wrapper; payload sem chaves `None`.
- Integração: `dry_run` em `tests/test.rvt` não altera `doc.IsModified`;
  aplicação real de `Comments` num elemento e leitura de volta com 002.

## Validação manual no Revit
- [ ] `dry_run` mostra antes/depois e não marca o documento como modificado
- [ ] 100 `Mark` de portas aplicados com um diálogo só; Ctrl+Z desfaz tudo de uma vez
- [ ] Um item read-only no meio → nada aplicado, erro com índice
- [ ] `"110 cm"` num projeto em metros e num em pés
- [ ] Sim/Não com `true` e `"não"`
- [ ] Usuário recusa o diálogo → 403, nada aplicado
