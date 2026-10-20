---
name: add-mcp-tool
description: Checklist para criar ou alterar uma tool MCP neste repositório (rota IronPython em revit_mcp/, wrapper CPython em tools/, catálogo, READ_ONLY, manifest, testes, changelog). Use sempre que for adicionar uma tool nova ou mudar a assinatura, a descrição ou a rota de uma existente.
---

# Adicionar uma tool MCP

Antes de começar: a spec existe em `docs/specs/NNN-*.md`? Se não, escreva-a
primeiro usando o template de `docs/specs/README.md` e marque o status como
**Em desenvolvimento**.

## 1. Rota (IronPython 2.7, dentro do Revit)

Arquivo `revit_mcp/<modulo>.py`. Siga o formato de `revit_mcp/colors.py`:

```python
# -*- coding: UTF-8 -*-
"""<O que o módulo faz>"""
from pyrevit import routes, DB
import json
import logging
from .security import authorize_request
from .utils import normalize_string, element_id_value

logger = logging.getLogger(__name__)


def register_<modulo>_routes(api):
    @api.route("/<rota>/", methods=["POST"])
    def <handler>(doc, request):          # injetáveis: doc, uidoc, uiapp, request
        denial = authorize_request(request, action="<Verbo legível>")  # None se só leitura
        if denial is not None:
            return denial
        try:
            if not doc:
                return routes.make_response(data={"error": "No active Revit document"}, status=503)
            data = json.loads(request.data) if isinstance(request.data, str) else request.data
            # validar entrada -> 400
            with DB.Transaction(doc, "MCP: <ação>") as t:   # só se alterar o modelo
                t.Start()
                ...
                t.Commit()
            return routes.make_response(data={"status": "success", ...})
        except Exception as e:
            logger.error("Error in <rota>: %s", e)
            return routes.make_response(data={"error": str(e)}, status=500)
```

Regras: sem f-strings nem type hints; IDs com `element_id_value`; strings do
Revit com `normalize_string`; uma transação por requisição; limite o tamanho
da resposta.

## 2. Registrar a rota

Em `startup.py`, dentro de `register_routes()`, no mesmo padrão dos outros:

```python
        from revit_mcp.<modulo> import register_<modulo>_routes

        register_<modulo>_routes(api)
```

## 3. Wrapper (CPython)

Arquivo `tools/<modulo>_tools.py`, no formato de `tools/colors_tools.py`:

- Função `register_<modulo>_tools(mcp, revit_get, revit_post)`.
- `@mcp.tool()` com `ctx: Context = None` e docstring com `Args:`.
- **Não envie chaves com `None`** no payload (ver `tests/unit/test_null_defaults.py`).
- Valide limites no wrapper também (falha rápida sem ir ao Revit).
- Retorne `format_response(response)`.
- Operações demoradas: `timeout=60.0` ou mais em `revit_post`.

## 4. Registrar o wrapper

`tools/__init__.py`: importar e chamar `register_<modulo>_tools(...)`.

## 5. Catálogo e anotações

- `tools/catalog.py`: adicionar `"<nome_da_tool>": "<descrição pública curta>"`.
  Sem isso o registro quebra com `KeyError`.
- `tools/registration.py`: se for só leitura, adicionar o nome em `READ_ONLY`.
  Se acessar arquivos/rede fora do modelo, avaliar `openWorldHint`.

## 6. Manifest

```bash
uv run --frozen python scripts/export_mcp_manifest.py
```

Se a tool usa uma API nova do Revit, atualize `autodesk_apis_used` em
`scripts/export_mcp_manifest.py` antes de regenerar.

## 7. Testes

- `tests/unit/`: teste do wrapper com `mock_mcp` e `mock_revit_post`/`mock_revit_get`
  (ver `tests/unit/test_tool_wrappers.py`): endpoint, payload, defaults, validação.
- `tests/integration/` (opcional, só Windows + Revit): marcar com `@pytest.mark.integration`.

```bash
uv run --frozen --extra test python -m pytest tests/unit -q
uv run --frozen python scripts/export_mcp_manifest.py --check
```

## 8. Documentação

- `CHANGELOG.md` → `[Unreleased] / Adicionado`.
- Spec: status **Implementada (não validada no Revit)**; índice em `docs/specs/README.md`.
- `HANDOFF.md`: o que falta validar no Revit.
- Tabela "Supported Tools" do `README.md` não é alterada (é do upstream);
  o roadmap do fork fica em `FORK.md`.

## 9. Commit

`feat: add <nome_da_tool> tool` — **sem** `Co-Authored-By`.
