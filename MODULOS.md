# Onde trabalhar no Finally

**Para LLMs e assistentes:** leia primeiro [AGENTS.md](AGENTS.md). Ele delimita os setores autorizados e as regras para mudanças compartilhadas; um pedido sobre contas não autoriza alterações em integrações.

O código desta cópia está organizado por setor. A aplicação original permanece em `QFar/Source-QFar-1.0.63`; não é usada como fonte Python pelo novo executável compilado aqui.

| Trabalho | Implementação | Contrato / entrada |
| --- | --- | --- |
| Campanhas: catálogo, preparo, execução, entrega por conta e ciclo de mídia | `moneymin/campaigns/` | `moneymin/campaign.py` conserva a API de compatibilidade |
| Entrega: protocolo, transferência, recibos, validação e retomada | `moneymin/delivery/` | `moneymin/upload.py`, `upload_types.py`, `upload_storage.py`, `upload_protocol_lease.py` |
| Contas: persistência, saúde, organização e restrições | `moneymin/web/services/accounts_domain/` | `moneymin/web/routes/accounts_domain/` |
| Cadastro: checkpoint, admissão e retomada | `moneymin/web/services/registration_domain/` | `moneymin/web/routes/registration_domain/` |
| Carteira: saldo, credenciais, métodos e saques | `moneymin/web/services/wallet_domain/` | `moneymin/web/routes/wallet_domain/` |
| Configuração de integrações | `moneymin/web/services/integrations.py` | `moneymin/web/routes/integrations.py` |
| Contratos e seleção dos provedores | `moneymin/integrations/` | registry com IDs estáveis e descritores de operações |
| Preparação explícita dos catálogos de mídia | `moneymin/catalog_policy.py`, `moneymin/web/routes/provider_catalogs.py` | `desktop/src/MainWindowCatalogs.cpp`; classificação nos provedores, índices vinculados às fontes e leitura preparada na campanha |
| Clientes externos | `moneymin/crowtado.py`, `claru.py`, `hostinger_mail.py`, `minute_api.py` | Mantêm suas APIs e regras existentes |
| Ego4D | `moneymin/ego4d.py`, `ego4d_annotations.py`, `ego4d_library.py` | Catálogo, anotações e biblioteca separados |
| NymeriaPlus | `moneymin/nymeria.py`, `nymeria_library.py`, `nymeria_vrs.py` | Catálogo, inventário e leitura VRS separados |
| Bibliotecas, aceleração, histórico, armazenamento e recuperação | módulos correspondentes em `moneymin/web/routes/` e `services/` | rotas declaradas em `moneymin/web/routes/registry.py` |
| Interface Qt por setor | `desktop/src/MainWindowAccounts.cpp`, `MainWindowCampaigns.cpp`, `MainWindowIntegrations.cpp`, `MainWindowBalances.cpp`, `MainWindowLibrary.cpp` | `MainWindow.hpp` conserva o contrato e a coordenação da janela |
| Dashboard, prontidão e monitoramento Qt | `MainWindowDashboard.cpp`, `MainWindowMonitoring.cpp` | criação e comportamento pertencem ao mesmo setor |
| Shell, componentes e serviço Qt | `MainWindow.cpp`, `MainWindowComponents.cpp`, `MainWindowService.cpp` | fontes listadas uma vez no CMake |

## Regras para uma alteração

1. Edite o módulo responsável, mantendo a fachada como adaptador de compatibilidade.
2. Rotas não importam `server.py`. A composição injeta as dependências declaradas em `REQUIRES`.
3. Para adicionar uma rota, declare seu handler/decoradores no registry. Para adicionar uma dependência de negócio, atualize o contrato do módulo e os testes correspondentes.
4. Preserve formatos persistidos, recibos e tratamento de operações incertas. Mudança de contrato precisa ser deliberada e testada.
5. Execute primeiro os testes do setor. Ao integrar, execute toda a suíte e a auditoria arquitetural.

Os adaptadores consultam os pontos de substituição antigos no momento da chamada. Isso mantém os testes e consumidores existentes enquanto a implementação fica em seu módulo próprio. A coordenação compartilhada de operações permanece explícita; modularização não elimina dependências necessárias entre campanha, contas e entrega.

Na interface, cada setor constrói e atualiza sua própria página. Mudanças que afetam outro setor passam por métodos de coordenação: remoção de contas, seleção de catálogo, disponibilidade do backend e encerramento. Os campos e o cliente HTTP compartilhados permanecem na fachada `MainWindow`; os contratos estão descritos em `desktop/src/MainWindowModules.md`.

No backend, os runners e locks de operações têm dono por processo. Caches de catálogo, planos de prévia, barreiras de admissão e workers de biblioteca têm dono por instância do serviço. A inicialização normal restaura os checkpoints existentes; a aplicação continua exigindo uma instância ativa por perfil. Duas factories de teste têm seus caches e barreiras separados e conservam a mesma callback de restrição do runner.

## Testes e compilação

```powershell
# Backend isolado; não usa contas reais nem conexões externas.
.\.venv\Scripts\python.exe scripts\run_tests.py

# Contratos arquiteturais.
.\.venv\Scripts\python.exe scripts\run_tests.py --pytest -q test/test_modular_architecture.py

# Compilação da cópia e seus testes; SDK pode ser configurado explicitamente.
.\scripts\build_local.ps1 -BuildOnly -ToolchainRoot C:\SDK\QMoney
```

O SDK Qt/MinGW/CMake pode ficar fora do projeto: ele fornece ferramentas, não código do aplicativo original. O único build ativo utiliza `Finally/build-modular2`. `dist-local` contém o executável instalado e as bibliotecas necessárias para abrir o aplicativo. Os builds obsoletos `build-local` e `build-modular` e os caches Python/testes foram retirados da cópia; o registro recuperável é local, em `docs/modularizacao/limpeza-local.json`, e fica fora do Git. Caches podem ser regenerados normalmente ao executar o código e os testes.

O executável usa o servidor desta cópia. Os dados do aplicativo continuam no perfil QMoney do usuário Windows, conforme o comportamento existente; use testes isolados para desenvolvimento e execute uma instância do aplicativo por vez.

## Acompanhamento

Tasklist: `docs/modularizacao/ROADMAP.md`. Relatório: `docs/modularizacao/RELATORIO_FINAL.md`. A validação integrada passou com 3.097 testes Python, 3.422 subtestes, 28 testes nativos/Qt e cinco casos de mídia real. O executável aprovado está instalado em `dist-local/QMoney.exe`; abra `ABRIR_QFAR_LOCAL.cmd`. A validação foi local e não representa aprovação remota dos provedores.
