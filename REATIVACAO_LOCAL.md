# QFar 1.0.63 reativado neste computador

Esta edição mantém a interface da 1.0.63 e incorpora o núcleo atualizado até a tag 2.0.62, além das correções locais de anotações Nymeria e do contrato Minute 1.29. As alterações foram feitas somente no workspace local. Não houve push, PR ou publicação.

## Abrir

Execute `ABRIR_QFAR_LOCAL.cmd` nesta pasta. O aplicativo está em `dist-local/QMoney.exe`; o serviço usa o Python de `.venv`, com as bibliotecas já disponíveis neste computador. Mantenha a pasta do projeto no local atual.

Contas, segredos e estado usam `%LOCALAPPDATA%\QMoney`, salvo uma raiz explícita nas variáveis de ambiente. A biblioteca respeita a preferência existente `libraryRoot`. Os testes usam dados temporários; as contas operacionais não foram alteradas durante a validação. Uma instância que já detenha a raiz de estado ou a porta 8876 deve ser encerrada antes de abrir esta edição.

## Atualizações incorporadas

- Validação de estado persistido, identidade de tokens, credenciais protegidas e compatibilidade com arquivos legados.
- Exclusividade do serviço e das sessões, journals duráveis, criação de upload inconclusiva, recibos e recuperação.
- Hash de conteúdo, sensores, timestamps, validação de respostas e contrato de mídia Minute 1.29.
- Catálogos, regras Ego4D/Nymeria, cobertura IMU, capacidade da campanha, planejamento aprovado e responsividade de contas/categorias.
- Saques com recibos, proteção contra repetição, elegibilidade confirmada e métodos Wise/PayPal.
- Proteção de mídia pendente, limpeza confinada, encerramento que aguarda operações e controle dos processos pertencentes ao aplicativo.
- Pausar/retomar e recuperar envios nas telas antigas; revisão completa dos candidatos e da capacidade antes de enviar.
- NymeriaPlus disponível na interface: em Acelerador → NymeriaPlus, importe o manifesto JSON de URLs, sincronize o catálogo, consulte sequências, estime conteúdo e baixe antecipadamente com reserva de disco e parada segura. Em Nova campanha, escolha Somente NymeriaPlus ou Ego4D + NymeriaPlus. O download antecipado é opcional: a campanha também adquire as fontes sob demanda.
- A importação Nymeria sincroniza as anotações automaticamente; a preparação Ego4D inclui catálogo e anotações temporais oficiais em segundo plano. O acesso AWS autorizado está configurado no armazenamento protegido desta instalação.
- Contrato Minute 1.29/1004038 fixo, sem flags para versões anteriores. `requiresAudio` é conferido por conta e novamente na mídia; tarefas que exigem áudio não aceitam o RGB silencioso do Nymeria.
- Melhorias da 2.0.60: classificação por buscas agrupadas, descarte antecipado de componentes Nymeria curtos, contadores reais de sequências/vídeos e prévia baseada no catálogo, sem abrir VRS ou varrer IMUs completos. A medição continua obrigatória no preparo.
- O acompanhamento local foi adaptado à interface antiga: a consulta conserva seu identificador e corpo, mostra progresso real, para após cinco minutos sem avanço ou trinta minutos no total, e recupera uma prévia interrompida em até quatro tentativas. Envios e cadastros produtivos não são repetidos automaticamente.
- Correções da 2.0.62: contas com banimento confirmado ficam fora dos acessos disponíveis e das campanhas; diagnóstico temporário/inconclusivo e retenção de saque não removem contas. Journals, pendências e histórico de entrega continuam protegidos.

A composição antiga foi preservada. Os módulos das bibliotecas e funcionalidades novas estão no núcleo; isso não reproduz todas as telas da interface 2.x. A opção de limpeza após envio segue a política atualizada de liberar mídia gerenciada confirmada, incluindo cache preparado, preservando pendências.

FFmpeg, FFprobe e Chromium foram instalados em `tools`. Nesta cópia modularizada, o único build ativo fica em `build-modular2` e o executável com as bibliotecas Qt necessárias está em `dist-local`. O antigo `build-local` foi retirado na limpeza local.

## Edição somente local

A atualização remota foi desativada na compilação da interface. O aplicativo não baixará uma release 2.x para substituir esta edição. Para reconstruir, use `scripts/build_local.ps1`; o script não publica nada. Os scripts históricos de release permanecem como referência e não são o caminho de build desta edição.

O backup da pasta original está em `../backups/Source-QFar-1.0.63-antes-20261007-011534.zip`. Os fontes originais também podem ser consultados em `../QFar-tag-1.0.63`.

## Conferência

Esta seção registra a validação histórica da reativação. Para a validação atual da cópia modularizada, consulte `docs/modularizacao/RELATORIO_FINAL.md`. A prévia histórica da interface foi preservada em `docs/modularizacao/local-edition-preview-historico.png`. A validação de inicialização confirma autenticação local, FFmpeg/FFprobe/Chromium/curl e fechamento seguro, com acesso externo bloqueado.

- C++/Qt: 12 de 12 testes passaram, incluindo interface antiga, Nymeria e polling da preparação completa Ego4D.
- Python: 2.573 testes e 3.315 subtestes passaram, com 7 testes ignorados, em `auditoria-minute-129-suite-final.log`. Após o último ajuste de parada do preparo em segundo plano, os 87 testes e 104 subtestes pertinentes também passaram em `auditoria-minute-129-parada-prefetch-testes.log`.
- Inicialização real do serviço, autenticação, dependências de mídia e encerramento: aprovados em ambiente temporário com rede externa bloqueada.
- NymeriaPlus: aquisição real de cerca de 12,7 GB e preparo aprovado de um recorte de 74 segundos com IMU real e PTS medidos. MP4/ZIP passaram pelo motor até finalização com transporte remoto simulado e rede externa bloqueada. A parada foi conferida durante a extração e no encoder pertencente ao trabalho, preservando fontes e saída anterior.
- Ego4D: acesso S3 e catálogo/anotações reais conferidos; recorte de tarefa atual de 61 segundos aprovado. MP4/ZIP passaram pelo motor até finalização com transporte remoto simulado e rede externa bloqueada.

Veja `AUDITORIA_MINUTE_1_29.md` para o mapeamento do APK, as revisões de engenharia/QA/usuário, as categorias encontradas e os limites de aprovação remota. Na conferência posterior pela interface, o mínimo salvo de 3 minutos mostrava um trecho; ajustado para 1 minuto, passou a mostrar 17 candidatos em cinco categorias. O mínimo de 1 minuto ficou salvo no rascunho. Os dados disponíveis não comprovam a meta salva de 8 horas.

A correção posterior do parser Nymeria passou a isolar intervalos inválidos, preservando o restante das gravações sem permitir cortes sobre a região incerta. Recuperou 153 gravações e 31.977 anotações utilizáveis. A recontagem de produção passou a **20 candidatos em sete categorias para 1–30 minutos**; para 3–30 minutos continua havendo apenas um candidato. A suíte após a correção passou com 2.580 testes, sete pulados e 3.321 subtestes; os testes complementares de quarentena e recibos também passaram. O modo permanece sob demanda, sem pré-cache, e os números são estimativas por anotações, sujeitos à medição de vídeo/IMU e às exigências da conta.

Não foram feitos upload produtivo, saque real ou publicação. A operação com as contas reais continua dependendo das credenciais, da biblioteca e da disponibilidade dos serviços externos.
