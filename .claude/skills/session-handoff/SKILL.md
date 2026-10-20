---
name: session-handoff
description: Fecha uma sessão de trabalho atualizando HANDOFF.md, CHANGELOG.md e o status das specs, para que a próxima pessoa ou agente continue de onde parou. Use ao terminar uma tarefa, quando o usuário disser "encerrar", "handoff", "passar o bastão" ou antes de um commit final.
---

# Handoff de sessão

1. Levante o que mudou: `git status`, `git diff --stat` e `git log --oneline buzfam/master..HEAD`.
2. Rode a verificação e guarde o resultado literal:
   ```bash
   uv run --frozen --extra test python -m pytest tests/unit -q
   uv run --frozen python scripts/export_mcp_manifest.py --check
   ```
3. `HANDOFF.md`:
   - Atualize `_Última atualização_` com a data de hoje (absoluta).
   - **Estado atual**: o que existe agora, com o resultado dos testes.
   - **Próximos passos**: lista numerada, a primeira coisa a fazer no topo.
   - **Decisões tomadas**: uma linha por decisão nova, com data e motivo.
   - **Pendências e riscos**: em especial o que **não foi validado no Revit**.
4. `CHANGELOG.md`: entradas em `[Unreleased]` (Adicionado / Alterado / Corrigido / Removido).
5. `docs/specs/README.md`: status de cada spec tocada.
6. Não invente validação: se algo não foi testado no Revit, escreva isso.
7. Commit `docs: update handoff` **sem** `Co-Authored-By`; pergunte antes do push.
