# Modularização do Finally

## Meta e escopo

Refatorar a cópia `Finally` por responsabilidades, mantendo comportamento, contratos HTTP, persistência e interface existentes. A origem `QFar/Source-QFar-1.0.63` permanece preservada. Não publicar no Git. Não alterar identificação, antifraude ou regras de restrição dos provedores.

“Concluído” exige evidências, não apenas arquivos menores: módulos compiláveis/importáveis, dependências declaradas, contratos de compatibilidade, testes de regressão, origem independente e documentação de manutenção. Dependências necessárias entre setores serão explícitas; não será prometida ausência absoluta de dependências.

## Tasklist

- [x] Confirmar cópia ativa, objetivo e limites.
- [x] Distribuir arquitetura/QA, interface e integrações entre agentes.
- [x] Registrar baseline e inventário de contratos/rotas/dependências.
- [x] Separar composição HTTP, middleware e rotas por domínio.
- [x] Separar serviços de campanhas, contas, integrações, saldos e bibliotecas.
- [x] Definir contexto de dependências explícito, conservando os pontos de substituição dos testes.
- [x] Modularizar processamento de campanhas e entrega de mídia.
- [x] Modularizar interface Qt em unidades por setor e infraestrutura compartilhada.
- [x] Centralizar contratos dos provedores e suas seleções.
- [x] Corrigir ambiente e caminhos da cópia para compilação/execução independente.
- [x] Adicionar verificações de arquitetura, fronteiras e equivalência de rotas.
- [x] Executar suítes Python, Qt, inicialização/parada isoladas e ferramentas reais aplicáveis.
- [x] Revisar riscos, documentar arquivos por responsabilidade e publicar relatório local final.

## Responsabilidades

| Setor | Responsável | Propriedade |
| --- | --- | --- |
| Coordenação e HTTP | Coordenador | composição, módulos HTTP/serviços, scripts locais, integração final |
| Interface | Agente desktop | `desktop/`, testes Qt necessários |
| Integrações | Agente integrações | contratos/registry e consumidores de providers |
| QA e arquitetura | Agente QA | inventário, auditoria e testes arquiteturais |
| Campanhas | Agente campanhas | motor, catálogo, preparo, entrega por conta e lifecycle |
| Contas e saldos | Agente contas | serviços de conta/cadastro/carteira e rotas correspondentes |
| Entregas | Agente integrações, segunda rodada | protocolo, diário, recibos e retomada |

Há quatro vagas simultâneas incluindo o coordenador. Cinco subagentes atenderam aos seis setores em rodadas; o agente de integrações também cuidou de entregas, sem edição concorrente do mesmo arquivo.

## Critérios de validação

1. Mesmos métodos, caminhos, endpoints e respostas HTTP existentes.
2. Mesmos formatos persistidos e tratamento de efeitos remotos incertos.
3. Parada cooperativa, exclusão de mídia e proteção de recibos preservadas.
4. Nenhuma conexão externa nem acesso a dados reais nos testes.
5. Módulos de domínio não importam a fachada HTTP; composição injeta dependências.
6. Compatibilidade de imports/monkeypatch documentada e testada enquanto a API antiga existe.
7. Interface nativa compilada de fontes da cópia, com todos os setores e testes listados no CMake.
8. Resultado final distingue simulação, ferramenta local real e aceitação remota não testada.

## Estado

Concluído e instalado em `Finally/dist-local`. O baseline foi executado contra a origem preservada; as alterações aconteceram exclusivamente no Finally. Não foi iniciado cadastro, campanha, upload ou saque real.

Baseline: 3.079 testes Python e 3.413 subtestes passaram, com 7 skips. Resultado final integrado: **3.097 testes Python e 3.422 subtestes passaram**, com 7 skips; **28 testes nativos/Qt passaram**. Startup/shutdown isolados e cinco casos com FFmpeg real passaram. Os cinco skips de mídia foram cobertos pelo harness de ferramentas reais; os dois restantes dependem da plataforma. O executável instalado tem o mesmo hash do build aprovado.

Relatório final: [`RELATORIO_FINAL.md`](RELATORIO_FINAL.md). Evidência estruturada: [`validation-final.json`](validation-final.json). Dependências compartilhadas e exceções coesas estão documentadas; não é prometida ausência absoluta de acoplamento ou falhas futuras.

## Limpeza do frontend — 12/10/2026

Meta: deixar todas as abas, subabas e janelas legíveis e organizadas, mantendo o design, os controles e os fluxos existentes. Mudanças limitadas à apresentação Qt; motor, autenticação, APIs e dados permanecem preservados.

- [x] Confirmar a cópia ativa e registrar os arquivos já modificados.
- [x] Distribuir revisão por setor, sem escrita concorrente nos mesmos arquivos.
- [x] Corrigir a base visual: dropdowns, barras de progresso, cabeçalhos e espaçamento.
- [x] Limpar Visão geral e Prontidão.
- [x] Limpar Integrações e o painel de diagnóstico USB.
- [x] Limpar Nova campanha, prévia, recuperação e confirmações.
- [x] Limpar Acelerador, bibliotecas Ego4D/Nymeria/HoloAssist e Catálogos.
- [x] Limpar Contas e suas janelas, mantendo campos, avisos e confirmações necessários.
- [x] Limpar Saldos, Histórico, Banidas e janelas correspondentes.
- [x] Revisar todos os diffs: apresentação sem alteração de comportamento.
- [x] Compilar e validar os contratos nativos, a arquitetura e a suíte Python.
- [x] Conferir todas as páginas em perfis de QA e dois tamanhos de janela; abrir dropdowns e verificar progressos.
- [x] Aplicar o executável local aprovado, preservando uma cópia anterior e qualquer aplicativo em uso.
- [x] Registrar evidências e limitações da validação.

Critérios: textos instrutivos longos vão para ajuda/tooltip quando não forem necessários à decisão; erros e confirmações relevantes continuam visíveis. Opções de dropdown devem permanecer alcançáveis com rolagem e teclado. Barras com percentual devem ter altura legível; estágios sem total continuam indeterminados. Testes não usam contas, envios ou saques reais.

Resultado: 3.339 testes Python e 3.683 subtestes passaram, com 7 skips; os 35 contratos nativos passaram após restaurar o aviso de aquisição/medição. Auditoria de layout em 10 páginas, subabas, dois temas e dois tamanhos, com 160 capturas incluindo rolagem e prévia; interação Windows validou dropdown e seleção por teclado em QA isolado. Executável instalado com hash igual ao build e backup preservado. Evidência: [`frontend-cleanup-20261012.json`](frontend-cleanup-20261012.json). Tabelas largas continuam usando rolagem horizontal; esta validação não realizou envios nem saques reais.

## Conexão USB por eventos — 12/10/2026

Correção do indicador que apagava o celular ao atualizar. A detecção deve acompanhar notificações de conexão/remoção USB do Windows, sem consultas periódicas ao aparelho. Dados públicos são lidos ao abrir, conectar, mudar a seleção ou atualizar manualmente.

- [x] Localizar a causa e medir a conexão real: 10 leituras do S22 permaneceram conectadas.
- [x] Corrigir a distinção entre falha de leitura e desconexão; preservar a seleção física com emulador presente.
- [x] Substituir o ciclo de seis segundos por notificações USB nativas.
- [x] Validar eventos duplicados, desconexão, reconexão, erros, leitura pendente e respostas atrasadas.
- [x] Compilar, validar contratos/arquitetura e conferir a interface no Windows.
- [x] Aplicar localmente o executável aprovado com backup e registrar evidência.

Resultado: ciclo periódico removido e notificações USB nativas validadas no Windows. 3.340 testes Python e 3.683 subtestes passaram, com 7 skips; 36 testes nativos passaram. Verificação visual de ON durante atualização manual em QA com dados públicos do S22; desconexão/reconexão e eventos foram testados de forma controlada, sem desconectar o cabo físico. Executável instalado, hash igual ao build e backup preservado. Evidência: [`usb-events-20261012.json`](usb-events-20261012.json).

## Revalidação dos módulos — 12/10/2026

- [x] Auditar fronteiras de campanhas/biblioteca, contas/saldos e shell/integrações em paralelo.
- [x] Remover acesso direto da Biblioteca à geração do catálogo da Campanha, usando operação do setor dono.
- [x] Exigir índice preparado na consulta, prévia e capacidade por horas do modo dataset; preservar cache/both e planos/IDs dos recibos aprovados.
- [x] Propagar a necessidade de preparação Nymeria para impedir aprovação silenciosa de plano parcial.
- [x] Usar confirmação da carteira para totais/filtros/ações e identificar histórico na tabela, resumo Claru e CSV.
- [x] Impedir respostas obsoletas e retomada de consultas de saldo em páginas ocultas; testar navegação, geração e encerramento.
- [x] Compilar, executar integração offline, testar ferramentas reais e conferir capturas nos dois temas.
- [x] Instalar localmente com backup, comparar hash e validar inicialização isolada do executável.

Resultado: **3.355 testes Python e 3.723 subtestes passaram; 7 skips e zero falhas. 37 contratos nativos passaram** (36 existentes mais o novo contrato de Saldos). Cinco casos de mídia foram executados com FFmpeg/FFprobe reais; os dois skips restantes exigem criação de symlink indisponível neste runner Windows. Startup, autenticação local, ferramentas e parada cooperativa passaram em perfil temporário com rede externa bloqueada. Os três warnings Python já existiam.

Auditoria estática: 183 módulos Python, 96 operações HTTP, nenhum handler/helper aninhado em create_app e nenhum ciclo de importação na inicialização. Persistem três componentes de imports tardios e o estado privado compartilhado em MainWindow, documentados; os setores não são controllers totalmente independentes. O novo teste de arquitetura protege campos de outros setores e a inclusão dos componentes USB no build compartilhado.

As capturas de Saldos usam o mesmo método de tema da aplicação. O teste CSV valida o arquivo efetivamente exportado; seu seletor Qt não nativo/offscreen usa Fusion para evitar uma falha do fixture com Qlementine. Isso não valida o seletor nativo Windows, nem muda o estilo de produção. Não houve cadastro, campanha, upload ou saque real nesta rodada.

Executável instalado com SHA-256 igual ao build aprovado, sem atualização pendente; backup anterior preservado. Evidência: [`modular-audit-20261012.json`](modular-audit-20261012.json). Inventário estrutural: [`architecture-audit-20261012.json`](architecture-audit-20261012.json).
