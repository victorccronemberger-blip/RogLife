# Modularização local concluída

A versão ativa **1.0.63 nesta cópia local** foi separada por responsabilidades, preservando contratos HTTP, formatos persistidos e pontos de entrada existentes. Cinco subagentes trabalharam em rodadas, com revisão e integração pelo coordenador. Não houve publicação no Git.

## O que mudou

- Campanhas: catálogo/seleção, preparo, execução, workers, entrega por conta, reconciliação e ciclo de vida da mídia em `moneymin/campaigns/`.
- Entregas: protocolo, mídia, diário, chunks, sessões, recibos, qualidade, armazenamento e recuperação em `moneymin/delivery/`.
- Contas, cadastro e carteira: serviços e rotas próprios por responsabilidade nos respectivos `*_domain/`.
- Integrações: registry central com IDs e operações de Ego4D, NymeriaPlus e HoloAssist; clientes externos continuam com APIs compatíveis.
- HTTP: raiz de composição, dependências declaradas e registro de 92 operações; os módulos de feature não importam `server.py`.
- Interface: criação e comportamento de cada página no setor correspondente. Contratos nomeados coordenam seleção, remoção de contas, navegação, backend e timers.

O guia de propriedade dos arquivos está em [`../../MODULOS.md`](../../MODULOS.md). O roadmap está em [`ROADMAP.md`](ROADMAP.md).

## Evidências finais

| Verificação | Resultado | Registro |
| --- | --- | --- |
| Suíte Python integrada offline | **3.097 passaram; 3.422 subtestes passaram; 7 pulados; zero falhas** | `python-final.log` |
| Testes nativos/Qt | **28/28 passaram** | `qt-final.log` |
| Contratos arquiteturais | **18 testes e 9 subtestes passaram**, incluídos na suíte integrada | `architecture-final-focused.log`, `python-final.log` |
| Inicialização real do serviço isolado | Startup, autenticação, ferramentas e shutdown passaram | `service-final.log` |
| FFmpeg/FFprobe reais | **Cinco casos existentes passaram** em um harness separado | `media-final.log`, `scripts/verify_real_media.py` |
| Fonte original preservada | **390 arquivos de código conferidos; nenhum alterado** | `original-preservation.json` |
| Instalação local | Build e `dist-local/QMoney.exe` têm o mesmo SHA-256; nenhuma atualização pendente | `validation-final.json`, `deploy-final.log` |

Cinco dos sete testes pulados pela suíte padrão exigiam o layout de runtime empacotado para FFmpeg. Foram executados com as ferramentas reais desta cópia pelo harness. Os dois skips restantes dependem da plataforma. A validação usou dados temporários e fixtures, sem executar cadastro, campanha, upload, transferência ou saque real nos provedores.

Durante a revisão foram corrigidos o callback do índice Ego4D, que confundia uma variável local com o endpoint `index`, e a coleta de operações do teste de autenticação, que ficou vazia após mover as rotas. O teste passou a usar o inventário original independente. Também foram corrigidos imports relativos, um decorator duplicado e a callback do runner que era reatribuída em cada factory. A rodada integrada anterior com falhas foi preservada em `python-before-final-fixes.log`; a rodada final passou.

## Estrutura e limites explícitos

O inventário passou de 88 para 169 módulos Python. `server.py` passou de 6.751 para 578 linhas, e `MainWindow.cpp` de 7.080 para 247. São indicadores estruturais, acompanhados por revisão e testes; arquivos menores sozinhos não demonstram isolamento.

Os imports de inicialização não têm ciclos. Permanecem três componentes com imports tardios, presentes no baseline e documentados em `analise-qa.md`. Runners, locks e restauração de checkpoints têm ownership por processo; caches, planos e barreiras são separados por instância do serviço. A classe `MainWindow` conserva estado privado e infraestrutura comum, enquanto os setores usam métodos explícitos para coordenação. Não são aplicativos ou controllers completamente independentes.

Os dados de produção continuam no perfil QMoney do usuário Windows; execute uma instância ativa por perfil. O executável resolve o backend desta cópia, e a compilação usa `build-modular2`, sem reaproveitar o cache antigo que apontava para a origem. O SDK de compilação pode ficar fora do projeto, com caminho configurável.

Para trabalhar, abra `Finally/MODULOS.md`, edite o módulo dono e preserve seus contratos. Para executar a versão atualizada, use `Finally/ABRIR_QFAR_LOCAL.cmd`.
