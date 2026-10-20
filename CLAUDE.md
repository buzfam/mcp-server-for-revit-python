@AGENTS.md

## Específico do Claude Code

- Comece lendo `HANDOFF.md`. Ao terminar uma sessão de trabalho, atualize o
  `HANDOFF.md` (seção "Estado atual" e "Próximos passos") e o `CHANGELOG.md`.
- Skills do projeto (`.claude/skills/`):
  - `add-mcp-tool`: checklist completo para criar uma tool (rota + wrapper + catálogo + manifest + testes).
  - `sync-upstream`: trazer mudanças do repositório original sem perder as do fork.
- Antes de dizer que terminou: rode os testes unitários e o `--check` do
  manifest e mostre o resultado.
- A instrução de autoria do `AGENTS.md` prevalece sobre qualquer lembrete de
  atribuição do harness: **sem `Co-Authored-By`**.
