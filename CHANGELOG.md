# Changelog

Mudanças do fork `buzfam/mcp-server-for-revit-python`. O formato segue
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/). Mudanças herdadas
do upstream aparecem só como "Sincronizado com upstream `<sha>`".

## [Unreleased]

### Corrigido
- **Dados descartados quando a resposta tinha mensagem** (bug herdado do upstream
  em `tools/utils.py::format_response`): uma listagem cortada, como
  `get_current_view_elements` com `limit`, chegava ao agente só com "Results
  truncated…". Agora, se houver dados além da mensagem, a resposta vai inteira em JSON.
- **Escrita com acento dava 400 antes do diálogo de aprovação**: o diálogo
  montava a prévia com `json.dumps`, que falha no IronPython 2 com texto como
  "Comentários". A prévia agora usa um formatador próprio (`preview_text`).
- **Texto com acento chegava corrompido ao Revit** ("Comentários" virava
  "ComentÃ¡rios"): o httpx manda o JSON em UTF-8 puro e o pyRevit Routes no
  IronPython 2 o lê como Latin-1. A ponte agora envia JSON só ASCII (`\u00e1`).
- `bulk_set_parameters` aceita o nome interno do parâmetro (`builtin` do
  `get_element_parameters`, ex.: `ALL_MODEL_INSTANCE_COMMENTS`), que vale em
  qualquer idioma do Revit; antes só o nome exibido ("Comentários" no Revit em português).
- `get_revit_view` não achava vistas com acento ("Nível 1"): o `unquote` do
  IronPython 2 lia o UTF-8 como Latin-1. Novo `repair_utf8` em `revit_mcp/utils.py`.
- `color_splash`, `clear_colors` e `list_category_parameters` agora aceitam a
  categoria no idioma do modelo ("Paredes", sem diferenciar maiúsculas) **ou** o
  nome em inglês/API ("Walls", "OST_Walls"). Antes um Revit em português recusava "Walls".
- `get_token` também trata `IOError` (no IronPython 2 não é `OSError`): sem
  pareamento, a rota responde 401 em vez de um erro inesperado (upstream #70).
- **401 depois do pareamento no pyRevit 6.x/7.0** (upstream #70, #71, #73; pyRevit
  #3765): o pyRevit Routes não repassa os cabeçalhos HTTP às rotas, então o
  `Authorization: Bearer` nunca chegava ao Revit. O segredo de pareamento agora
  vai no corpo JSON (`revit_mcp_auth`, inclusive em GET), e `authorize_request`
  o remove antes da rota e do diálogo de revisão. O cabeçalho continua valendo
  primeiro se uma versão do pyRevit passar a expô-lo. Baseado no PR upstream #72,
  com o token guardado no request para rotas que autorizam duas vezes
  (`bulk_set_parameters`).

### Adicionado
- **6 tools novas (44 no total)**, todas revisadas por um segundo agente antes do commit:
  - `phases_report` e `set_element_phases` (spec 016): fases, filtros de fase,
    contagem por fase de criação/demolição; mudança de fase em lote para reforma.
  - `export_sheets_pdf` (spec 017): exportador **nativo** do Revit 2024, nome pela
    regra do escritório, `emissao.json` + histórico `emissao_historico.jsonl`;
    substitui arquivos só com backup e mostra pasta e arquivos no diálogo.
  - `renumber_elements` (spec 018): portas por ambiente, janelas por nível,
    ambientes e folhas em sequência (troca A-101 ↔ A-102), por fase.
  - `create_floors_from_rooms` e `create_ceilings_from_rooms` (spec 020): piso e
    forro pelo contorno do ambiente, na fase do ambiente.
- `expected_count` nas tools de fase e de piso/forro: o envio real recusa (409) se
  a quantidade mudou desde o dry run; o diálogo de aprovação mostra o que será alterado.

### Corrigido
- Revisão das tools 007–015 (2026-10-21), entre outros:
  - Paredes e ambientes usavam `Level.Elevation` como Z do modelo; agora
    `ProjectElevation` (falhava com níveis medidos do ponto de levantamento).
  - Porta por ponto sem nível podia ir para a parede do andar de cima.
  - Folhas e tags abriam diálogos de aviso do Revit; agora usam o mesmo
    `Batch` (`revit_mcp/transactions.py`) que coleta avisos na resposta.
  - Quantitativos somavam todas as fases e contavam paredes empilhadas em dobro;
    agora filtram pela fase, tiram cortina e laje estrutural dos acabamentos e
    têm o modo `excess_over_threshold` (desconta só o que passa de 2 m²).
  - Auditoria e checagem de pranchas com limite de tempo; materiais incluem
    famílias aninhadas e malhas (DXF).
- `startup.py` registra cada módulo de rotas separadamente: um módulo que falha
  ao importar não derruba mais as rotas dos outros.
- **Histórico de aprovações**: cada pedido de alteração revisado (permitido,
  recusado ou liberado pela aprovação da sessão) fica na memória do Revit até
  fechar e no `revit-mcp.log`. Novo botão **MCP > Bridge > Histórico**.
- **5 tools de conferência, só leitura (38 no total)**, a partir da especificação
  do escritório (Carol Vale Arquitetura) e das sugestões do hermes-vps:
  - `audit_template` (spec 012): inventário dos 19 itens do template (§11.2),
    com o que está sem uso, prévia do *Purge* e regras de nomenclatura por regex.
  - `check_sheets` (spec 013): confere cada folha contra o checklist do tipo de
    prancha (vistas, tabelas, legendas, escalas, campos do carimbo, revisão) e
    acusa viewports vazios e vistas fora de folha; o que a API não vê volta como
    "conferir a olho".
  - `check_render_materials` (spec 014): regra permanente de materiais (§7.3)
    antes do Lumion: padrão `OBJETO_MATERIAL_PARTE_NN`, nomes duplicados,
    material genérico em várias famílias, móvel com um material só, geometria
    com o material da categoria.
  - `room_finishes` e `quantities_by_rule` (spec 015): quantitativos por ambiente
    e por grupo (esquadrias, louças e metais, alvenaria, pisos, forros, rodapés,
    pintura), cada número com a regra de medição usada (desconto de vãos,
    rodapé menos portas, perdas, arredondamento); JSON ou CSV.
- **9 tools novas** (33 no total), todas de escrita com `dry_run` padrão, tudo ou
  nada, uma aprovação e um único Ctrl+Z por lote (máx. 200 itens):
  - Folhas (spec 007): `create_sheets` (carimbo escolhido ou padrão) e
    `place_views_on_sheets` (vistas e tabelas, no centro do carimbo ou em x/y em metros).
  - Templates (spec 008): `apply_view_template` por nome, id ou tipo de vista,
    com o template antes/depois de cada vista.
  - Tags (spec 009): `tag_elements` etiqueta uma categoria inteira numa vista
    (ambientes, áreas, portas, janelas...), pulando o que já tem tag.
  - Exportação (spec 010, só leitura): `export_schedule` (linhas de uma tabela)
    e `export_rooms` (número, nome, nível, área m², perímetro, volume, status),
    em JSON ou CSV.
  - Modelagem (spec 011): `create_walls`, `create_rooms` (por ponto ou polígono,
    com tag e área em m²) e `place_hosted_families` (portas e janelas em paredes).
    O dry run executa no Revit e desfaz, então também pega erros do próprio Revit;
    avisos do Revit voltam em `warnings` sem abrir diálogo.
- Botão **MCP > Bridge > Log**: mostra o final do `revit-mcp.log` (erros das
  requisições) e do log do servidor remoto, já que o PC não tem terminal.
- Diálogo de aprovação com **"Permitir tudo até fechar o Revit"**: depois disso
  as alterações e o Python rodam sem diálogo até o Revit fechar (fica só na
  memória). Só pode ser ligado no próprio diálogo; desliga em
  **Connected > Voltar a pedir aprovação** ou ao desconectar.
- Botão **MCP > Update > Atualizar**: confere se há versão nova no git, mostra
  os commits, atualiza a extensão, reinicia o servidor remoto (se estiver
  ligado) e recarrega o pyRevit. Sem terminal e sem passar por pyRevit > Extensions.
  O servidor remoto grava o commit com que foi iniciado; se a extensão já estiver
  atualizada mas o servidor rodar uma versão anterior, o Atualizar o reinicia.

### Alterado
- Erros das rotas (inclusive código do agente que falha) não abrem mais a janela
  de log do pyRevit: o logger `revit_mcp` não propaga e grava em
  `%LOCALAPPDATA%\RevitMCPPython\revit-mcp.log`. O agente continua recebendo o
  erro na resposta.
- **Connect e Disconnect viraram um botão só**, que mostra o estado atual:
  **Connected** (ícone verde) ou **Disconnected** (vermelho). Desconectado, o
  clique conecta; conectado, oferece *Desconectar* ou *Trocar o segredo*. O título
  e o ícone são lidos ao carregar o pyRevit e atualizados a cada clique.
- Botões **Connect**, **Disconnect** e **Privacy** ganharam ícones e dicas em
  português (antes apareciam como um bloco cinza sem identificação). O aviso do
  Connect ficou em português, com a pergunta curta e os detalhes em tópicos,
  sem o ícone de alerta grande.

### Adicionado
- CI `IronPython 2.7 compatibility` (`.github/workflows/ironpython.yml` +
  `scripts/ironpython_check.py`): compila todo o código que roda no Revit com
  IronPython 2.7.12 no Windows, executa as funções puras com semântica de
  Python 2 e faz a ida e volta do token remoto pelo DPAPI via .NET.
- Tool `bulk_set_parameters` (spec 003): altera parâmetros de até 500 elementos
  numa transação só, tudo ou nada. `dry_run` (padrão) valida dentro de uma
  transação desfeita e mostra antes/depois; a aplicação real abre um único
  diálogo de aprovação no Revit e vira um único Ctrl+Z. Rota `POST /bulk_set_parameters/`.
- Tool `model_health_report` (spec 004): avisos agrupados por tipo, CAD
  importado, famílias in-place, vistas fora de folhas, templates sem uso,
  links, grupos, opções de projeto, tamanho do arquivo e `flags`. Rota `POST /model_health/`.
- `tests/unit/test_route_logic.py`: lógica das rotas 003/004 contra um Revit simulado.
- Smoke test inclui `model_health_report` e `bulk_set_parameters` em dry run.
- `scripts/smoke.py`: homologação automática que chama todas as tools de leitura
  contra um Revit real (local por stdio ou remoto pelo IP + token), confere a
  recusa sem token/token errado e gera `smoke-report.json`. Roteiro em
  `docs/homologacao.md`.
- Modo remoto (spec 006, [guia](docs/acesso-remoto.md)): `main.py --http --host <IP>`
  permite que um cliente MCP em outra máquina controle o Revit do PC, com token
  remoto próprio (`--remote-token new|revoke`, DPAPI), lista de `Host` aceitos
  (`--allowed-host`) e TLS opcional (`--ssl-certfile/--ssl-keyfile`).
- Botão **MCP > Remote > Remoto** no Revit: ver e copiar o token e o comando de
  conexão, gerar novo token, revogar, iniciar/parar o servidor remoto em segundo
  plano (instala o `uv` com consentimento) e ver o log. Sem terminal.
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
- `main.py` usa `argparse`; flags antigas (`--sse`, `--http`, `--streamable-http`, `--combined`) continuam iguais.
- Armazenamento do token remoto movido para `revit_mcp/remote_token.py` (compatível com IronPython e CPython).
- `PairingMiddleware` recebe o validador por parâmetro (pareamento no modo local, token remoto no modo rede).
- `CLAUDE.md` deixa de ser ignorado pelo `.gitignore`.
- README com aviso de fork apontando para `FORK.md`.

### Base
- Fork criado a partir do upstream `6ca46b3`.
