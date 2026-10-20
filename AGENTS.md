# Regras para agentes, LLMs e assistentes de programação

Estas instruções se aplicam a todo este projeto. Leia este arquivo antes de editar, seguido de [MODULOS.md](MODULOS.md). Na interface Qt, leia também [MainWindowModules.md](desktop/src/MainWindowModules.md).

## 1. Trabalhe no pedido, dentro do setor autorizado

**Um pedido sobre contas não autoriza alterações em integrações, campanhas, carteira ou infraestrutura.** A mesma regra vale para os demais setores.

- Antes de editar, identifique o resultado solicitado, o setor dono e os arquivos que pretende alterar. Apresente esse escopo brevemente ao usuário; um pedido claro dentro do setor não precisa de confirmação adicional.
- Se o usuário pedir vários setores explicitamente, trabalhe nesses setores e nas interfaces necessárias entre eles.
- Leia código de outros setores quando necessário para entender contratos, rastrear erros ou escolher testes. Leitura não autoriza escrita fora do escopo.
- Edite apenas funções, classes e trechos relacionados ao pedido. Estar em um arquivo permitido não autoriza modificar todas as suas responsabilidades.
- Não faça refatorações paralelas, reorganização de diretórios, renomeações amplas, formatação global, atualização de dependências ou alterações cosméticas não solicitadas.
- Não corrija silenciosamente problemas independentes encontrados durante a tarefa. Registre-os separadamente, sem ampliar o trabalho.
- Não desfaça mudanças do usuário ou de outros agentes. Preserve o trabalho existente e identifique conflitos antes de editar.
- Instruções explícitas do usuário e instruções de prioridade superior prevalecem sobre este arquivo. Não invente autorização e não peça novamente uma autorização já concedida.

## 2. Mapa de propriedade e limites de escrita

Os caminhos abaixo são relativos à raiz deste projeto. Eles delimitam responsabilidades; não são autorização para editar o setor inteiro.

| Assunto solicitado | Implementação principal | Entradas e interface do mesmo setor |
| --- | --- | --- |
| Contas: listagem, persistência, saúde, organização, remoção e restrições | `moneymin/web/services/accounts_domain/`, `moneymin/web/account_health.py`, `moneymin/web/account_issues.py`, `moneymin/web/org_migration.py`; módulos de conta como `moneymin/account_transfer.py` e `moneymin/account_bans.py` | `moneymin/web/routes/accounts_domain/`, fachadas `moneymin/web/services/accounts.py` e `moneymin/web/routes/accounts.py`, `desktop/src/MainWindowAccounts.cpp`; monitor de restrições em `desktop/src/MainWindowMonitoring.cpp` quando solicitado |
| Cadastro e retomada de contas | `moneymin/web/services/registration_domain/`, `moneymin/web/registration_batch.py`, `moneymin/web/registration_state.py`, `moneymin/web/registration_limits.py`; `moneymin/registration_proxy.py` quando o pedido tratar dessa configuração | `moneymin/web/routes/registration_domain/`, fachadas `moneymin/web/services/registration.py` e `moneymin/web/routes/registration.py`, trechos de cadastro em `desktop/src/MainWindowAccounts.cpp` |
| Campanhas: catálogo, seleção, preparo, planejamento, execução e lifecycle | `moneymin/campaigns/`, `moneymin/campaign_types.py` | Fachada `moneymin/campaign.py`, `moneymin/web/services/campaigns.py`, `moneymin/web/services/catalogs.py`, rotas `moneymin/web/routes/campaign_admission.py`, `moneymin/web/routes/campaign_execution.py`, `moneymin/web/routes/campaign_original.py`, `moneymin/web/routes/catalogs.py`, `desktop/src/MainWindowCampaigns.cpp` |
| Preparação dos catálogos de provedores | `moneymin/catalog_policy.py`, `moneymin/web/routes/provider_catalogs.py`; algoritmos nos módulos de cada provedor | `desktop/src/MainWindowCatalogs.cpp`; não iniciar classificação fria na campanha nem mudar credenciais ou contas |
| Entrega de mídia: protocolo, transferência, diário, recibos, qualidade e retomada | `moneymin/delivery/` | Fachadas `moneymin/upload.py`, `moneymin/upload_types.py`, `moneymin/upload_storage.py`, `moneymin/upload_protocol_lease.py`, `moneymin/deliverable.py` |
| Saldo, carteira, credenciais de saldo e saques | `moneymin/web/services/wallet_domain/`, `moneymin/web/wallet.py` | `moneymin/web/routes/wallet_domain/`, fachadas `moneymin/web/services/wallet.py` e `moneymin/web/routes/wallet.py`, `desktop/src/MainWindowBalances.cpp` |
| Configuração de integrações: credenciais, conexões e perfis de provedores | `moneymin/web/services/integrations.py` | `moneymin/web/routes/integrations.py`, `desktop/src/MainWindowIntegrations.cpp` |
| Celular: USB, ADB, JWT de App Check, coordenação e diagnóstico móvel | `moneymin/mobile/`; provedor legado em `moneymin/roglife_appcheck/` | Fachadas `moneymin/appcheck.py`, `moneymin/integrations/android_diagnostics.py`; `moneymin/web/routes/mobile.py`; `desktop/src/AndroidDevicePanel.*`, `UsbDeviceWatcher.*`, `MainWindowMobile.cpp`; mapa em `docs/modularizacao/CELULAR.md` |
| Registro e seleção dos provedores de mídia | `moneymin/integrations/contracts.py`, `moneymin/integrations/registry.py` | Consumidores só mudam se o pedido autorizar a alteração de seu contrato |
| Ego4D | `moneymin/ego4d.py`, `moneymin/ego4d_annotations.py`, `moneymin/ego4d_library.py`, `moneymin/ego_accelerator.py` | Rotas/biblioteca/acelerador e UI correspondentes, somente para a operação solicitada |
| NymeriaPlus | `moneymin/nymeria.py`, `moneymin/nymeria_library.py`, `moneymin/nymeria_vrs.py` | `moneymin/web/routes/library_nymeria.py`, `desktop/src/MainWindowLibrary.cpp` |
| HoloAssist | `moneymin/holoassist.py`, `moneymin/holo_accelerator.py` | Rotas/acelerador e UI correspondentes |
| Histórico, recuperação, armazenamento e bibliotecas | Módulo e serviço correspondentes em `moneymin/` e `moneymin/web/` | Rotas do mesmo domínio; UI correspondente indicada em `MODULOS.md` |
| Dashboard e prontidão | Serviços de diagnóstico/prontidão correspondentes | `desktop/src/MainWindowDashboard.cpp` |

**Contas/cadastro e configuração de integrações são setores diferentes.** Por exemplo, adicionar uma conta Claru não autoriza reescrever a tela de integrações, substituir credenciais Hostinger ou mudar o registry de mídia. Reutilize as APIs existentes.

**O setor de celular é mantido separadamente pelo usuário.** Pedidos sobre campanhas, contas, catálogos, entrega ou integrações gerais não autorizam editar JWT/App Check, ADB, seu cache, configuração, provedor legado ou painel móvel. Consuma as fachadas existentes; uma alteração interna exige pedido explícito que inclua celular/autenticação móvel. Isso também vale para os símbolos móveis em arquivos compartilhados, identificados em `docs/modularizacao/CELULAR.md`; não torna esses arquivos inteiros exclusivos do setor. Autorização explícita já existente continua válida e não exige nova confirmação.

**Clientes externos são compartilhados:** `moneymin/crowtado.py`, `moneymin/claru.py`, `moneymin/hostinger_mail.py` e `moneymin/minute_api.py` podem servir a vários setores. Um problema em contas não concede autorização genérica para reescrever esses clientes. Diagnostique a operação exata e aplique a regra de mudança compartilhada abaixo.

**Infraestrutura compartilhada:** `moneymin/config.py`, persistência/segurança comuns, stores de credenciais/tokens, leases, locks, composição HTTP (`moneymin/web/server.py`, `moneymin/web/dependencies.py`, `moneymin/web/routes/registry.py`), runners compartilhados, `desktop/src/MainWindow.hpp`, `desktop/src/MainWindow.cpp`, `desktop/src/MainWindowService.cpp`, `desktop/src/MainWindowComponents.cpp`, `desktop/src/MainWindowSupport.hpp`, `ApiClient`, CMake e scripts de build/execução. Não trate esses arquivos como área livre para acomodar lógica de negócio.

## 3. Quando uma mudança atravessar setores

1. Primeiro tente resolver no módulo dono usando o contrato existente. Não contorne o contrato com acesso direto a estado de outro setor.
2. Se for necessário alterar um contrato ou arquivo compartilhado, identifique o símbolo, os consumidores afetados, a menor alteração proposta e os testes necessários.
3. Se essa mudança já estiver expressamente abrangida pelo pedido ou por autorização anterior, prossiga dentro desse limite. Caso contrário, conclua o diagnóstico e o trabalho independente no setor solicitado e peça autorização para a ampliação concreta antes de editar fora dele. Não faça a alteração primeiro e avise depois.
4. Uma entrada adicional no registry, um import/reexport, uma declaração no header ou uma linha no CMake pode ser uma adaptação necessária. Mesmo assim, justifique-a, preserve os consumidores existentes e limite o diff a essa adaptação. Não aproveite para reorganizar o arquivo compartilhado.
5. Um pedido de nova funcionalidade não autoriza reescrever toda a arquitetura, mesmo que isso pareça conveniente.

Exemplos:

- **“Corrigir a lista de contas”**: investigar/editar persistência, rota e UI de contas; validar seus contratos. Não alterar integrações ou seleção de campanhas.
- **“Corrigir a senha usada no cadastro”**: atuar no fluxo de cadastro. Não mudar a configuração de credenciais do provedor se a API existente já atende ao fluxo.
- **“Adicionar integração e usá-la nas contas”**: os dois setores estão autorizados; declarar a interface entre eles e testar ambos.
- **“Falha de contas vem do cliente Minute compartilhado”**: demonstrar a causa e a alteração mínima; obter autorização se o pedido não abranger a mudança compartilhada.

## 4. Preserve a arquitetura

- Coloque a implementação no módulo dono. Fachadas existentes conservam compatibilidade e delegação; não volte a concentrar regras em `campaign.py`, `upload.py`, `web/server.py` ou `MainWindow.cpp`.
- Features HTTP não importam `server.py`. Recebem dependências declaradas em `REQUIRES` e compostas por `DependencyBindings`.
- Não use `exec`, `eval`, injeção/cópia de globals nem imports da raiz de composição para resolver dependências de domínio.
- Preserve assinaturas públicas, decorators, ordem dos hooks, métodos/paths/endpoints HTTP e os pontos de substituição usados pelos testes. Mudanças deliberadas nesses contratos precisam estar no escopo e atualizar os consumidores/testes envolvidos.
- Dependências substituíveis continuam resolvidas na chamada pelo adaptador existente; não congele referências que atualmente admitem patch tardio.
- Preserve captures locais em callbacks. Não substitua uma variável local por uma dependência de mesmo nome; a colisão do caminho SQLite `index` com o endpoint `index` já causou regressão.
- Workers recebem entradas materializadas. Não carregue proxies Flask `request`, `g` ou `current_app` para execução fora do contexto da requisição.
- A composição registra rotas e estado antes de iniciar workers. Caches/barreiras por app não passam a ser globais; runners/locks por processo não são duplicados para “desacoplar”.
- Preserve nomes/campos persistidos, IDs de providers/tarefas/clipes, deduplicação, recibos e checkpoints. Não faça migrações de dados sem escopo e validação específicos.
- Preserve ordem de locks, leases, parada cooperativa, ownership de mídia e tratamento de efeito remoto incerto. Uma ausência de resposta não confirma falha nem autoriza repetir uma operação remota.
- Não introduza ciclos de imports de inicialização. Os componentes com imports tardios existentes estão documentados; não amplie o acoplamento como atalho.

## 5. Fronteiras da interface Qt

- Cada setor constrói e atualiza sua própria página; preserve os métodos de coordenação descritos em `MainWindowModules.md`.
- Campanhas não altera diretamente tabelas/checks de contas ou saldos. Use `notifyPermanentCampaignAccountRemoval` e os métodos do setor dono.
- Biblioteca não lê nem altera diretamente campos privados de seleção/catálogo de campanha. Use os accessors e operações de campanha existentes.
- Serviço de backend despacha disponibilidade e encerramento aos handlers do setor; não passe a controlar diretamente seus timers/widgets.
- A classe `MainWindow` ainda possui estado privado e infraestrutura comuns. Isso não autoriza acesso cruzado informal nem a criação de um novo arquivo que reúna todas as páginas.
- Se adicionar uma unidade de compilação, atualize a lista compartilhada no CMake para aplicativo e testes. Preserve ownership QObject, geração de operações/callbacks e os contratos friend dos testes, salvo mudança explicitamente autorizada.

## 6. Validação proporcional e honesta

- Execute os testes existentes que cobrem o setor alterado usando o runner isolado. Acrescente testes para comportamento novo, regressão concreta ou contrato alterado; não enfraqueça testes para aprovar uma mudança incompatível.
- Para mudanças de código que afetem as fronteiras, rode também a suíte arquitetural. Para integração final de código, execute a suíte completa; mudanças exclusivamente documentais não exigem recompilar nem rodar milhares de testes.
- Se alterar C++, compile e rode os testes nativos/Qt pertinentes. Use `-BuildOnly` para validar sem instalar um executável no ambiente do usuário.
- Se alterar código que trate mídia/timestamps, use a validação local real aplicável além das fixtures.
- Não execute teste com contas, credenciais, mídias ou dados reais por conveniência. Cadastro, campanhas, uploads, saques ou outras operações reais exigem autorização específica para a operação; um pedido de edição de código não a fornece.
- Não inicie/paralise o aplicativo nem instale um executável para uma simples edição de código. Faça isso quando estiver abrangido pelo pedido/autorização de entrega.
- Informe testes executados, resultado, skips, testes não executados e limitações. Não apresente fixtures como aceitação real do provedor nem declare “100% sem risco”.

Comandos a partir da raiz desta cópia:

```powershell
# Passe os arquivos de teste pertinentes ao setor; não use contas reais.
.\.venv\Scripts\python.exe scripts\run_tests.py --pytest -q test/test_modular_architecture.py

# Integração final de alterações de código.
.\.venv\Scripts\python.exe scripts\run_tests.py

# C++: compilar/testar sem instalar; SDK pode ser configurado por -ToolchainRoot.
.\scripts\build_local.ps1 -BuildOnly
```

Não rerode os scripts de extração única `modularize_http.py` ou `compose_http_features.py` sobre o projeto já modularizado.

## 7. Cópia ativa, dados e publicação

- Trabalhe na raiz desta cópia local do Finally. Confirme a raiz antes de escrever ou compilar.
- Não edite a origem preservada `QFar/Source-QFar-1.0.63`, outras versões ou a pasta isolada `Criador` sem pedido explícito para essas cópias.
- Use somente `build-modular2`. Os builds obsoletos `build-local` e `build-modular` foram retirados desta cópia; não recrie builds paralelos sem necessidade explícita. `dist-local` contém o aplicativo instalado e suas bibliotecas, não é outro diretório de compilação.
- Os aplicativos usam o perfil QMoney do usuário Windows. Copiar o código não isola os dados de produção. Teste com dados temporários e respeite uma instância ativa por perfil.
- Não apague contas, recibos, checkpoints, mídia ou configurações para “limpar” uma tarefa. Não exponha senhas, tokens ou credenciais em código, logs ou documentação.
- Este trabalho é local. Não faça push, publicação, release ou instalação em outro ambiente sem pedido explícito.

## 8. Revisão final e entrega

Antes de encerrar, confira:

1. Todos os arquivos alterados têm relação direta com o pedido e com o setor autorizado.
2. Cada alteração compartilhada está autorizada, justificada e limitada ao contrato necessário.
3. Nenhum outro setor recebeu refatoração, formatação ou mudança de comportamento incidental.
4. Contratos e formatos existentes foram preservados ou tiveram mudança deliberada validada.
5. Os testes necessários passaram; resultados pendentes ou limitações foram informados.
6. Mudanças do usuário e dados reais foram preservados.

Na resposta final, descreva o resultado, o setor, os arquivos principais e a validação. Se houver problema independente fora do escopo, relate-o como achado separado, sem dizer que foi corrigido.

## 9. Delegação a outros agentes

Quando houver subagentes autorizados, forneça a cada um um setor e uma lista de arquivos de escrita. Todos devem ler este arquivo. Proíba edição concorrente do mesmo arquivo e mantenha alterações de contratos compartilhados sob coordenação. O coordenador revisa os diffs por setor antes de integrar; dividir trabalho não amplia a autorização do usuário.
