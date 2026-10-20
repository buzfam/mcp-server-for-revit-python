# HANDOFF — buzfam/mcp-server-for-revit-python

Documento de passagem de bastão entre sessões (pessoas ou agentes). Atualize
ao fim de cada sessão de trabalho: o que mudou, o que falta, o que está travado.

_Última atualização: 2026-10-20_

## Estado atual

- Fork sincronizado com o upstream em `6ca46b3` (merge do PR #69 upstream).
- 22 tools: as 20 do upstream + `get_selected_elements` (001) e
  `get_element_parameters` (002), mescladas no `master`. **Não validadas no Revit.**
- Spec 005 (fábrica: hub central + agentes nos PCs) escrita; fase 1 não iniciada.
- Guias de agente criados: `AGENTS.md`, `CLAUDE.md`, `FORK.md`, specs 001–004,
  skills `add-mcp-tool`, `sync-upstream` e `session-handoff`.
- Testes (macOS): `108 passed, 9 skipped`; manifest confere 22 tools.

## Próximos passos

1. **Fábrica, fase 1** (spec 005): extrair `_revit_call` para módulo
   reutilizável, agente mínimo e hub mínimo só leitura (`--insecure-dev`).
2. **003 `bulk_set_parameters`** — spec 003; usa 002 para ler valores antes/depois.
3. **004 `model_health_report`** — spec 004.
4. Validar 001–004 num Windows com Revit (checklist em cada spec), quando o
   usuário instalar no PC. Ponto de maior risco: `element_id_from_int`
   (overload Int64/Int32 do construtor de `ElementId` no IronPython).

## Decisões tomadas

| Data | Decisão | Motivo |
|---|---|---|
| 2026-10-20 | Mudanças do fork não vão para o upstream | Liberdade de escopo; upstream mira o Autodesk Marketplace |
| 2026-10-20 | Autosave e plugins sem LLM ficam em `buzfam/pyrevit-cvarq-plugins` | MCP só reage a pedidos; autosave silencioso não combina com o diálogo de aprovação |
| 2026-10-20 | Priorizar tools de escrita em lote | O MCP nativo do Revit 2027 só lê o modelo |
| 2026-10-20 | Sem `Co-Authored-By` de IA nos commits | Autor único: Carlos Felipe Gomes |
| 2026-10-20 | Rotas de leitura com parâmetros usam POST + JSON | Padrão do repositório (`/current_view_elements/`); nenhuma rota usa query string |
| 2026-10-20 | Campo de ID nas respostas: `element_id` | Consistência com as rotas existentes |
| 2026-10-20 | Fábrica = hub central + agente por PC (conexão de saída); ponte com o Revit segue loopback | Não expor execução de código na rede; token DPAPI não sai do PC; escala para N PCs |
| 2026-10-20 | Fábrica: estações de trabalho, aprovação de escrita no hub, diálogo no Revit mantido, hub numa VM | Decisão do usuário |

## Pendências e riscos

- **Sem Revit no Mac**: tudo que roda em `revit_mcp/` precisa de validação no
  Windows. Nada foi validado no Revit até agora pelo fork.
- O remote `origin` aponta para o fork pessoal `karlipegomes`; o `master` local
  rastreia `buzfam/master`, destino dos pushes.
- Fábrica: confirmar no Windows como o pyRevit numera as portas de vários Revits no mesmo PC.

## Ambiente

- Clone local: `~/Documents/StaffOps/Contributing/mcp-server-for-revit-python`
- Remotes: `buzfam` (destino), `origin` (pessoal), `upstream` (adicionar quando sincronizar)
- Projeto irmão: `~/Documents/StaffOps/Contributing/pyrevit-cvarq-plugins`
