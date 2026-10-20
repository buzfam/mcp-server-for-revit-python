---
name: sync-upstream
description: Traz as mudanças do repositório original (mcp-servers-for-revit/mcp-server-for-revit-python) para o fork buzfam sem perder as mudanças do fork. Use quando o usuário pedir para atualizar, sincronizar ou puxar novidades do upstream.
---

# Sincronizar com o upstream

1. Garantir o remote e buscar:
   ```bash
   git remote get-url upstream || git remote add upstream https://github.com/mcp-servers-for-revit/mcp-server-for-revit-python.git
   git fetch upstream
   git fetch buzfam
   ```
2. Ver o que chega antes de mexer:
   ```bash
   git log --oneline buzfam/master..upstream/master
   ```
   Se não houver commits novos, pare e avise.
3. Branch de sincronização a partir do fork:
   ```bash
   git switch -c sync/upstream-$(date +%Y%m%d) buzfam/master
   git merge --no-ff upstream/master -m "chore: sync with upstream <sha curto>"
   ```
   Use **merge**, não rebase: o fork é público e o histórico já foi publicado.
4. Conflitos esperados e como resolver:
   - `.gitignore`: manter o upstream, mas **sem** a linha `CLAUDE.md`.
   - `README.md`: manter o upstream + o bloco de aviso do fork no topo.
   - `tools/catalog.py`, `tools/__init__.py`, `startup.py`, `tools/registration.py`:
     manter as entradas dos dois lados.
   - `mcp-tool-manifest.json`: não resolver à mão; regenerar no passo 5.
5. Validar:
   ```bash
   uv sync --frozen --extra test
   uv run --frozen python scripts/export_mcp_manifest.py
   uv run --frozen --extra test python -m pytest tests/unit -q
   uv run --frozen python scripts/export_mcp_manifest.py --check
   ```
6. Registrar: `CHANGELOG.md` → "Sincronizado com upstream `<sha>`"; `HANDOFF.md`
   → nova base do fork e qualquer mudança de comportamento relevante do upstream.
7. Mostrar o resumo ao usuário e **perguntar** antes de fazer push e merge no
   `master` do `buzfam`. Nunca faça push para o upstream.
