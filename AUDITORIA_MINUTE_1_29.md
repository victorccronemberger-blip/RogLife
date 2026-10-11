# Auditoria local — Ego4D, NymeriaPlus e Minute 1.29

## Incorporação das versões 2.0.60 e 2.0.62

A base de referência anterior era a 2.0.57 (`a64bddb`). Foram revisados os cinco commits até a tag 2.0.62, cujo commit é `2590c52073c7eb65c6d9069290adfb149a4a42f5`, e os 32 arquivos alterados entre essas tags. As notas públicas estão em [2.0.60](https://github.com/victorccronemberger-blip/QFar/releases/tag/v2.0.60) e [2.0.62](https://github.com/victorccronemberger-blip/QFar/releases/tag/v2.0.62). As versões 2.0.58 e 2.0.59 foram canceladas e suas correções reunidas na 2.0.60; a 2.0.61 não foi publicada e a 2.0.62 corrige seu teste de arredondamento do limite de tempo.

Foram integrados 22 arquivos de núcleo e testes por comparação de três versões. O conflito em `campaign.py` foi resolvido preservando a verificação local de `requiresAudio`; a recuperação de CSVs Nymeria sob algoritmo v5 e o contrato fixo Minute 1.29 também foram preservados. Os arquivos de versão/publicação e a interface 2.x não substituíram a edição local. O código de progresso e recuperação foi adaptado ao `ApiClient` desta interface: mesma identidade e corpo de prévia em todas as retomadas, até quatro recuperações, cinco minutos de inatividade e trinta minutos totais. O tempo decorrido sozinho não renova a janela; mudanças reais de fase/contador a renovam. Downloads de integrações e consultas de recuperação mantiveram seus limites específicos anteriores.

A prévia agora usa `catalog_only=True`: não baixa mídia, abre VRS nem varre IMUs completos para estimar capacidade. A preparação e a entrega continuam exigindo as validações completas. A classificação de evidências usa buscas agrupadas equivalentes e evita classificar componentes Nymeria que não podem atingir a duração pedida. A interface mostra os contadores reais. Banimentos confirmados são reconciliados no arquivo protegido existente e retirados dos acessos disponíveis; falhas temporárias, diagnósticos inconclusivos e retenções de saque não autorizam essa exclusão. A remoção mantém registros de campanha, journals e referências de recuperação que protegem mídias pendentes.

Os 58 testes dirigidos passaram com 192 subtestes. Os **16 testes nativos passaram em 94,11 s**, incluindo andamento após 250 consultas, limite de inatividade sem confundir `elapsed_s` com progresso, retomada da mesma prévia depois de HTTP 504 e conexão interrompida, limite de quatro recuperações e os fluxos Nymeria da interface local. Logs: `../mapeamento/port-2.0.62-testes-dirigidos.log` e `../mapeamento/port-2.0.62-suite-qt.log`. Backup antes da incorporação: `../backups/pre-port-2.0.62.zip`; executável anterior: `../backups/QMoney-pre-2.0.62-local.exe`. O executável distribuído e o compilado têm SHA-256 `B7298F0F303BEB4F408108C0B13C4FF81A789C7654F4EE476C3CE8589F7170BE`.

A aplicação foi encerrada sem campanha em andamento e reaberta. A interface 1.0.63, uma conta conectada, o modo sob demanda e o rascunho foram preservados; os contadores de classificação foram observados na tela real. Não houve publicação, push, PR ou início de envio produtivo.

A suíte completa após a incorporação executou 2.609 casos: 2.601 passaram, sete foram pulados e um teste local ainda esperava a antiga janela de 15 minutos sem progresso. O teste foi atualizado para verificar a consulta aos 299 segundos e seu limite aos 301 segundos, conforme a regra atual de cinco minutos sem avanço. A revalidação dos 14 casos de consulta/progresso/prévia passou, incluindo esse caso. Assim, os **2.602 casos executáveis ficaram validados**, com 3.321 subtestes aprovados na execução completa; o log completo conserva a falha inicial para rastreabilidade e o log complementar registra sua resolução. Não foi alterado o código de produção para ocultar essa divergência. Logs: `../mapeamento/port-2.0.62-suite-python.log` e `../mapeamento/port-2.0.62-limites-revalidacao.log`.

A recontagem do catálogo real após a integração manteve **20 candidatos em sete categorias para 1–30 minutos** e **um candidato para 3–30 minutos**. Foram preservadas as 153 gravações e 31.977 anotações recuperadas pela correção local. A execução levou 586,27 s, contra 638,25 s na conferência anterior; são tempos informativos com outras validações em paralelo, não um benchmark isolado. Arquivos: `../mapeamento/nymeria-selecao-2.0.62-local.json` e `../mapeamento/nymeria-selecao-2.0.62-local.log`. Nenhuma mídia foi baixada nem enviada por essa recontagem, que mantém a rede externa bloqueada.

A consulta na aplicação reaberta também concluiu: a interface confirmou sete categorias compatíveis entre 49 e mostrou os dois novos planos de montagem de móveis. Durante a classificação, os contadores reais foram acompanhados em várias posições até o término. O mínimo de um minuto, o modo sem pré-cache, a conta e o rascunho permaneceram preservados, e a tela continuou indicando nenhuma campanha em andamento.

## Escopo e evidências

Auditoria da edição local com interface QFar 1.0.63, feita contra o APK do Minute mapeado localmente. A versão do contrato é Minute **1.29.0 / versionCode 1004038**. O número QFar 1.0.63 identifica a interface local; não é enviado como versão do Minute. Não houve push ou publicação.

O relatório JSON em `../mapeamento/auditoria-minute-129-apk-catalogo.json` registra o SHA-256 do bundle, caminhos e linhas dos literais conferidos. Fontes primárias: `bundle/decompiled.js`, `apktool/base/smali/l2.1`, e os mapas `reports/MAPA-TAREFAS-ENVIO.md` e `reports/MAPA-COMPLETO-GRAVACAO.md`.

## Revisão como engenheiro

| Etapa | Contrato conferido | Implementação e proteção |
|---|---|---|
| Tarefas | `/orgs/{org}/tasks`, categorias e `requiresAudio` | Catálogo da conta atual; regras de ação e duração; seleção vinculada à prévia |
| Aquisição | Fontes de cada dataset | URLs/identidade/hash do Nymeria; origem e janelas Ego4D; download durante a campanha |
| Vídeo | MP4 e duração efetiva | FFmpeg/FFprobe; PTS medidos; preservação de VFR Nymeria; recorte revalidado |
| Sensores | `t,ax,ay,az,wx,wy,wz` | Amostras das fontes; grade 500 Hz; lacunas/NaN/cobertura inválida bloqueiam a entrega |
| Quadros | `i,ptsNs,dtNs,tNs,key` | Timestamps extraídos do vídeo codificado; os provedores não podem relaxar a exigência de quadros medidos |
| Sidecar | `{logId}.data.zip` e membros na raiz | CSV e metadata; identidade de sessão/chunk; coerência de relógios; hashes de conteúdo |
| Criação | `POST /uploads`, UploadOut e conflito 409 | Antes do SAS/PUT; recibo durável; resultado inconclusivo não permite repetir criação indiscriminadamente |
| Armazenamento | `signed_urls` com filename/blob_url/expires_at | Transporte de vídeo e ZIP; identidade dos arquivos; retomada e renovação do SAS |
| Conclusão | PATCH complete, último chunk e finalize | `suppress_per_chunk_catbear`, `session_complete`, `network_type`; commits e finalização antes de contabilizar sucesso |
| Recuperação/limpeza | Recibos e arquivos ainda necessários | Sem reenviar conteúdo confirmado; arquivos pendentes/inconclusivos protegidos; mídia gerenciada liberada após confirmação |

Correções desta auditoria:

- Removida a seleção de versões antigas por `MINUTE_APP_VERSION` e `MINUTE_ANDROID_VERSION_CODE`. Contrato 1.29/1004038 fixo. Overrides históricos de plataforma iOS, nome do sistema e User-Agent não alteram mais o protocolo Android desta edição.
- Retirados exemplos de configuração 1.22/iOS do `.env.example`. Migrações de dados e testes de rejeição de formatos antigos permanecem para proteger contas, originais e recibos.
- Importar o JSON Nymeria agora sincroniza metadados/anotações na mesma operação cooperativa. Não faz pré-download de VRS/vídeos.
- Resultados parciais informam falhas de download e anotações inválidas; não mostram apenas uma conclusão genérica.
- Headers de localização alinhados aos callsites de createUpload e SAS do APK 1.29, com método/caminho exatos. Sem amostra configurada e válida, o header permanece ausente.
- Consultas de categorias e polling alinhados ao limite de 15 minutos: o processamento frio do catálogo real ultrapassou o limite anterior de 5 minutos.
- Bloqueada nova partida durante verificação, campanha ativa ou encerramento; retorno de categorias não pode reativar o botão durante uma verificação.
- `requiresAudio` é validado no catálogo e propagado pela prévia, pelas contas de destino e pelo motor. Nymeria RGB sem áudio não fica elegível para tarefas que o exigem. Ego4D é sondado novamente antes do envio; perda de áudio durante a codificação bloqueia a entrega antes da criação remota.
- A preparação Ego4D inclui agora as anotações temporais oficiais. O JSON grande é processado por gravação, com memória limitada, hashes de origem e publicação atômica. Não usa resumos sem timestamps nem substitui um índice válido por um download truncado. A preparação completa roda em um único trabalho de segundo plano, com polling e encerramento que aguardam sua liberação.
- A parada Nymeria chega à aquisição, às fases de preparo, à extração de cada frame e ao encoder pertencente ao trabalho. Inclui o preparo do próximo clipe em segundo plano. O cancelamento preserva fontes e saída anterior, aguarda o processo filho e remove os temporários da extração.

## Revisão como QA

Os logs ficam em `../mapeamento/auditoria-minute-129-*.log`. A suíte cobre orquestração API/runner/motor, pausa, recibos, recuperação, finalização, retenção de pendências e limpeza; testes de mídia executam FFmpeg real e fixtures de sensores/SDK. O transporte remoto é simulado ou isolado para não criar uploads produtivos.

A inicialização real isolada passou: autenticação local, Chromium, curl, FFmpeg/FFprobe e encerramento seguro, com rede externa bloqueada.

Os testes Qt incluem o fluxo Nymeria, planos inválidos, falta de espaço, invalidação de planos, paginação e parada. O teste de campanha sob demanda também verifica persistência das escolhas e bloqueio da confirmação quando o conteúdo não alcança a meta.

Resultados registrados:

- Suíte Python completa com parada durante extração/encode: **2.573 aprovados, 7 ignorados e 3.315 subtestes aprovados**, em 351,32 s, em `auditoria-minute-129-suite-final.log`.
- C++/Qt: **12 de 12 aprovados**, em 87,11 s, incluindo polling da preparação completa Ego4D e interface local Nymeria.
- Após o último ajuste da propagação de parada para o preparo em segundo plano, os testes de parada, timestamps, motor, publicação e encerramento passaram: **87 testes e 104 subtestes**, em 33,86 s, em `auditoria-minute-129-parada-prefetch-testes.log`.
- Hashes do executável compilado e distribuído coincidem. As imagens finais da interface foram conferidas; elas usam fixtures locais de QA, não resultados da conta operacional.

Fontes reais e entrega:

| Dataset | Preparo real aprovado | Conferência |
|---|---|---|
| Ego4D | Tarefa atual `Replace Bulbs or Batteries`; 61.066 ms; 30.534 amostras IMU; H.264 1440×1080 com AAC | Fontes reais, janela/anotações, continuidade de sensores e proveniência; MP4 e ZIP pelo motor completo com respostas remotas simuladas |
| Ego4D adicional | `Pet Grooming & Bathing`; 389.133 ms; 194.567 amostras IMU | Vídeo e sensores aprovados; conferência visual. Categoria não presente no snapshot atual de 49 tarefas |
| NymeriaPlus | `Change Sheets & Make Bed`; 73.988 ms; 36.995 amostras IMU; H.264 1440×1080 sem áudio | Aquisição real da sequência, hashes, SDK Aria, relógios RGB/IMU, PTS e conteúdo visual; MP4 e ZIP pelo motor completo com respostas remotas simuladas |

Os testes de entrega com mídia real de **ambos os datasets** usam o motor de produção até create, SAS, transferências de MP4/ZIP, complete, avaliação simulada e finalize. Os recibos terminam em `done`, com finalização confirmada pelas fixtures. Os ZIPs contêm exatamente IMU, frames e metadata na raiz, com os headers do APK. A rede externa foi bloqueada; isso verifica o motor e o contrato local, não aprovação pelo servidor.

O preparo Nymeria avulso não tinha ID de conta/tarefa. A primeira tentativa de entrega foi corretamente bloqueada antes de qualquer operação remota por divergência entre esse vínculo ausente e o catálogo da fixture. A execução aprovada usa o vínculo da mesma ação no catálogo offline, incluindo o plano de aquisição, e revalida a prova completa pelo código de produção. Os bytes, janelas, sensores e ação observada não foram substituídos. As evidências estão em `auditoria-minute-129-ego4d-entrega-offline.json` e `auditoria-minute-129-nymeria-entrega-offline.json`; a amostra preparada completa Nymeria está em `auditoria-minute-129-nymeria-item-real.json`.

## Revisão como usuário final

1. Para Nymeria, em **Acelerador → NymeriaPlus**, use **Importar e sincronizar JSON…** e espere terminar. Para Ego4D, em **Integrações → Ego4D**, configure o acesso autorizado e use **Preparar catálogo**. Ambos adquirem catálogo/anotações sem preparar vídeos antecipadamente.
2. Em **Nova campanha**, escolha **Somente NymeriaPlus**, **Somente Ego4D** ou **Ego4D + NymeriaPlus**. Use **Sob demanda (sem pré-cache)**.
3. Confira duração mínima/máxima e meta por conta. Uma categoria indisponível não autoriza substituir a tarefa por conteúdo sem evidência.
4. Confira a revisão de candidatos e capacidade. Planos vindos apenas de anotações ainda precisam de aquisição e medição; as horas estimadas não são horas confirmadas.
5. Autorize a partida. O motor prepara, valida e envia; preserva pendências e libera mídia gerenciada confirmada se a limpeza estiver habilitada.

O cache Ego4D antecipado inicia desativado para novas sessões. Preferências explicitamente salvas continuam sendo respeitadas. O launcher evita outra instância e pode aplicar um executável preparado na próxima abertura.

Ao concluir esta atualização, o aplicativo distribuído foi reaberto, a janela `QMoney — Operations Desk` foi confirmada e seu serviço estava escutando apenas em `127.0.0.1:8876`. A chamada sem credencial local recebeu 401, como esperado; a autenticação e o encerramento já tinham sido validados no teste de inicialização isolado. O resultado consolidado está em `../mapeamento/auditoria-minute-129-resultado-final.json`.

## Situação real desta instalação e limites

O manifesto contém 1.100 sequências. A sincronização anterior registrou 692 catalogadas, 233 sem anotações e 175 com anotações inválidas. A conferência atual com o parser corrigido encontrou **844 catalogadas, uma com mídia baixada e 255 sem anotações**. Não restaram arquivos mistos inteiramente rejeitados. Fontes sem evidência válida não são consideradas aptas só porque existem no manifesto.

A auditoria fria de 5–30 minutos encontrou zero candidatos nas 49 tarefas do snapshot local. Na faixa de **1–30 minutos**, foram encontrados **17 planos em cinco categorias**: Change Sheets & Make Bed (10), Holiday Decoration Setup (1), Leaf Raking or Blowing (1), Sweep the Porch (3) e Toy or Clothing Pickup (2). São planos por anotações, ainda sujeitos à validação da mídia e dos sensores adquiridos.

O rascunho salvo pelo usuário tinha mínimo de **3 minutos** e meta de **8 horas**. A conferência posterior no aplicativo aberto mostrou **um** trecho de troca de lençóis nessa faixa; a conclusão anterior de que 3 minutos eliminavam todos os trechos foi incorreta. Ao ajustar a duração mínima para **1 minuto** pela interface, foram confirmados os **17 candidatos em cinco categorias**: 10 para cama/lençóis, 1 para decorações, 1 para folhas, 3 para varanda e 2 para recolher brinquedos/roupas. Somente o mínimo foi alterado; a meta de 8 horas e a seleção de categorias foram preservadas. Nenhuma campanha foi iniciada nessa conferência. Os aproximadamente 29,8 minutos somados de anotações são um limite superior anterior à medição e à deduplicação, insuficiente para certificar a meta de 8 horas. Importar 1.100 sequências não significa obter conteúdo apto para todas as tarefas.

A leitura direta dos CSVs encontrou 845 arquivos de ações atômicas, 173.616 linhas, 172.349 linhas com intervalos válidos e 1.267 inválidas. Havia 153 arquivos que misturavam intervalos válidos e inválidos; o parser anterior rejeitava esses arquivos inteiros, incluindo 33.293 linhas válidas neles. A correção `nymeria-atomic-device-v5` delimita os intervalos problemáticos pelos seus extremos finitos, exclui suas anotações e todas as linhas que os tocam, e preserva o restante. Não inverte os tempos para inventar uma ação válida. Tempos negativos, desconhecidos ou não finitos continuam bloqueando a gravação, pois não permitem delimitar a região incerta.

A leitura com o parser corrigido recuperou as **153 gravações e 31.977 linhas utilizáveis** nelas. O total passou a 845 gravações utilizáveis e 170.897 linhas únicas; 1.267 linhas inválidas e 1.220 linhas que tocavam suas regiões foram excluídas. Linhas repetidas são deduplicadas pelo parser. Uma lacuna de anotação continua separando os cortes: não se junta conteúdo através da região em quarentena. Planos e provas de seleção anteriores exigem nova validação; recibos históricos continuam preservados. A classificação ainda exige a ação da tarefa disponível, duração contínua e ausência de atividade concorrente. Por exemplo, dobrar roupas aparece em gravações reais, mas não entre as 49 tarefas da conta nesta conferência. Os diagnósticos estão em `../mapeamento/nymeria-diagnostico-anotacoes.json` e `../mapeamento/nymeria-selecao-corrigida.json`; não baixam mídia nem certificam cobertura dos sensores.

A suíte Python após essa correção passou com **2.580 testes, sete pulados e 3.321 subtestes**, em 516,17 s, com rede externa bloqueada e dados do usuário isolados. Os testes dirigidos do fluxo de campanha/preparo passaram com 80 testes e 183 subtestes; a verificação complementar passou com 31 testes e 20 subtestes, incluindo tempos invertidos, intervalos sobrepostos, ausência de legenda, tempos não delimitáveis, cortes que não atravessam a quarentena, resolução do plano pela mídia/sensores, rejeição das provas de seleção anteriores e preservação dos recibos históricos. Os logs estão em `../mapeamento/nymeria-correcao-suite-completa.log`, `nymeria-quarentena-testes.log` e `nymeria-quarentena-complementar.log`. Os resultados anteriores de mídia real foram obtidos sob a revisão v4; não são apresentados como uma nova entrega validada sob v5.

A recontagem completa de produção sob v5, sem rede externa nem aquisição de mídia, terminou em **638,25 s**. Na faixa de **3–30 minutos** continuam existindo um candidato e uma categoria. Na faixa de **1–30 minutos**, o total passa a **20 candidatos em sete categorias**, com cerca de **34,15 minutos de anotações somadas como limite superior**, ainda sem certificar sensores, deduplicação ou aprovação remota. Os novos candidatos vieram de gravações recuperadas e tiveram seus planos revalidados pelo código de produção. Não se afrouxaram as regras de atividade ou continuidade para aumentar a contagem.

| Categoria Minute atual | Planos de 1–30 minutos | Novos pela recuperação |
|---|---:|---:|
| Furniture Assembly | 2 | 2 |
| Change Sheets & Make Bed | 10 | 0 |
| Holiday Decoration Setup | 1 | 0 |
| Leaf Raking or Blowing | 1 | 0 |
| Planting or Pulling Weeds | 1 | 1 |
| Sweep the Porch | 3 | 0 |
| Toy or Clothing Pickup | 2 | 0 |

A aplicação foi encerrada ociosa e reaberta com a correção. Na interface real, foram conferidas as sete categorias e os 20 candidatos acima, com mínimo de um minuto e modo **Sob demanda (sem pré-cache)**. A meta de oito horas e as seleções anteriores foram preservadas. Nenhuma campanha foi iniciada e nenhuma mídia foi baixada por esta recontagem. Evidência da conferência: `../mapeamento/nymeria-conferencia-interface-v5.json`. A lista exibida ainda representa planos por anotações; só o preparo posterior mede e valida a cobertura de vídeo e IMU.

As credenciais AWS são mantidas localmente pelo armazenamento de integrações protegido por DPAPI; este relatório não afirma que tenham sido provisionadas nesta máquina nem inclui seus valores. Foram adquiridos e verificados `ego4d.json`, `clips.csv` e o índice temporal derivado das narrações oficiais: **2.096 vídeos e 1.011.374 eventos**, com 695 eventos inválidos descartados. O recibo local registra o SHA-256 da fonte oficial de 1.104.784.208 bytes; esse JSON temporário foi removido após a conversão.

O catálogo Ego4D atual produziu **422 candidatos por evidência em 22 categorias** do snapshot de 49 tarefas. Isso é elegibilidade de catálogo, não medição completa de todos os sensores nem garantia de horas aprovadas. Uma amostra com lacuna real de giroscópio de 2,576 s foi corretamente bloqueada antes do preparo de vídeo.

Sem pré-cache, os metadados e anotações continuam necessários. VRS/vídeo e IMU são baixados quando a campanha precisa deles, preparados temporariamente e liberados conforme a política de limpeza após confirmação. A amostra Nymeria exigiu cerca de 12,7 GB de fontes, além de espaço temporário para frames e codificação; download sob demanda não elimina esse uso transitório de disco.

As fontes e mídias das amostras desta auditoria foram preservadas para revisão; os diretórios temporários de frames foram removidos automaticamente. Como não houve confirmação por um servidor real, a auditoria não acionou a limpeza de entrega produtiva nem alterou recibos operacionais para simular aprovação.

Não é possível certificar aprovação remota ou qualidade perfeita a partir do APK e de testes locais. O servidor pode aplicar políticas não presentes no APK. Perfis, calibrações e relógios derivados do desktop não comprovam captura nativa Android; a origem dos datasets é preservada e validada localmente. Não foi iniciado upload produtivo por esta auditoria. A aceitação real precisa ser confirmada pelos recibos e pela avaliação do servidor.
