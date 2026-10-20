# Changelog

Mudanças do fork `buzfam/mcp-server-for-revit-python`. O formato segue
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/). Mudanças herdadas
do upstream aparecem só como "Sincronizado com upstream `<sha>`".

## [Unreleased]

### Adicionado
- Tool `get_selected_elements` (spec 001): elementos selecionados pelo usuário,
  com categoria, família, tipo e nível. Rota `POST /selection/`.
- Tool `get_element_parameters` (spec 002): parâmetros de instância e tipo com
  valor bruto, valor exibido, tipo de armazenamento e se é editável. Rota
  `POST /element_parameters/`.
- `element_id_from_int` em `revit_mcp/utils.py` (construtor de `ElementId` por versão do Revit).
- Guias para agentes de IA: `AGENTS.md`, `CLAUDE.md` e skills em `.claude/skills/`
  (`add-mcp-tool`, `sync-upstream`, `session-handoff`).
- `FORK.md` (propósito e roadmap do fork), `HANDOFF.md` e este `CHANGELOG.md`.
- Specs em `docs/specs/`: 001 `get_selected_elements`, 002 `get_element_parameters`,
  003 `bulk_set_parameters`, 004 `model_health_report`, 005 fábrica (hub + agentes).

### Alterado
- `CLAUDE.md` deixa de ser ignorado pelo `.gitignore`.
- README com aviso de fork apontando para `FORK.md`.

### Base
- Fork criado a partir do upstream `6ca46b3`.
