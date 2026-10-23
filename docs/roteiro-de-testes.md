# Roteiro de testes no Revit (pendências e resultados esperados)

Todos os testes que faltam fazer num PC com **Windows + Revit 2024 pt-BR + pyRevit 7**,
cada um com o resultado esperado. Gerado em 2026-10-22 a partir das specs
(`docs/specs/`), do código e do `HANDOFF.md`. Os itens já validados na homologação
de 2026-10-20 estão marcados nas specs e não se repetem aqui.

**Como usar**
- Copie o texto da coluna "O que pedir / fazer" no chat do agente conectado ao Revit e
  compare com "Resultado esperado". Marque `[x]` no que passou; no que falhou, anote o
  que apareceu (texto ou print) ao lado.
- Erros de HTTP chegam ao agente como `Error: 409 - {...}` (o número aparece no texto).
  Erros por item com HTTP 200 chegam como `=== ERROR DETAILS ===` com a lista `errors`.
  A prévia (`dry_run`) chega como `=== DRY RUN ===`.
- Nas tools que alteram o modelo, **a prévia é o padrão e não abre diálogo**; o diálogo de
  aprovação só aparece na execução real.
- **"Não altera o modelo"**: salve antes (Ctrl+S); depois da prévia a lista do botão
  Desfazer não pode ganhar nenhum item "MCP: ...".
- No diálogo de aprovação, use **Permitir** (não "Permitir tudo até fechar o Revit"),
  porque vários testes conferem que aparece "um diálogo só". Se ligou, use
  *Bridge > Status > Voltar a pedir aprovação*.
- **(confirmar)** marca o que o código não deixa garantir: anote o que o Revit fez.
- Ao terminar, mande este arquivo (com as marcações) e o `smoke-report.json` para quem
  vai corrigir, ou cole no Claude: ele marca os checklists das specs.

## Resultados da sessão de 2026-10-22 (remoto, PC 192.168.68.111)

Revit 2024 pt-BR, modelo vazio `karlipe` (template sem famílias de porta, janela,
etiquetas nem carimbo). Testes feitos pelo agente pela rede, com o usuário clicando
em Permitir no PC. Desfazer (Ctrl+Z) e o que aparece na tela **não** foram conferidos.

**Passou**
- S-07 smoke remoto: 401 sem token e com token errado, 45 tools, 16 ok, 0 falhas.
- T-FMT-01: `get_current_view_elements` sem o aviso do smoke (dados vêm com o corte).
- Leitura: `audit_template` (5 s, `revit_purge`, 16 seções, 19 itens, sem erros, mas num
  `.rvt`, não no `.rte`), `phases_report`, `check_sheets`, `check_render_materials`,
  `quantities_by_rule`, `export_rooms` (8 ambientes, 49,92 m²).
- T-011-01 (prévia com aviso de sobreposição em português em `warnings`), T-011-03
  (12 paredes), T-011-04 (8 ambientes; áreas batem com parede de 0,20 m), T-011-16.
- `room_finishes` da Sala: perímetro 15,00; parede 15,00 × 2,44 = 36,58; pintura 50,52.
  Depois do forro: altura 2,60 (`ceiling_height`), parede 39,00, pintura 52,94.
- T-020-01, T-020-02 (8 pisos), T-020-06 (`existing_floor`), T-020-08 (8 forros a 2,60),
  T-020-12 (409 com `expected_count` errado, sem diálogo).
- T-016-04 (prévia de fase: 12 paredes "Construção nova" → "Existente").
- T-018-08 prévia (ambientes 101–108).
- T-007-01, T-007-02 (2 folhas sem carimbo), posicionamento da planta na A-101.
- T-017-01, T-017-02 (2 PDFs nativos em 6 s, `emissao.json` e histórico), prévia
  seguinte com `exists: true`, T-017-12 (`C:\Windows` e `\\servidor` recusados).
- 008/010: modelo de vista e tabela inexistentes → erro claro.
- `execute_revit_code` com erro: 500 com traceback e `partial_output`.

**Depois das correções (mesma sessão)**
- `create_rooms` por contorno: área 0 → corrigido (`3409088`) e **revalidado**: Deck 3 × 3 m = 9,00 m².
- Etiqueta sem família: **revalidado**, `tag: "skipped: no room tag family is loaded ..."`.
- T-016-05: 12 paredes passaram a "Existente" (real, um diálogo).
- Ainda não testados no PC: pareamento automático (B-01/S-03), Emitir PDF escolhendo a pasta
  (inclusive rede) e o Auditar template salvando Excel + HTML (B-32).

**Achados**
- **Bug corrigido** (`9b70cfd`): `create_rooms` devolvia `tag_id` sem família de
  etiqueta de ambiente carregada (o Revit cria etiquetas sem tipo, invisíveis).
  Revalidar: as 8 etiquetas desta sessão provavelmente estão invisíveis; apague-as.
- T-018-08: na ordem de leitura o Lavabo (centro y 2,70) vem antes da Sala (y 1,80),
  porque fica numa "linha" mais alta. Confirmar com o escritório se essa ordem serve.
- Altura padrão dos ambientes do template: 2,44 m (8 pés).
- O PC caiu da rede uma vez no meio das chamadas (Wi-Fi/energia). O IP mudou de .108 para .111.

**Pendente: precisa carregar famílias no PC** (porta, janela, etiquetas de
ambiente/porta/janela e um carimbo): T-011-05 a T-011-15, T-007-03 a T-007-10 com
carimbo, todo o 009, T-015 com vãos, T-018 de portas e janelas, T-017 com carimbo
e revisão.

## Ordem sugerida e teste mínimo

São cerca de 200 itens; não precisa fazer tudo de uma vez. **Primeira sessão (≈ 2 h)**,
o que mais tem risco:

1. Preparação S-01 a S-07 (atualizar, parear, smoke).
2. Segurança S-08 a S-16 (diálogo, recusa, permitir tudo, erro sem janela, Log).
3. Modelagem T-011-01 a T-011-08 (planta de teste: paredes, ambientes, portas e janelas).
4. Folhas T-007-01 a T-007-06, etiquetas T-009-02 a T-009-04, exportação T-010-01 e T-010-07.
5. Painel Conferir B-15 a B-17, B-22, B-25, B-26 e B-30.
6. PDF T-017-01 a T-017-03 e T-017-19 (arquivo aberto no Acrobat).
7. Quantitativos T-015-01 (bater a conta de referência).

Depois, na ordem do documento. Os blocos 012–020 rendem mais num `.rvt` real do escritório.

**Arquivos para separar**
- (a) projeto com famílias de porta e janela e 2 carimbos carregados (pode ser o da planta de teste);
- (b) um `.rvt` real e grande do escritório;
- (c) o template `.rte` do escritório;
- (d) um projeto de reforma com fases "Existente" e "Nova construção";
- um projeto em pés (template imperial), para os testes de unidade do 003.

---

## Parte 1 — Preparação, segurança e botões do ribbon

### S1. Preparação
Uma vez, antes de qualquer outro teste.

| # | O que fazer | Resultado esperado | OK? |
|---|---|---|---|
| S-01 | Separe os arquivos (a) a (d) acima. | Os 4 abrem no Revit sem erro. | [ ] |
| S-02 | Abra o arquivo (a). **MCP > Update > Atualizar**. | Janela "Atualizar Revit MCP". Se houver versão nova: "Há uma atualização. Instalar agora?" com a lista de commits. Clique Sim: "Atualizado de `<hash>` para `<hash>`." e "O pyRevit vai recarregar agora.", e o pyRevit recarrega. Sem versão nova: "Já está na versão mais recente (`<hash>`)." | [ ] |
| S-03 | Veja o botão **MCP > Bridge > Status**. | O título é **Disconnected** (ícone vermelho) ou **Connected** (verde). | [ ] |
| S-04 | Primeira instalação: o botão já deve abrir como **Connected** (pareamento automático). Se estiver Disconnected (alguém desconectou), clique nele. | Janela "Conectar Revit MCP": "Permitir que clientes MCP deste usuário do Windows acessem este Revit?" com os avisos. Clique Sim: aparece "Connected." e o botão fica verde. | [ ] |
| S-05 | **pyRevit > Settings > Routes**. | Routes ligado. | [ ] |
| S-06 | Selecione 1 a 3 elementos (parede, porta). Rode `uv run --frozen python scripts/smoke.py --local`. | Uma linha por tool, com `[ok  ]`, `[warn]`, `[skip]` ou `[FAIL]`. No fim, "`N passed, N warnings, N skipped, 0 failed`" e "Report: ...\smoke-report.json". O modelo não muda, porque o `bulk_set_parameters` só roda em dry run. | [ ] |
| S-07 | Com o servidor remoto ligado (B-12), rode de outra máquina: `uv run --frozen python scripts/smoke.py --url http://<IP-DO-PC>:8000/mcp --token <TOKEN>`. | `[ok  ] auth: no token - 401 as expected` e `[ok  ] auth: wrong token - 401 as expected`. O resto fica como no S-06, com 0 failed. | [ ] |

### S2. Segurança e aprovação

| # | O que fazer | Resultado esperado | OK? |
|---|---|---|---|
| S-08 | Peça uma alteração. Exemplos: `color_splash` nas paredes da vista ativa, ou "Comentários" de 1 elemento com `bulk_set_parameters`. | Abre o diálogo **"Revit MCP request review"** com "An MCP client requests: Apply view colors." (ou "Set element parameters.") e o pedido completo. Botões: **Permitir**, **Permitir tudo até fechar o Revit** e **Recusar**. | [ ] |
| S-09 | Clique **Recusar**. | O agente recebe 403 "The user declined the request in Revit.". O modelo não muda e o Desfazer não ganha item. | [ ] |
| S-10 | Repita e clique **Permitir**. | A alteração é aplicada, e um Ctrl+Z desfaz tudo. | [ ] |
| S-11 | Faça outro pedido e clique **Permitir tudo até fechar o Revit**. Peça mais 2 alterações. | A primeira é aplicada, e as 2 seguintes rodam **sem diálogo**. | [ ] |
| S-12 | Clique em **Bridge > Status**. | Aparece "Aprovação automática: LIGADA até fechar o Revit." e a opção **Voltar a pedir aprovação**. Escolha essa opção: aparece "Pronto: cada alteração volta a pedir aprovação." | [ ] |
| S-13 | Peça mais uma alteração. | O diálogo volta a abrir. | [ ] |
| S-14 | Ligue "Permitir tudo..." de novo, feche e reabra o Revit, e peça uma alteração. | O diálogo abre, porque a liberação fica só na memória. | [ ] |
| S-15 | Peça `execute_revit_code` com erro proposital, por exemplo `print(doc.NaoExiste)`. Permita. | O diálogo mostra "Execute Python code" e o aviso "This code is NOT sandboxed...". **Nenhuma janela de erro do pyRevit abre.** O agente recebe "Error: 500 - ..." com `AttributeError`, o traceback e uma dica. | [ ] |
| S-16 | **Bridge > Log**. | Na seção "Requisições do Revit MCP", o caminho `%LOCALAPPDATA%\RevitMCPPython\revit-mcp.log` e a linha "Code execution failed: AttributeError: ...". | [ ] |
| S-17 | **Bridge > Status > Desconectar**. Depois peça qualquer tool. Reconecte (S-04) no fim. | Aparece "Disconnected. Novos pedidos do MCP serão recusados." e o botão fica vermelho. O agente recebe `Error: Use Revit's MCP > Bridge button (it shows Disconnected) to consent and pair this Windows user.`, sem chamar o Revit. | [ ] |
| S-18 | Com o servidor remoto ligado e um agente remoto conectado: **Remoto > Gerar novo token** > Sim. | Aparece "Gerar um novo token? O token atual deixa de funcionar na hora..." e depois a janela com o token novo. **Sem reiniciar o servidor**, o token antigo recebe **401**, e o novo funciona. | [ ] |
| S-19 | No PC, rode `uv run --frozen python main.py --remote-token revoke`. | O terminal mostra "Remote access token revoked." e o agente remoto passa a receber 401. | [ ] |
| S-20 | Rode `uv run --frozen python main.py --remote-token new`. Abra `%LOCALAPPDATA%\RevitMCPPython\remote.dpapi` no Bloco de Notas. | O token aparece uma vez no terminal. O `.dpapi` **não** tem o token legível. | [ ] |
| S-21 | **Remoto > Parar servidor remoto**. Depois `netstat -ano \| findstr :8000`. | Aparece "Servidor remoto parado." e nada escuta na porta 8000. | [ ] |
| S-22 | Com o servidor **remoto** ligado, peça `import_parameters_from_csv` com `file_path` de um `.csv` em `Documentos\RevitMCP\Importar`. | "Error: file_path is disabled while the server is reachable from the network; paste the spreadsheet as csv_text". Nada é lido. | [ ] |
| S-23 | O mesmo pedido com o servidor local (Claude no próprio PC). | O arquivo é lido e vem a prévia. | [ ] |
| S-24 | **Bridge > Histórico**. | Título "Pedidos do agente nesta sessão (mais recentes primeiro)". Uma linha por pedido: `HH:MM:SS  permitido / recusado / permitido + sessão inteira / automático (sessão)  <ação>  <resumo>`. Com a liberação ligada, aparece também "**Aprovação automática LIGADA**...". | [ ] |

### S3. Rede (itens ainda abertos da spec 006)
Revogar, gerar novo token e parar o servidor já estão em S-18 a S-21.

| # | O que fazer | Resultado esperado | OK? |
|---|---|---|---|
| S-25 | Regra de firewall na porta 8000 com `-RemoteAddress` só da máquina do agente. Tente conectar de uma terceira máquina. | A terceira dá timeout, e a permitida conecta. | [ ] |
| S-26 | Servidor com `--ssl-certfile` e `--ssl-keyfile` (certificado da CA interna). Conecte por `https://`. | Conecta sem aviso de certificado. Sem TLS, o terminal avisa "WARNING: no TLS...". | [ ] |
| S-27 | **Remoto > Iniciar servidor remoto** pela primeira vez num PC sem as dependências. | Aparece "O servidor está iniciando. Na primeira vez ele baixa as dependências...". Depois, *Ver token e comando de conexão* mostra "Servidor: RODANDO". | [ ] |
| S-28 | *Ver token > Copiar comando do Claude Code*. Cole na outra máquina. | O `claude mcp add ...` funciona e lista as tools (45). | [ ] |

### B1. Botões Bridge, Remote e Update

| # | O que fazer | Resultado esperado | OK? |
|---|---|---|---|
| B-01 | Abra o Revit com a extensão. | O botão Status mostra o estado salvo (Connected verde / Disconnected vermelho). | [ ] |
| B-02 | Connected: **Status > Trocar o segredo**. | Aparece "Segredo trocado. Continua Connected.". Confirmar se o agente local continua funcionando sem reiniciar. | [ ] |
| B-03 | **Log**. | Seção "Requisições do Revit MCP" com as últimas 80 linhas do `revit-mcp.log` (ou "(vazio: nenhum erro registrado)") e seção "Servidor remoto" com as últimas 30 linhas do `remote-server.log` (ou "(vazio)"). | [ ] |
| B-04 | **Histórico**, sem nenhum pedido feito nesta sessão. | Aparece "(nenhum pedido de alteração nesta sessão)" e o caminho do log. | [ ] |
| B-05 | **Privacy**. | Mostra o `PRIVACY.md`. | [ ] |
| B-06 | **Remoto**, sem token criado. | Menu "Acesso remoto ao Revit MCP" com Criar token, Iniciar (ou Parar) servidor remoto e Ver log do servidor. | [ ] |
| B-07 | **Remoto > Criar token**. | Janela "Revit MCP - Acesso remoto" com o estado do servidor (RODANDO/PARADO), os IPs, a porta 8000, a linha "Authorization: Bearer ..." e os botões Copiar comando do Claude Code, Copiar token e Fechar. | [ ] |
| B-08 | **Remoto**, com token criado. | O menu mostra Ver token e comando de conexão, Gerar novo token, Revogar token, Iniciar/Parar servidor remoto e Ver log do servidor. | [ ] |
| B-09 | **Copiar token**. | Aparece "Copiado para a área de transferência.". | [ ] |
| B-10 | **Revogar token** > Sim. | "Revogar o token? Nenhum agente remoto consegue se conectar..." e depois "Token revogado.". O menu volta a mostrar "Criar token". | [ ] |
| B-11 | Desconectado: **Remoto > Iniciar servidor remoto**. | "Conecte primeiro: clique em MCP > Bridge (botão Disconnected).". | [ ] |
| B-12 | Conectado: **Iniciar servidor remoto**. | Como o B-10 revogou o token, primeiro aparece "Ainda não existe token remoto. Criar agora?" (Sim). Depois "Iniciar o servidor de acesso remoto na porta 8000?" com aviso de firewall (permitir só em rede privada). Depois a janela do token com "Servidor: RODANDO". | [ ] |
| B-13 | **Ver log do servidor**. | Janela "Log do servidor remoto" com `%LOCALAPPDATA%\RevitMCPPython\remote-server.log` e as últimas 80 linhas, incluindo "Uvicorn running". | [ ] |
| B-14 | Com o servidor remoto ligado, **Update > Atualizar** sem versão nova. | "Já está na versão mais recente (...)". Se o servidor estava numa versão anterior, aparece também "O servidor remoto estava numa versão anterior e foi reiniciado.". | [ ] |

### B2. Painel Conferir (sem IA)
Com o **agente desligado**. Estes botões não usam o MCP nem o pareamento e nunca alteram o modelo.

| # | O que fazer | Resultado esperado | OK? |
|---|---|---|---|
| B-15 | Qualquer botão do Conferir sem projeto aberto. | "Abra um projeto no Revit primeiro." No Auditar template: "Abra um projeto ou template no Revit primeiro.". | [ ] |
| B-16 | No `.rvt` do escritório, **Checar pranchas** pela primeira vez. | Cria `%APPDATA%\RevitMCPPython\checklists_pranchas.json`, copiado de `config\checklists_pranchas.exemplo.json`. O relatório começa com "**Primeiro uso:** os checklists de EXEMPLO foram copiados para `...`. Edite esse arquivo...". | [ ] |
| B-17 | Leia o relatório. | Mostra "Checagem de pranchas"; o caminho dos checklists; "**X de Y folhas conferidas** · OK · Faltando · Revisar · Sem checklist"; as tabelas "Problemas por tipo" e "Folhas" (Id clicável, Número, Nome, Checklist, Status, Problemas, Conferir a olho); um bloco por folha com itens "☐"; e "Vistas fora de pranchas". Clicar no Id seleciona a folha. | [ ] |
| B-18 | **Checar pranchas** de novo. | Sem a mensagem de "Primeiro uso". Usa o arquivo do `%APPDATA%`. | [ ] |
| B-19 | Apague uma vírgula do JSON no `%APPDATA%`, salve e rode o botão. Desfaça o erro no arquivo depois. | "Arquivo de checklists inválido: `<caminho>`" com "o arquivo não é um JSON válido (...)". | [ ] |
| B-20 | **Shift+clique** em Checar pranchas. | Abre a escolha de arquivo ("Arquivo de checklists das pranchas") na pasta `%APPDATA%\RevitMCPPython`. Cancelar não mostra nada. | [ ] |
| B-21 | **Update > Atualizar** e depois abra o JSON do `%APPDATA%`. | Suas edições continuam lá. | [ ] |
| B-22 | **Checar materiais** num projeto com mobiliário. | Janela "Checar materiais" com "Checagem de materiais para render", o modelo e as categorias. Depois, os problemas (sem material, nome fora de `OBJETO_MATERIAL_PARTE_NN`, repetido, genérico) ou "Nenhum problema encontrado.". | [ ] |
| B-23 | **Shift+clique** em Checar materiais com uma vista 3D ativa. | Só os elementos visíveis nessa vista. Se não houver nenhum: "**Nenhum elemento das categorias verificadas visível nesta vista.**". | [ ] |
| B-24 | **Shift+clique** com uma folha ou tabela ativa. | "A vista ativa (Folha) não mostra o modelo: abra uma vista 3D, planta ou elevação.". | [ ] |
| B-25 | **Emitir PDF** > "Todas as folhas". | Diálogo "Emitir N PDFs de M folhas?" com a pasta `Documentos\RevitMCP\Emissoes\<projeto>\<AAAA-MM-DD_HHMMSS>` "(será criada)", a lista de arquivos `projeto_folha_revisão_data`, as folhas fora da emissão e "Também grava emissao.json...". | [ ] |
| B-26 | Sim. | "N PDF(s) emitido(s)." com a pasta, "Registro: ...emissao.json" e "Abrir a pasta?". A pasta tem os PDFs, o `emissao.json` e o `emissao_historico.jsonl`. **Não** abre o diálogo de aprovação do MCP. | [ ] |
| B-27 | "Escolher folhas..." com 2 folhas marcadas. | O plano mostra só as 2. | [ ] |
| B-28 | **Histórico**. | Aparece a linha `usuário no Revit  Emitir PDF (botão)  N PDF(s) em <pasta>`. | [ ] |
| B-29 | Abra no Acrobat um PDF do B-26 e rode **Emitir PDF** de novo. | **Sucesso**, porque o botão grava sempre numa pasta nova com data e hora. O teste de arquivo travado é o T-017-19, pela tool. | [ ] |
| B-30 | Abra o `.rte` do escritório. **Auditar template**. | "Auditoria do template", "Modelo: **...** (arquivo de template .rte) · Revit 2024", tabela "Inventário" (Seção, Itens, Sem uso), listas "Sem uso", "Prévia do Limpar não utilizados: N item(ns)" com "Nada foi apagado." e "Checklist do template (19 itens)". Sem nomenclatura: o botão não recebe regras (ela é testada no T-012-09). | [ ] |
| B-31 | Compare com **Gerenciar > Limpar não utilizados** (sem confirmar a limpeza). | As listas batem. Modelos de vista e filtros aparecem à parte, como "Fora do Limpar do Revit". | [ ] |
| B-32 | Responda Sim em "Salvar o relatório (planilha Excel e página HTML)?" e salve na pasta sugerida. | O nome sugerido é `auditoria_<modelo>_<AAAA-MM-DD>.xlsx`, na pasta do modelo. Na janela aparecem "Planilha salva em `<caminho>.xlsx`" e "Página HTML salva em `<caminho>.html`", e depois "Abrir a pasta?". A planilha abre no Excel **sem aviso de reparo**, com as abas "Resumo", "Checklist" (19 itens), uma aba por seção com itens (ex.: "Famílias", "Materiais", "Modelos de vista") e "Limpeza (prévia)"; cabeçalho em negrito, fixo e com filtro; nomes com acento certos (ex.: "Montante de canto trapezóide"); "Situação" = "Sem uso"/"Em uso"; Id e contagens como números. A página `.html` abre no navegador com o mesmo conteúdo da janela do pyRevit e imprime em PDF (Ctrl+P). Rode de novo com a planilha aberta no Excel e salve por cima: aparece "Substituir os arquivos que já existem?" (com o `.html`) e, depois de Sim, "Não foi possível salvar o relatório." com "Feche a planilha no Excel e tente de novo."; nenhum arquivo `.tmp` fica na pasta. | [ ] |
| B-33 | Depois de B-15 a B-32, abra a lista do Desfazer. | Nenhum item novo dos botões. | [ ] |
| B-34 | No projeto de reforma (d): **Checar pranchas** e **Auditar template**. | Os dois rodam sem erro. As pranchas de demolir/construir caem no checklist "Planta demolir/construir". | [ ] |

---

## Parte 2 — Tools 001–011

**Planta de teste (9,30 × 6,25 m, eixos das paredes, nível Térreo).**
- Embaixo (y 0 a 3,60): Sala (x 0 a 4,30), Cozinha (4,30 a 6,70), Área de serviço (6,70 a 9,30, y 0 a 1,80) e Lavabo (6,70 a 9,30, y 1,80 a 3,60).
- Em cima (y 3,60 a 6,25): Quarto 1 (0 a 3,10), Circulação (3,10 a 4,30), Banheiro (4,30 a 5,60) e Quarto 2 (5,60 a 9,30).

As áreas esperadas usam paredes de e = 0,15 m. Refaça a conta com a espessura real do tipo padrão.

### 011 — Modelagem (create_walls, create_rooms, place_hosted_families)
Precisa:
- nível **"Térreo"** (renomeie "Nível 1" e aceite renomear as vistas) e **"Pavimento 1"** a 3,00 m;
- planta de piso Térreo aberta;
- famílias de porta 0,80 × 2,10 e de janela carregadas;
- a espessura **e** do tipo de parede padrão anotada.

| ID | O que pedir / fazer | Resultado esperado | OK? |
|---|---|---|---|
| T-011-01 | "Crie no Térreo, altura 2,80 m, só a prévia, estas 13 paredes (x; y em metros): 1 (0;0)→(9,30;0); 2 (9,30;0)→(9,30;6,25); 3 (9,30;6,25)→(0;6,25); 4 (0;6,25)→(0;0); 5 (0;3,60)→(4,30;3,60); 6 (4,30;3,60)→(9,30;3,60); 7 (4,30;0)→(4,30;3,60); 8 (4,30;3,60)→(4,30;6,25); 9 (6,70;0)→(6,70;3,60); 10 (6,70;1,80)→(9,30;1,80); 11 (3,10;3,60)→(3,10;6,25); 12 (5,60;3,60)→(5,60;6,25); 13 = cópia da parede 1." | `status: dry_run`, `created: 0`, "Revit accepted all 13 walls", `results` sem `element_id`. `warnings` com o aviso de paredes sobrepostas, em português. **Nenhuma janela de aviso abre**, nenhuma parede aparece e o Desfazer não ganha item. | [ ] |
| T-011-02 | "Agora crie de verdade só as 12 primeiras." No diálogo **"Create walls"**, clique **Recusar**. | `Error: 403 - ... The user declined the request in Revit.` Nada é criado. | [ ] |
| T-011-03 | "Tente de novo as 12 paredes." Permitir. Depois Ctrl+Z e Ctrl+Y. | Um diálogo, `status: success`, `created: 12` e 12 `element_ids` (anote o id da parede 1). Desfazer: **"MCP: create walls"**. Ctrl+Z apaga as 12 de uma vez e Ctrl+Y traz de volta. | [ ] |
| T-011-04 | "Crie os ambientes no Térreo, primeiro só a prévia: 01 Sala (2,15;1,80), 02 Cozinha (5,50;1,80), 03 Área de serviço (8,00;0,90), 04 Lavabo (8,00;2,70), 05 Quarto 1 (1,55;4,90), 06 Circulação (3,70;4,90), 07 Banheiro (4,95;4,90), 08 Quarto 2 (7,45;4,90). Depois aplique com etiqueta." | Prévia: `area_m2` ≈ Sala 14,32; Cozinha 7,76; Área de serviço 4,04; Lavabo 4,04; Quarto 1 7,38; Circulação 2,63; Banheiro 2,88; Quarto 2 8,88 (±0,01). Real: 1 diálogo **"Create rooms"**, 8 etiquetas com nome e número acentuados, `tag_id` em cada item, área igual à das Propriedades e "MCP: create rooms" no Desfazer. | [ ] |
| T-011-05 | "Crie um ambiente 'Teste' no ponto (12;3) no Térreo, de verdade." Permitir. | `status: error`, "1 of 1 rooms failed in Revit; nothing was created", com o erro "Room is not in an enclosed region (area 0)...". Nada é criado e **nenhuma janela de aviso** abre. | [ ] |
| T-011-06 | Na planta Térreo: "Crie o ambiente 09 'Deck' pelo contorno (0;−3), (3;−3), (3;−1,5), (0;−1,5), com etiqueta." Depois, numa vista 3D: "ambiente 10 'Teste 3D' pelo contorno (5;−3), (8;−3), (8;−1,5), (5;−1,5)". Desfaça só o segundo. | Deck: 4 linhas separadoras, `separation_line_ids` com 4 ids, `area_m2: 4.5` e `tag_id`. Pela 3D: as linhas vão para a planta Térreo, `area_m2: 4.5` e `tag: "skipped: the active view is not a floor plan of this level"`. | [ ] |
| T-011-07 | Na **planta de forro** Térreo: "ambiente 11 'Forro teste' pelo contorno (10;0), (12;0), (12;2), (10;2), com etiqueta". Depois Ctrl+Z. | `tag: "skipped: ..."`. As linhas separadoras vão para a **planta de piso** Térreo, sem erro, e `area_m2: 4.0`. | [ ] |
| T-011-08 | Na planta Térreo: "Coloque as portas 80×210 (prévia, depois de verdade): P1 na parede id <id da parede 1> a 1,50 m do início; P2 (3,70;3,60); P3 (3,10;5,30); P4 (4,30;5,30); P5 (6,15;3,60); P6 (4,30;1,80); P7 (6,70;2,70); P8 (6,70;0,90). Janelas: J1 (0;1,80) peitoril 1,00; J2 (5,50;0) peitoril 1,10; J3 (9,30;0,90) peitoril 1,10; J4 (1,55;6,25) peitoril 1,00; J5 (4,95;6,25) peitoril 1,60; J6 (7,45;6,25) peitoril 1,00; J7 (9,30;2,70) sem peitoril." | Prévia: 15 itens e nada criado. Real: 1 diálogo **"Place doors/windows in walls"**, `created: 15`. P1 com `wall_id` pedido, `offset_m: 1.5` e `point: [1.5, 0.0]`. Os itens por ponto trazem `distance_to_wall_m: 0.0`, e J1–J6 trazem `sill_height_m` igual ao pedido. Um Ctrl+Z ("MCP: place hosted families") apaga as 15 (refaça com Ctrl+Y). | [ ] |
| T-011-09 | Veja o J7 na resposta e nas Propriedades (*Altura do peitoril*). | O `sill_height_m` do J7 bate com o padrão da família e as Propriedades, e não é 0 (a não ser que a família seja assim). | [ ] |
| T-011-10 | "Parede de teste de (12;0) a (12,60;0) no Térreo, de verdade; depois uma porta 80×210 nela a 0,30 m do início, de verdade." Desfaça a parede no fim. | A porta dá `status: error`, `created: 0`, com o texto do Revit em `errors`. Nenhuma janela do Revit abre. (confirmar: se o Revit só avisar, a porta é criada e o aviso vem em `warnings`; anote) | [ ] |
| T-011-11 | "Parede de (0;9) a (4;9) com linha de localização 'face de acabamento interior', de verdade." Meça e depois Ctrl+Z. | *Linha de localização = Face de acabamento: interior*. Uma face fica exatamente em y = 9,00 e a parede fica toda de um lado (confirmar o lado). | [ ] |
| T-011-12 | "Parede de (0;10) a (4;10), base Térreo e topo Pavimento 1, de verdade." Depois a mesma com topo no Térreo. Desfaça a primeira. | 1ª: `top_level: "Pavimento 1"`, sem `height_m`, *Restrição superior = Até o nível: Pavimento 1* (3,00 m). 2ª: `status: error`, "top_level 'Térreo' must be above level 'Térreo'", nada criado. | [ ] |
| T-011-13 | "Parede de (0;0) a (9,30;0) no Pavimento 1, de verdade." Depois: "janela no ponto (2,80;0), só a prévia, sem dizer o nível". Depois com nível Pavimento 1. Ctrl+Z na parede no fim. | Sem nível: `status: error`, "Walls on 2 levels (Térreo, Pavimento 1) are equally close to point [2.8, 0]; pass level or wall_id" (a ordem dos níveis pode variar). Com nível: `dry_run` ok, `level: "Pavimento 1"`. | [ ] |
| T-011-14 | Numa **cópia**: tipo do nível com *Base de elevação = Ponto de levantamento* e o ponto de levantamento a 100 m. "Paredes (0;12)→(4;12)→(4;15)→(0;15)→(0;12) no Térreo; ambiente 'Cota' no ponto (2;13,5); ambiente 'Cota B' pelo contorno (6;12), (8;12), (8;14), (6;14)." | Num corte, as paredes nascem no nível Térreo (*Deslocamento da base = 0*, sem salto de 100 m). Os dois ambientes têm `area_m2` > 0, e as separadoras aparecem na planta Térreo. | [ ] |
| T-011-15 | Numa cópia **sem família de etiqueta de ambiente**: "ambiente 'Sem tag' pelo contorno (10;5), (12;5), (12;7), (10;7), de verdade". | `status: success` e `tag: "failed: <mensagem do Revit>"`. O ambiente fica. (confirmar) | [ ] |
| T-011-16 | Num projeto **sem famílias de porta/janela**, com uma parede: "porta 'Porta' no ponto (1;0), só a prévia". | `status: error`, "... is not loaded. No door or window families are loaded ... Insert > Load Family", `loaded_door_window_families: []` e `hint`. | [ ] |

### 007 — Folhas (create_sheets, place_views_on_sheets)
Precisa:
- a planta do 011;
- 2 carimbos, um tipo com nome acentuado (ex.: "Prancha Padrão");
- uma "Tabela de portas" com Marca, Largura, Altura e Nível.

Nomes de vista repetidos (planta de piso e de forro "Térreo" ou "Pavimento 1") dão erro pedindo `view_id`; nesses casos, peça a vista pelo id.

| ID | O que pedir / fazer | Resultado esperado | OK? |
|---|---|---|---|
| T-007-01 | Ctrl+S. "Crie as folhas A-101 a A-120 com o carimbo padrão, nomes 'Folha 01' a 'Folha 20', só a prévia." | `status: dry_run`, `applied: 0`, 20 `results` sem `sheet_id`. `title_block` = primeiro carimbo em ordem alfabética. Nada no Navegador nem no Desfazer. | [ ] |
| T-007-02 | "Agora crie de verdade." Depois Ctrl+Z e Ctrl+Y. | 1 diálogo **"Create sheets"**, `applied: 20`. Ctrl+Z ("MCP: create sheets") apaga as 20 e Ctrl+Y traz de volta. | [ ] |
| T-007-03 | "Crie A-121 'Extra' e A-105 'Repetida', de verdade." | Abre o diálogo **Create sheets** (Permitir). Depois `status: error`, `applied: 0`, "1 of 2 sheets are invalid; nothing was applied", `errors[{index: 1, number: "A-105", error: "A sheet with this number already exists"}]`. A-121 **não** é criada e o Desfazer não ganha item. | [ ] |
| T-007-04 | "Folha A-201 'Carimbo com acento' com o carimbo '<Família> : Prancha Padrão', de verdade." Depois com um nome inexistente. | `title_block.type: "Prancha Padrão"` com acento correto. O inexistente dá `status: error` com `available` ("Família : Tipo"). | [ ] |
| T-007-05 | "Coloque a planta de piso Térreo na A-101 sem posição, e a planta de piso Pavimento 1 na A-102 em x = 0,30 m, y = 0,20 m (prévia, depois de verdade)." | Pelo nome "Térreo": erro "2 views named 'Térreo' (CeilingPlan, FloorPlan); use view_id". Com `view_id`: `kind: viewport`, `anchor: centre`. A-101 com a vista no centro do carimbo. A-102 com o centro da viewport a 300/200 mm da origem da folha (confirmar a origem do carimbo). 1 diálogo **"Place views on sheets"** e "MCP: place views on sheets" no Desfazer. | [ ] |
| T-007-06 | "Coloque a Tabela de portas na A-103 sem posição, de verdade." | `kind: schedule`, `anchor: top_left`. O canto superior esquerdo fica 2 cm para dentro do canto do carimbo, e a tabela inteira dentro da folha. | [ ] |
| T-007-07 | "Tabela de portas na A-104 em x = 0,05, y = 0,55, de verdade." | `position_m {x: 0.05, y: 0.55}`, com o canto superior esquerdo nesse ponto. | [ ] |
| T-007-08 | Confira T-007-06 e T-007-07 juntos. | A mesma tabela fica em duas folhas, sem erro. | [ ] |
| T-007-09 | "Coloque a planta de piso Térreo (por id) também na A-105, de verdade." | Abre o diálogo **Place views on sheets** (Permitir). Depois `status: error`, "View cannot be added to this sheet (already on a sheet, empty or not placeable)". Nada é posicionado. | [ ] |
| T-007-10 | Em todos os posicionamentos, observe a tela. | Nenhuma janela de aviso do Revit trava o pedido. Os avisos vêm em `warnings`. | [ ] |

### 008 — Modelo de vista (apply_view_template)
Precisa: as plantas de piso Térreo e Pavimento 1, a planta de forro Térreo (se também existir planta de forro "Pavimento 1", peça as vistas por `view_id`, senão vem o erro de nome ambíguo) e o modelo **"Planta – Apresentação"** (Vista > Modelos de vista > Criar a partir da vista atual).

| ID | O que pedir / fazer | Resultado esperado | OK? |
|---|---|---|---|
| T-008-01 | Ctrl+S. "Aplique o modelo 'Planta – Apresentação' em todas as plantas de piso, só a prévia." | `status: dry_run`, `template.name` com acento. Um item por planta de piso, com `view_type: "FloorPlan"`, `before` e `after`. Nada muda e o Desfazer não ganha item. | [ ] |
| T-008-02 | "Agora aplique de verdade." Depois Ctrl+Z. | 1 diálogo **"Apply view template"**, `applied` = nº de plantas de piso, e o *Modelo de vista* trocado em cada uma. Ctrl+Z ("MCP: apply view template") volta todas. | [ ] |
| T-008-03 | "Aplique 'Planta – Apresentação' na planta de forro Térreo (por id) e na planta de piso Pavimento 1, de verdade." | Abre o diálogo **Apply view template** (Permitir). Depois `status: error`, `applied: 0`, "Template cannot be applied to this view (view type mismatch)" no índice 0. O Pavimento 1 também **não** recebe o modelo. | [ ] |
| T-008-04 | "Aplique 'planta – apresentação' (minúsculas) no Pavimento 1, só a prévia." | Encontra o modelo, e `after: "Planta – Apresentação"` vem com acento e travessão. | [ ] |

### 009 — Etiquetas (tag_elements)
Precisa:
- a planta do 011 com 8 portas, 7 janelas e 8 ambientes, e a planta Térreo **ativa**;
- uma planta de área do Térreo com áreas;
- etiqueta multicategoria **ainda não** carregada.

| ID | O que pedir / fazer | Resultado esperado | OK? |
|---|---|---|---|
| T-009-01 | "Etiquete o Mobiliário nesta vista, só a prévia." (categoria sem etiqueta carregada) | 400: "No tag type loaded for 'Mobiliário'; load a tag family first". | [ ] |
| T-009-02 | "Etiquete todas as portas desta vista, só a prévia." | `status: dry_run`, `found: 8`, `tagged: 8`, `skipped: {}`, `failed_count: 0` e sem `tag_ids`. Nenhuma etiqueta é criada. | [ ] |
| T-009-03 | "Agora de verdade." Depois Ctrl+Z e Ctrl+Y. | 1 diálogo **"Tag elements in a view"**, `tagged: 8`, 8 `tag_ids` e 8 etiquetas. Ctrl+Z ("MCP: tag elements") apaga as 8. | [ ] |
| T-009-04 | "Etiquete as portas de novo, de verdade." | **Sem diálogo**, `tagged: 0` e `skipped: {already_tagged: 8}`. Nada duplicado. | [ ] |
| T-009-05 | Apague 3 etiquetas de ambiente à mão. "Etiquete os Ambientes desta vista (prévia, depois de verdade)." | `skipped.already_tagged: 5` (ou 6, com o Deck) e `tagged: 3`. Ambientes não fechados entram em `skipped.not_placed`. | [ ] |
| T-009-06 | Na planta de área: "Etiquete as Áreas desta vista, de verdade." | `tagged` = nº de áreas, com nome e área corretos. | [ ] |
| T-009-07 | "Etiquete as janelas com o tipo 'Inexistente', só a prévia." Depois com um nome real de `available`. | 1º: 400 "Tag type 'Inexistente' not found" com `available`. 2º: `tag_type` = o nome pedido. | [ ] |
| T-009-08 | Carregue a etiqueta multicategoria e apague as etiquetas de porta. "Etiquete as portas com a multicategoria '<nome>', de verdade." | As portas recebem a multicategoria, e não a de porta. | [ ] |
| T-009-09 | "Etiquete as portas na vista 'Térreo', só a prévia." | 400: "2 views named 'Térreo' (CeilingPlan, FloorPlan); use view_id" (3, com AreaPlan, se a planta de área também se chamar "Térreo"). Com `view_id` funciona. | [ ] |
| T-009-10 | Observe se o Revit avisa algo (ex.: etiquetar num corte). | Nenhuma janela trava o pedido, e os avisos vêm em `warnings`. | [ ] |
| T-009-11 | Repita T-009-02 a T-009-04 num **Revit 2025+** (se houver). | As mesmas contagens. Ids grandes certos. | [ ] |

### 010 — Exportação (export_schedule, export_rooms)
Precisa:
- os ambientes do 011;
- a "Tabela de portas" **agrupada por Nível**, com cabeçalho de grupo e o campo "Família e tipo" **oculto** no meio;
- um projeto grande, para o T-010-08.

| ID | O que pedir / fazer | Resultado esperado | OK? |
|---|---|---|---|
| T-010-01 | "Exporte a Tabela de portas em JSON." | `columns` com os cabeçalhos em português, sem "Família e tipo". `rows` alinhadas e sem linhas em branco de agrupamento. `truncated: false`. | [ ] |
| T-010-02 | Confira a coluna oculta no T-010-01. | Nenhum valor de "Família e tipo", e cada valor na coluna certa. | [ ] |
| T-010-03 | "Exporte em CSV e me mostre o texto." Cole no Excel (Texto para colunas, vírgula) e no Google Sheets. | Separador vírgula e acentos certos. Valores como "0,80" vêm entre aspas, numa coluna só. (confirmar se o Excel pt-BR separa sozinho) | [ ] |
| T-010-04 | "Exporte a tabela 'Tabela inexistente'." | `Error: 404` com `available` e `available_total`. | [ ] |
| T-010-05 | Numa cópia: apague o ambiente Circulação **na planta** (fica não colocado) e a parede y = 1,80. "Exporte os ambientes em JSON." | `counts` com `unplaced: 1` e `not_enclosed: 1`. `total_area_m2` só com os colocados. Com `include_unplaced=false`, os dois somem. | [ ] |
| T-010-06 | "Exporte os ambientes do nível Térreo." Depois "Terreo" (sem acento) e "Pavimento 9". | Térreo traz os 8 (+ Deck). "Terreo" (sem acento) e "Pavimento 9" **não** são encontrados: `total: 0` com `levels_with_rooms`. | [ ] |
| T-010-07 | Faça uma tabela de ambientes com Área no Revit e compare. | `area_m2` = Área da tabela (2 casas), e `total_area_m2` = soma da tabela. `perimeter_m` confere. `volume_m3` é null se o cálculo de volume estiver desligado. | [ ] |
| T-010-08 | No projeto grande: "Exporte a tabela <milhares de linhas> com limite 1000." | Resposta em menos de 120 s, `returned: 1000`, `truncated: true` e `body_rows` = total real. | [ ] |

### 001 a 004 — itens ainda abertos

| ID | O que pedir / fazer | Resultado esperado | OK? |
|---|---|---|---|
| T-001-01 | Selecione 3 portas. "O que está selecionado?" | `total: 3`, com `category` "Portas", família/tipo iguais às Propriedades, `level: "Térreo"` e `view_specific: false`. | [ ] |
| T-001-02 | Selecione cerca de 1500 elementos num projeto grande. "Liste a seleção com limite 200." | `total` ≈ 1500, `returned: 200`, `truncated: true`. | [ ] |
| T-001-03 | Repita o T-001-01 num Revit anterior ao 2024 (se houver). | Ids iguais aos de Gerenciar > IDs de seleção. | [ ] |
| T-002-01 | Com um parâmetro compartilhado de instância em Portas (ex.: "CV_Acabamento"), selecione P1: "Leia os parâmetros com filtro 'CV_'." | `shared: true`, `builtin: null`, `read_only: false`. | [ ] |
| T-002-02 | "Leia os parâmetros destes 51 ids: ..." | `Error: at most 50 elements per call, got 51`, antes de chamar o Revit. | [ ] |
| T-003-01 | Ctrl+S. "Mude a Marca das portas P1–P8 para P01–P08, só a prévia." | `status: dry_run`, `applied: 0` e `before`/`after` por porta. Nada no Desfazer. | [ ] |
| T-003-02 | No projeto grande: "Marca de 100 portas = P001–P100, de verdade." Depois Ctrl+Z. | 1 diálogo **"Set element parameters"**, `applied: 100`. Um Ctrl+Z ("MCP: bulk set parameters") volta as 100. | [ ] |
| T-003-03 | "Marca da P1 = 'X1'; Comprimento da parede 1 = '5'; Marca da P2 = 'X2', de verdade." | Abre o diálogo **Set element parameters** (Permitir). Depois `status: error`, `applied: 0`, "1 of 3 changes are invalid; nothing was applied", `errors[{index: 1, error: "Parameter is read-only"}]`. Nada muda e o Desfazer não ganha item. | [ ] |
| T-003-04 | "Altura do peitoril da J1 = '110 cm', só a prévia." Repita no projeto em pés. | Metros: `after` 1,10 m. Pés: ≈ 3' 7 5/16". `ok: true` nos dois. | [ ] |
| T-003-05 | "Delimitação de ambiente da parede 5 = true e da parede 6 = 'não', só a prévia." | `after` "Sim" e "Não" (confirmar o nome do parâmetro em pt-BR). | [ ] |
| T-003-06 | "Altura do peitoril da J1 = '1,10' com normalização de números, só a prévia." Repita num projeto com ponto decimal. | pt-BR: `number_format.decimal_symbol: ","` e o valor é aceito. Com ponto: `decimal_symbol: "."` e o item traz `sent: "1.10"`. Um texto (Comentários = "1,10") não muda. | [ ] |
| T-003-07 | Duas portas do mesmo tipo, "Comentários de tipo = 'Porta interna' nas duas, usando o tipo, só a prévia". Depois com valores diferentes. | Iguais: `collapsed: [{index: 1, same_as: 0}]`. Diferentes: "Changes [0, 1] set different values on the same type parameter" e nada aplicado. | [ ] |
| T-004-01 | No projeto grande (> 300 MB): "Rode o relatório de saúde do modelo." Cronometre. | Menos de 60 s e menos de 20 KB, sem `section_errors`. | [ ] |
| T-004-02 | Com 1 DWG importado e 1 vinculado: "Rode o relatório." | `imported_cad.count: 1` e `linked_cad.count: 1`. | [ ] |
| T-004-03 | Compare com Gerenciar > Revisar avisos. | `warnings.total` = total do Revit. | [ ] |
| T-FMT-01 | Planta Térreo ativa: "Liste os elementos da vista atual com limite 5." | JSON com `truncated: true`, `returned_elements: 5`, os 5 `elements` e a `message` "Results truncated...". Os elementos vêm **junto** com o aviso de corte. No smoke, esse passo deixa de dar aviso. | [ ] |

---

## Parte 3 — Tools 012–020

### 012 — audit_template
Precisa: o `.rte` do escritório, um `.rvt` grande, a regra de nomes de vistas (regex) e um cronômetro.

| ID | O que pedir / fazer | Resultado esperado | OK? |
|---|---|---|---|
| T-012-01 | No `.rte`: "Faça a auditoria completa do template aberto." | Menos de 240 s, `is_template_file: true`, sem `section_errors` nem `time_budget`. 16 seções e `checklist` com 19 itens. | [ ] |
| T-012-02 | "Qual é o `unused_source`?" | `"revit_purge"`. Se vier `"instance_count"`, anote. | [ ] |
| T-012-03 | "Veio `unused_source_note`?" | Não deve vir. Se vier, anote e confira o T-012-05. | [ ] |
| T-012-04 | No `.rvt` grande: "Audite o projeto inteiro." Se passar de 4 min: "Rode de novo só com as seções que ficaram de fora." | Pode vir `time_budget.skipped_sections`. A 2ª chamada termina sem `time_budget`. | [ ] |
| T-012-05 | "Quantos itens o `purge_preview` removeria, por tipo?" Compare com Gerenciar > Limpar não utilizados. | `purge_preview.count` e `by_kind` batem com o Revit. Modelos e filtros ficam à parte, em `not_in_revit_purge`. | [ ] |
| T-012-06 | "Mostre a organização do navegador atual e as pastas." | `browser_organization.items[0]` com `current: true` e `folders` em português. | [ ] |
| T-012-07 | "Liste os filtros de fase e a apresentação de cada um." | `presentation` com `new/existing/demolished/temporary` = `ShowByCategory`/`ShowOverriden`/`DontShow`, iguais a Gerenciar > Fases. | [ ] |
| T-012-08 | "Liste os estilos de linha com espessura, cor e padrão." | `weight`, `color` (hex) e `pattern` iguais a Estilos de linha. | [ ] |
| T-012-09 | "Audite só a nomenclatura com estas regras: vistas `^(PB\|CT\|EL)-.+$`, folhas `^A-\d{3}$`." (use as regras reais) | `naming.views` e `.sheet_numbers` com `violations_count` e `violations[]`. As vistas listadas são as que o escritório reconhece como fora do padrão. | [ ] |
| T-012-10 | Auditoria completa do `.rvt` grande, cronometrada. | Anote o tempo e se houve `time_budget`. | [ ] |

### 013 — check_sheets
Precisa: um RVT do escritório com:
- folhas emitidas e em andamento;
- uma vista vazia numa folha, e uma vista que só mostra um vínculo;
- uma elevação só com a marca e os níveis;
- uma 3D em perspectiva numa folha;
- um carimbo com parâmetro de tipo;
- uma folha com revisão e outra sem.

| ID | O que pedir / fazer | Resultado esperado | OK? |
|---|---|---|---|
| T-013-01 | "Confira as pranchas com este checklist: planta de layout = folhas com 'layout' no nome, exige planta com 'layout' no nome, legenda 'legenda.*layout\|mobili', escalas 1:50 ou 1:75, carimbo com 'Desenhado por' e data de emissão, revisão obrigatória, conferência manual 'cotas gerais'." | `summary` com `sheets_checked`, `ok/missing/review` e `problems_by_check`. `problems[].detail` em português e itens manuais em `manual_checks`. Confira 3 folhas à mão. | [ ] |
| T-013-02 | "Confira só as checagens genéricas em todas as folhas." Cronometre. | A vista vazia gera `empty_viewport`. A vista só com vínculo **não** gera. `summary.empty_check_skipped` fica ausente ou 0. | [ ] |
| T-013-03 | Com a elevação só com marca e níveis, repita. | Ela aparece como `empty_viewport` e a folha fica em `review`. | [ ] |
| T-013-04 | "Exija o parâmetro '<parâmetro de tipo do carimbo>' preenchido." | Preenchido: nada é apontado. Vazio no tipo: "parâmetro 'X' vazio". | [ ] |
| T-013-05 | Folha com a 3D em perspectiva: "Escalas permitidas só 1:50." | A perspectiva **não** gera `scale`. Uma planta 1:100 gera "vista 'X' em 1:100 (permitidas: 1:50)". | [ ] |
| T-013-06 | "Exija os parâmetros do carimbo <nomes reais>, mais 'Nome do cliente' e 'Número do projeto'." | Preenchidos: nada é apontado. Vazios em Informações do projeto: "parâmetro 'X' vazio". Nome inexistente: "não encontrado na folha, no carimbo...". | [ ] |
| T-013-07 | "Confira A-101 e A-102 exigindo revisão" (uma com e outra sem). | Com revisão: `revision` preenchido. Sem: "folha sem revisão atual" e `status: missing`. | [ ] |
| T-013-08 | "Quantas vistas fora de folha? Compare com o relatório de saúde." | `unplaced_views.count` ≈ `views.not_on_sheets` do health report quando `unplaced_include_schedules=true`. Pode haver diferença pequena (confirmar). | [ ] |

### 014 — check_render_materials
Precisa: o modelo real com a 3D do Lumion e famílias de teste:
- uma com faces `<Por categoria>`;
- uma sem material;
- o material "Madeira" em 3 ou mais famílias;
- uma família pintada;
- materiais com acento (`POLTRONA_MADEIRA_PÉ_03`);
- uma família com aninhada compartilhada;
- uma família com SKP/DWG importado.

| ID | O que pedir / fazer | Resultado esperado | OK? |
|---|---|---|---|
| T-014-01 | "Confira os materiais para render dos elementos visíveis na vista '<3D do Lumion>'." | `view`, `elements_checked`, `summary` e `problems[]` (erros primeiro). Uma vista inexistente dá 404. | [ ] |
| T-014-02 | Veja a família `<Por categoria>`. | `rule: "category_material_only"`, `severity: "error"`. | [ ] |
| T-014-03 | Veja a família sem material. | `rule: "no_material"`, `severity: "error"`. | [ ] |
| T-014-04 | Veja o material "Madeira". | `rule: "generic_shared"` com as 3+ famílias, "Used by 3 different families (max 2)". | [ ] |
| T-014-05 | Veja a família pintada. | `painted: [...]` e `summary.painted_types` ≥ 1. **Não** cai em `category_material_only`/`no_material`. | [ ] |
| T-014-06 | Veja `..._PÉ_03` e `..._AÇO_...`. | **Não** aparecem em `name_pattern`. | [ ] |
| T-014-07 | Sem filtro de vista, no modelo grande, cronometrado. | Menos de 120 s. Anote o tempo. | [ ] |
| T-014-08 | Veja a família com aninhada compartilhada. | Os materiais da aninhada entram na família-mãe, e `single_material` não dispara à toa. | [ ] |
| T-014-09 | Veja a família com SKP/DWG. | `note` de geometria só de malhas e `summary.mesh_only_types` ≥ 1. | [ ] |

### 015 — room_finishes e quantities_by_rule
Precisa: um **ambiente de referência retangular de 3,00 × 4,00 m** (face a face do acabamento) com:
- 1 porta 0,80 × 2,10 para outro ambiente;
- 1 janela 2,00 × 1,20 numa parede externa;
- altura de 2,70 m (Limite superior = o próprio nível, Deslocamento limite = 2,70), ou `wall_height_m = 2.70` no pedido.

Para os outros itens, precisa também de:
- um ambiente com forro modelado;
- uma laje única sob vários ambientes;
- uma linha separadora;
- um projeto de reforma com paredes e portas demolidas;
- uma parede empilhada e um trecho de parede cortina;
- uma laje estrutural com piso de acabamento por cima;
- um modelo com ponto base deslocado;
- uma janela 2,00 × 1,75.

**Conta de referência (regra padrão: vão acima de 2,00 m² descontado inteiro, 2 casas):**

| Grandeza | Conta | Valor esperado |
|---|---|---|
| Área do piso | 3,00 × 4,00 | **12,00 m²** |
| Perímetro (`perimeter_m`, `walls.boundary_length_m`) | 2 × (3,00 + 4,00) | **14,00 m** |
| Parede bruta (`walls.gross_m2`) | 14,00 × 2,70 | **37,80 m²** |
| Porta | 0,80 × 2,10 = 1,68 m² ≤ 2,00 | `deducted: false` |
| Janela | 2,00 × 1,20 = 2,40 m² > 2,00 | `deducted: true`, `deducted_m2: 2.4` |
| Parede líquida (`walls.net_m2`) | 37,80 − 2,40 | **35,40 m²** |
| Rodapé (`skirting.length_m`) | 14,00 − 0,80 | **13,20 m** |
| Forro (sem forro modelado) | área do ambiente | **12,00 m²**, `source: "room_area"` |
| Pintura (`painting.area_m2`) | 35,40 + 12,00 | **47,40 m²** |
| `deduct_openings="all"` | 37,80 − 4,08 | líquida **33,72**, pintura **45,72** |
| `deduct_openings="none"` | 37,80 | líquida **37,80**, pintura **49,80** |
| `excess_over_threshold` | janela 2,40 − 2,00 = 0,40 | líquida **37,40** |

| ID | O que pedir / fazer | Resultado esperado | OK? |
|---|---|---|---|
| T-015-01 | "Mostre os acabamentos do ambiente <número de referência>." | Os números da tabela acima. `height.source` = `"room_unbounded_height"` (ou `"rule:wall_height_m"`). As duas aberturas trazem `size_source`. | [ ] |
| T-015-02 | "Repita descontando todos os vãos", depois "sem descontar nenhum". | Mudam só as paredes e a pintura. Piso, forro, perímetro e rodapé ficam iguais. | [ ] |
| T-015-03 | "Acabamentos do ambiente com forro modelado." | `height.source: "ceiling_height"` e `ceiling.source: "modeled_elements"`, com área igual à da tabela de forros. | [ ] |
| T-015-04 | "Acabamentos dos ambientes sobre a laje única." | `floor.source: "room_area"` com `note`. | [ ] |
| T-015-05 | "Acabamentos do ambiente com linha separadora." | A linha fica fora de parede, pintura e rodapé, com nota em `notes`. | [ ] |
| T-015-06 | "Acabamentos dos dois ambientes ligados pela porta <marca>." | A porta aparece nos dois. | [ ] |
| T-015-07 | Na reforma, sem fase: "Acabamentos de todos os ambientes do Térreo." | `rules.phase` = última fase. Os da fase Existente vão para `skipped.other_phase`. Os demolidos ficam fora. | [ ] |
| T-015-08 | "Na fase 'Existente'", depois "na fase <id>", depois "na fase 'Projeto Futuro'". | Nome e id funcionam. A fase inexistente dá 404 com a lista de fases. | [ ] |
| T-015-09 | "Quantitativo de alvenaria do nível com parede empilhada." | A empilhada aparece **uma vez**, com volume igual à soma das subparedes e os vãos descontados. | [ ] |
| T-015-10 | "Acabamentos do ambiente com parede cortina." | A cortina fica fora de paredes, pintura e rodapé. `walls.curtain_length_m` e nota. | [ ] |
| T-015-11 | "Quantitativo de pisos do nível com laje estrutural + acabamento." | Só o acabamento conta. A laje vai para `notes`. | [ ] |
| T-015-12 | No modelo com base point deslocado: "Acabamentos de 3 ambientes com piso e forro modelados." | `floor.source` e `ceiling.source` = `"modeled_elements"`. | [ ] |
| T-015-13 | No ambiente de referência, troque a janela 2,00 × 1,20 por uma 2,00 × 1,75: "Acabamentos descontando só o excedente acima de 2 m²." | `area_m2: 3.5`, `deducted_m2: 1.5`, líquida **36,30 m²**. | [ ] |
| T-015-14 | No projeto grande: "Quantitativos por regra de todos os grupos." Cronometre. | Menos de 120 s. Anote o tempo. | [ ] |
| T-015-15 | "Quantitativo de esquadrias" no modelo do escritório. | `size_source` aponta parâmetros que existem, sem `width_m`/`height_m` vazios. | [ ] |
| T-015-16 | "Quantitativos por regra em CSV." Salve em `.csv` UTF-8 e abra no Excel por Dados > De Texto/CSV. | As colunas `group, item, measure, unit, quantity, quantity_with_waste, rule` com acentos. O separador é vírgula e o decimal é ponto: com duplo clique no Excel pt-BR deve ficar tudo numa coluna (confirmar). | [ ] |
| T-015-17 | Leve a seção "Decisões para confirmar" ao escritório. | Padrões confirmados ou ajustados. | [ ] |

### 016 — phases_report e set_element_phases
Precisa: o projeto (d) com:
- paredes, portas e janelas;
- uma planta de reforma;
- uma planta com filtro "Mostrar novo";
- uma tabela filtrada por fase;
- um nível com mais de 200 elementos.

| ID | O que pedir / fazer | Resultado esperado | OK? |
|---|---|---|---|
| T-016-01 | "Gere o relatório de fases." | Fases em ordem. `phase_filters[].presentation` igual a Gerenciar > Fases > Filtros de fase. | [ ] |
| T-016-02 | "Relatório só de Paredes e Portas, por categoria." | As contagens de `combinations[]` batem com a tabela filtrada por fase. | [ ] |
| T-016-03 | Veja `views` no relatório. | As vistas agrupadas por fase e filtro, iguais ao navegador. | [ ] |
| T-016-04 | Selecione 5 paredes: "Prévia: paredes selecionadas criadas em 'Existente'." | `status: "dry_run"`, `matched: 5`, `before`/`after`. Sem diálogo e nada no Desfazer. | [ ] |
| T-016-05 | "Aplique: todas as Paredes do Térreo criadas em Existente." (prévia e depois real) | 1 diálogo "Set element phases", `changed: N`. **Um** Ctrl+Z ("MCP: set element phases"). | [ ] |
| T-016-06 | "Demolir em 'Nova construção' as paredes <ids>." Abra a planta de reforma. | As paredes ficam tracejadas. | [ ] |
| T-016-07 | "Limpe a demolição dessas paredes." | `after.demolished_phase: null`. | [ ] |
| T-016-08 | "Parede <id> criada em 'Nova construção' e demolida em 'Existente'." | Erro "1 of 1 elements are invalid; nothing was changed", "Demolished phase 'Existente' comes before created phase 'Nova construção'". | [ ] |
| T-016-09 | "Prévia: fase Existente para Paredes do Térreo", depois Portas e Janelas. Depois 3 elementos selecionados, "use a seleção". | Cada categoria traz só o Térreo, e portas e janelas entram. Com a seleção, `matched: 3`. | [ ] |
| T-016-10 | "Prévia: fase Existente para todas as Paredes" (mais de 200). | 400 pedindo filtro. | [ ] |
| T-016-11 | "Prévia usando a vista <tabela>", depois "<folha>", depois "<planta Mostrar novo>". | Tabela e folha dão 400. Na planta, só entram os visíveis. | [ ] |
| T-016-12 | Prévia com 3 selecionados. Mude a seleção para 4: "Aplique com expected_count 3." | 409, **sem diálogo** e sem alteração. | [ ] |
| T-016-13 | Leia o diálogo do T-016-05. | Mostra `_resolved_count` e `_element_ids`. | [ ] |
| T-016-14 | Peça uma fase inválida (demolição antes da criação) com a outra fase sem mudar. | Recusada antes da transação, com erro por elemento. Anote a mensagem (confirmar `IsPhaseDemolishedValid` no 2024). | [ ] |
| T-016-15 | Demolir uma parede que hospeda portas e janelas. | Conclui, com os avisos em `warnings`, sem janela travando. | [ ] |
| T-016-16 | Todos os pedidos usando "Nova construção" (com cedilha e til). | Entrada e saída com os acentos certos. | [ ] |

### 017 — export_sheets_pdf
Precisa:
- cerca de 30 folhas, algumas de espaço reservado;
- folhas A1, A2 e A3 com revisão;
- uma folha com acento e `/` no nome;
- o conjunto de folhas "Emissão Executivo";
- o Número do projeto preenchido;
- um leitor de PDF;
- uma unidade `Z:` mapeada, uma pasta-junção (`mklink /J`) e um pendrive.

| ID | O que pedir / fazer | Resultado esperado | OK? |
|---|---|---|---|
| T-017-01 | "Prévia da emissão de todas as folhas em PDF." | `status: "dry_run"`, `folder` em `Documentos\RevitMCP\Emissoes\<projeto>\<AAAA-MM-DD_HHMMSS>` e `files[]` em ordem natural (A-2 antes de A-10). As de espaço reservado vão para `skipped` (`placeholder`). Nenhum arquivo é criado. | [ ] |
| T-017-02 | "Exporte de verdade A-101 e A-102, na pasta e com a data da prévia." | 1 diálogo "Export 2 PDF(s) to <pasta>", `exported: 2`, com o nome exato da prévia (ex.: `P-07_A-101_R02_2026-10-21.pdf`). | [ ] |
| T-017-03 | Abra a pasta no Explorador. | Cada arquivo termina em `.pdf` uma vez só. | [ ] |
| T-017-04 | Exporte A1, A2 e A3 no padrão, e depois "A-101 com zoom 100%". | No padrão, cada PDF sai no tamanho da folha. Com 100%, a escala impressa confere. | [ ] |
| T-017-05 | Veja os `warnings` do T-017-02 e do T-017-04. | Vazios: todas as opções foram aceitas no 2024. Se o Revit recusar uma opção crítica, vem 501 com `details`. | [ ] |
| T-017-06 | Abra os PDFs. | Sem regiões de recorte, caixas de escopo nem planos de referência. | [ ] |
| T-017-07 | "Exporte A-103, A-101 e A-102, nessa ordem, num PDF único." | Um arquivo com as páginas nessa ordem. | [ ] |
| T-017-08 | "Prévia do conjunto 'Emissão Executivo'." Depois um nome errado. | Encontra o conjunto. O nome errado dá 404 com `available_sheet_sets`. | [ ] |
| T-017-09 | Abra o `emissao.json`. | `revision` e `revision_date` iguais aos do carimbo. A folha sem revisão sai sem o trecho de revisão no nome. | [ ] |
| T-017-10 | Folha com acento e `/`, padrão `{sheet_number}_{sheet_name}`. | No nome do arquivo, `/` vira `-` e o acento é mantido. O `emissao.json` abre com os acentos certos no Python/Notion. | [ ] |
| T-017-11 | Num PC sem `Documentos\RevitMCP`, execução real sem `folder`. Repita com Documentos no OneDrive. | A pasta é criada nos Documentos reais. | [ ] |
| T-017-12 | "Prévia para C:\Windows\Temp" e "para \\\\servidor\\projetos". | 400 nos dois, antes de qualquer diálogo. | [ ] |
| T-017-13 | Repita o T-017-02 sem `overwrite`. Depois "pode substituir". | Sem `overwrite`: 409 com `existing`, e nada exportado. Com substituição: o diálogo diz "and REPLACE 2 existing file(s)" e substitui. | [ ] |
| T-017-14 | Qualquer execução real. | Sem erro de "transação aberta" ou "contexto de API". | [ ] |
| T-017-15 | Exporte cerca de 30 folhas, cronometrado. | Dentro do tempo (≥ 600 s ou 20 s por folha). Se der timeout, confira a pasta antes de repetir. | [ ] |
| T-017-16 | Padrão `{project_number}_{sheet_number}_R1.2_{date}`. | `P-07_A-101_R1.2_2026-10-21.pdf`, com o nome inteiro. | [ ] |
| T-017-17 | Repita o T-017-07 na ordem A-102, A-103, A-101. | As páginas seguem a nova ordem. | [ ] |
| T-017-18 | Execução real. Com o diálogo aberto, copie para a pasta um arquivo com o nome planejado. Depois aprove. | 409, nada exportado. | [ ] |
| T-017-19 | Com um PDF da pasta aberto no Acrobat, exporte com substituição. | `Error: 423 - file in use or locked: ...`. Os originais ficam intactos e sem `.bak`. | [ ] |
| T-017-20 | Provoque a falha de uma folha com substituição ligada (confirmar como). | `status: "error"`, "1 of N PDF file(s) failed; the others were written". O PDF anterior volta, sem `.bak-*` sobrando. | [ ] |
| T-017-21 | Segunda emissão real na mesma pasta. | `emissao.json` novo, `emissao.json.bak-AAAAMMDD_HHMMSS` e `emissao_historico.jsonl` com **2 linhas**. | [ ] |
| T-017-22 | Exporte para "Z:\\Emissao", "D:\\JuncaoTeste\\x" e "E:\\Emissao" (pendrive). | `Z:` e a junção dão 400. O pendrive é aceito. | [ ] |
| T-017-23 | Leia o diálogo de uma execução com substituição. | Mostra `_resolved_folder`, `_files` e `_will_replace`. | [ ] |
| T-017-24 | Veja os `warnings` de uma execução real. | Sem aviso sobre `StopOnError` nem `PaperPlacement`. | [ ] |

### 018 — renumber_elements
Precisa:
- ambientes numerados com portas, incluindo uma porta de fachada e uma porta sem ambiente;
- uma planta da fase Existente;
- uma porta em grupo;
- uma tabela, uma folha e uma legenda;
- janelas em 2 níveis;
- ambientes não colocados;
- as folhas A-101 e A-102;
- mobiliário;
- mais de 200 portas.

| ID | O que pedir / fazer | Resultado esperado | OK? |
|---|---|---|---|
| T-018-01 | "Prévia: renumere as portas do Térreo por ambiente com o padrão P-{room_number}-{seq:02}." | `results[]` com `before`, `after`, `room_number` e `level`. Cada porta pega o ambiente do lado "para" (confira 3). A ordem segue a leitura. | [ ] |
| T-018-02 | Veja a porta de fachada e a porta solta. | A de fachada usa o único lado com ambiente. A solta dá erro por elemento, e nada é alterado. | [ ] |
| T-018-03 | "Mesma prévia usando a vista <planta Existente>", depois "com a fase 'Nova construção'". | `phase` = a fase da vista. Com a fase explícita, vale a dada. | [ ] |
| T-018-04 | Veja a porta criada em Existente. | `room_phase: "Existente"`. | [ ] |
| T-018-05 | Inclua a porta do grupo. | Erro por elemento ("edit the group in Revit"), com o grupo intacto. | [ ] |
| T-018-06 | "Prévia com a vista <tabela>", depois "<folha>" e "<legenda>". | 400 nos três. | [ ] |
| T-018-07 | "Prévia: janelas por nível, padrão J-{level}-{seq:02}, agrupando por nível." | A sequência recomeça em cada nível. | [ ] |
| T-018-08 | "Prévia: ambientes do 1º Pavimento em sequência a partir de 101." | 101, 102... Os não colocados vão para `skipped.unplaced_rooms`. | [ ] |
| T-018-09 | Peça um número já usado na **mesma fase**. Depois um usado só em **outra fase**. | Mesma fase: erro de colisão. Outra fase: aceito, com `skipped.other_phase_rooms`. | [ ] |
| T-018-10 | "Troque os números das folhas A-101 e A-102 (ids <a>, <b>), na ordem dos ids." Prévia e depois real. | Os números trocados e nenhuma folha `MCP-TMP-*` sobrando. | [ ] |
| T-018-11 | "Renumere a folha A-105 para A-102" (A-102 fora do lote). | "'A-102' is already used by an element outside the batch". Nada muda. | [ ] |
| T-018-12 | "Prévia: Marca do Mobiliário com o padrão M{seq:03}, parâmetro 'Marca'." | M001, M002... | [ ] |
| T-018-13 | Gere uma Marca já usada fora do lote. | Vem em `warnings`, não como erro. | [ ] |
| T-018-14 | Uma prévia e depois a real. | A prévia não deixa nada no Desfazer. A real deixa **um** "MCP: renumber elements". | [ ] |
| T-018-15 | Na execução real. | O diálogo "Renumber elements" aparece **uma vez**. | [ ] |
| T-018-16 | "Prévia: renumere todas as portas" (mais de 200). | 400 pedindo filtro. | [ ] |

### 019 — import_parameters_from_csv
Precisa:
- a pasta `Documentos\RevitMCP\Importar` (ou `REVIT_MCP_IMPORT_DIR`) e o Excel pt-BR;
- um parâmetro de texto de acabamento nos ambientes e um parâmetro de comprimento;
- duas portas do mesmo tipo;
- um número de ambiente repetido em 2 níveis.

| ID | O que pedir / fazer | Resultado esperado | OK? |
|---|---|---|---|
| T-019-01 | "Exporte os ambientes em CSV." Salve como `ambientes.csv` em Importar, troque 3 nomes (com acento) no Excel e salve como "CSV (separado por vírgulas)". "Prévia: importe ambientes.csv, coluna name → ROOM_NAME." | `before`/`after`/`row`/`column` com os acentos certos. `import_report` com `encoding` `cp1252`, `delimiter: ";"` e `rows_used`. | [ ] |
| T-019-02 | Planilha com `Número` e `Acabamento de piso`. "Prévia: importe por número de ambiente." Depois "Aplique." | **Um** diálogo "Set element parameters" e **um** Ctrl+Z ("MCP: bulk set parameters"). | [ ] |
| T-019-03 | Número repetido em 2 níveis, sem nível. Depois "só no Térreo". | Sem nível: `skipped` com "room number is used by 2 rooms (levels: …); pass level". Com nível: passa. | [ ] |
| T-019-04 | Coluna de comprimento com `2,40` e `2.40`, e uma de texto com `2,40`. Prévia. | Os dois comprimentos são aceitos (`sent`). `number_format.decimal_symbol` = o símbolo do projeto. O texto fica `2,40`. | [ ] |
| T-019-05 | Duas portas do mesmo tipo, gravando no tipo, com valores diferentes e depois iguais. | Diferentes: erro com os índices, nada aplicado. Iguais: aplicado uma vez (`collapsed`). | [ ] |
| T-019-06 | Célula vazia numa coluna de comprimento e noutra de texto, com `set_empty`. | Comprimento: aviso em `warnings`. Texto: o valor fica vazio. | [ ] |
| T-019-07 | "Importe C:\\Temp\\x.csv." | "Error: file_path must be inside the import folder …". O modo rede é o S-22. | [ ] |
| T-019-08 | Coluna que não é parâmetro (ex.: `Acabamento piso X`). Prévia. | `status: "error"` com o `row` e a `column` certos. Nada aplicado. | [ ] |
| T-019-09 | Planilha com mais de 500 alterações. | "Error: the spreadsheet gives N changes, at most 500 per call…", sem chamar o Revit. | [ ] |
| T-019-10 | Execução real e **Recusar**. | Nada muda e o Desfazer não ganha item. | [ ] |

### 020 — create_floors_from_rooms e create_ceilings_from_rooms
Precisa:
- no Térreo, ambientes:
  - com pilar no meio;
  - com parede curva;
  - com piso de acabamento já modelado;
  - com base 0,10 e limite 2,70 no próprio nível;
  - com limite no nível de cima;
  - na fase Existente;
- uma laje estrutural sob o pavimento;
- na reforma, um piso existente demolido.

| ID | O que pedir / fazer | Resultado esperado | OK? |
|---|---|---|---|
| T-020-01 | "Prévia: crie pisos em todos os ambientes do Térreo." | `created: 0`, `area_m2` próximo de `room_area_m2` e `total_area_m2`. Sem diálogo. | [ ] |
| T-020-02 | "Crie de verdade (expected_count = <da prévia>)." | 1 diálogo "Create floors from rooms". **Um** Ctrl+Z ("MCP: create floors from rooms") apaga todos. | [ ] |
| T-020-03 | "Pisos com deslocamento 0,05 m no ambiente <n>." | Nível do ambiente e "Deslocamento de altura do nível" = 0,05. | [ ] |
| T-020-04 | Piso do ambiente com pilar, depois `room_finishes` dele. | Piso com furo (`loops: 2`) e `floor.source: "modeled_elements"`. | [ ] |
| T-020-05 | Piso do ambiente com parede curva. | Arco aceito, sem `invalid_boundary`. | [ ] |
| T-020-06 | Prévia no Térreo com laje estrutural e com o ambiente que já tem piso. | A laje não faz pular ninguém. O ambiente com piso vai para `skipped` (`existing_floor`). | [ ] |
| T-020-07 | "Prévia do ambiente <n> pelo eixo das paredes." | `boundary_location: "center"` e `area_m2` maior que pelo acabamento. | [ ] |
| T-020-08 | "Forros a 2,60 m nos ambientes <n>." Depois `room_finishes`. | Altura 2,60 (`height_source: "height_m"`). Em `room_finishes`: `height.source: "ceiling_height"`. | [ ] |
| T-020-09 | "Prévia de forros sem altura" nos 2 ambientes de teste de altura. | Base 0,10 e limite 2,70: forro a **2,70 m** (`room_top`). Limite no nível de cima: `skipped`, `no_ceiling_height`. | [ ] |
| T-020-10 | Piso do ambiente da fase Existente. | "Fase de criação" = Existente, e `phase: "Existente"`. | [ ] |
| T-020-11 | Na reforma: prévia dos ambientes da fase nova, onde o piso existente foi demolido. | Não é pulado. | [ ] |
| T-020-12 | Leia o diálogo da execução real. Depois repita com `expected_count` errado. | O diálogo mostra `_count` e `_rooms`. A contagem errada dá 409, sem diálogo. | [ ] |
| T-020-13 | "Prévia de pisos do tipo '<nome real em minúsculas>'." Depois um tipo inexistente. | Encontra sem diferenciar maiúsculas. O inexistente dá 400 com `available_types`. | [ ] |
| T-020-14 | Pisos que sobrepõem outro piso. | O aviso vem em `warnings`, sem janela travando. | [ ] |

---

## Riscos a observar durante os testes

- **Janelas de aviso do Revit**: o filtro de avisos (`revit_mcp/transactions.py`) deve impedir que elas travem os pedidos no Revit 2024. Se alguma abrir, anote qual tool e a mensagem.
- **`bulk_set_parameters` não usa o filtro de avisos** (só uma transação simples). Se o Revit avisar algo nos testes do 003 ou do 019 (ex.: marca duplicada), pode abrir uma janela. Anote.
- `IndependentTag.Create` e a detecção de etiquetas existentes (009).
- `NewRoomBoundaryLines` numa vista que não é a ativa (011).
- A face da parede com `location_line` (011).
- `Document.GetUnusedElements` e `BrowserOrganization` no 2024 (012).
- O tempo das conferências num `.rvt` grande (012, 013, 014, 015).

## Decisões para confirmar com o escritório

1. **Desconto de vãos (015).**
   - Hoje o vão **acima** de 2,00 m² sai inteiro, e um vão de 2,00 m² exatos não sai.
   - A alternativa (`excess_over_threshold`, à moda SINAPI/TCPO) desconta só o excedente.
   - Qual regra e qual limiar o escritório usa?
2. **Rodapé desconta a largura da porta?** Hoje sim. E a porta de correr ou janela-porta?
3. **Altura da parede.** Hoje é altura fixa → forro modelado → altura do ambiente. É até o forro ou até a laje?
4. **Pintura = paredes líquidas + teto.** O teto entra mesmo sem forro modelado? E as faces externas?
5. **Perdas.** O padrão é 0, e só aparecem em `quantity_with_waste`. Qual percentual usar por serviço?
6. **Arredondamento.** São 2 casas, só no valor final.
7. **Laje que atravessa ambientes (015/020).** Acima de 105% da área do ambiente + 0,5 m², a medição usa a área do ambiente. Essa tolerância serve?
8. **Fase medida.** É a última fase, sem os demolidos. A reforma precisa de quantitativo de demolição à parte?
9. **Números na planilha (003/019).** `"1.234"` sozinho vira **1,234 (decimal)**. `"1.234,5"` e `"1.234.567"` são lidos como milhar. As planilhas usam ponto de milhar sem decimais?
10. **Sim/Não na planilha.** São aceitos `sim/não`, `yes/no`, `true/false` e `1/0`.
11. **CSV do quantitativo (015).** Usa vírgula como separador e ponto decimal, que não abre direto no Excel pt-BR. Precisa da opção `;` com vírgula decimal?
12. **Nome dos PDFs (017).** O padrão é `{project_number}_{sheet_number}_{revision}_{date}`, na pasta `Documentos\RevitMCP\Emissoes\<projeto>\<data_hora>`. É a regra do escritório?
13. **Nome de material (014).** O padrão é `OBJETO_MATERIAL_PARTE_NN` em maiúsculas, com no máximo 2 famílias por material.
14. **Pasta de importação (019).** É `~/Documents/RevitMCP/Importar`. Com Documentos no OneDrive, `~/Documents` pode não ser a pasta do Explorador. Se for o caso, defina `REVIT_MCP_IMPORT_DIR`.
