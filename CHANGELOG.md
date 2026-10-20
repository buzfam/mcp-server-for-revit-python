# Changelog

Mudanças do fork `buzfam/mcp-server-for-revit-python`. O formato segue
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/). Mudanças herdadas
do upstream aparecem só como "Sincronizado com upstream `<sha>`".

## [Unreleased]

### Adicionado
- Guias para agentes de IA: `AGENTS.md`, `CLAUDE.md` e skills em `.claude/skills/`
  (`add-mcp-tool`, `sync-upstream`, `session-handoff`).
- `FORK.md` (propósito e roadmap do fork), `HANDOFF.md` e este `CHANGELOG.md`.
- Specs em `docs/specs/`: 001 `get_selected_elements`, 002 `get_element_parameters`,
  003 `bulk_set_parameters`, 004 `model_health_report`.

### Alterado
- `CLAUDE.md` deixa de ser ignorado pelo `.gitignore`.
- README com aviso de fork apontando para `FORK.md`.

### Base
- Fork criado a partir do upstream `6ca46b3`.
