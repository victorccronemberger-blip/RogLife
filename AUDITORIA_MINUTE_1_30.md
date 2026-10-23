# Minute 1.30: atualização física, integração dinâmica e limites da validação

## Evidência de 21/10/2026

O operador autorizou atualizar o Minute no celular USB e investigar a atualização. O pacote `com.bakerdata.minute` foi atualizado pela página oficial da Google Play Store, sem desinstalar nem limpar dados:

| Campo público do pacote instalado | Antes | Depois |
| --- | --- | --- |
| versionName | 1.29.0 | 1.30.0 |
| versionCode | 1004038 | 1004039 |
| installerPackageName | com.android.vending | com.android.vending |

O app atualizado abriu e carregou a lista de tarefas. Nenhuma gravação/campanha/upload real foi iniciado nesta manutenção.

Antes da atualização, as consultas de quota das duas contas da ocorrência retornaram HTTP 403 com `detail.error=app_version_too_old` e `min_version=1.30.0`. Após a atualização e a integração da observação física, ambas retornaram `recordingAuthorization=APPROVED`, `canUpload=true`, `blockedReason=null`. Essa leitura comprova a autorização consultada naquele momento, **não a aceitação de um upload futuro**.

### Proveniência dos APKs

Os APKs base foram copiados do pacote instalado antes e depois da atualização. Os artefatos de análise ficam localmente em `tmp/minute-update/` (ignorados pelo Git); não há APKs, contas ou tokens neste documento.

| Artefato público | SHA-256 |
| --- | --- |
| Base APK 1.29.0 | `c03abc84c8a8038768def11fedcbe3fb634779abf9680fb361d453052d659d57` |
| Base APK 1.30.0 | `3c111c966c53e5d3a67c65b89aba199df20b14905fc629843662a4f6e0294c89` |
| Bundle Hermes 1.29.0 | `f1c1756c5e8ecd76de67628d42850144f046d2296bd33615e74f5263bcba7a37` |
| Bundle Hermes 1.30.0 | `286db518202d0900c04f200e51a909fde164579d7f9a7177c22868e1c8221193` |

## Comparação estática: fatos e limites

O bundle permanece Hermes v96. Houve mudanças no manifest, app.config, bundle, DEX e recursos. Um analisador parcial offline comparou corpos e literais resolvidos de funções selecionadas, não só nomes de strings; sua cobertura não foi integral. Splits nativos e comportamento remoto de upload não foram validados.

Diferenças observadas:

- App Check: modo best-effort no cliente oficial, cache por expiração, renovação compartilhada, warm-up; margem de 30 s e antecipação de 300 s. O timeout de espera de 5 s já existia.
- CREATE e COMPLETE passam a pedir espera de atestado; o oficial também diferencia `app_check` de outros bloqueios e tem janela própria de recuperação de 30 s.
- COMPLETE preserva o motivo de bloqueio em 403 e classifica 408/429 e 403 com motivo `app_check` como recuperáveis.
- Calibração: redução dos delays e orçamento de tentativas persistido. Isso não autoriza inventar calibração nem modificar capturas existentes.
- Deep links Uber mudaram de host para `useminute.app`.

Nos trechos comparados de quota, configuração de gravação, política de câmera, UploadOut, `getDeviceUploadMeta` e adaptação de metadata para sidecar, não foi demonstrada mudança local de contrato. Igualdade local não prova equivalência de todo o grafo de chamadas nem de políticas remotas.

O Finally já espera App Check nas escritas, verifica validade local e conserva os motivos de bloqueio. Já há retry limitado para COMPLETE com 408/429 comuns. Esta manutenção **preserva o gate obrigatório**: não porta o modo best-effort do oficial, não inventa tokens e não libera replay de CREATE incerto. A recuperação automática específica de App Check do oficial não foi portada; uma rejeição persistente permanece visível e preserva a pendência.

## Implementação dinâmica

### Fonte real, não renumeração

`moneymin/mobile/minute_version.py` observa somente metadados públicos do pacote instalado pelo ADB:

- respeita o dispositivo selecionado e recusa seleção ambígua;
- exige dispositivo autorizado e pacote/versão/build válidos;
- cache somente em memória, TTL de 12 s, com isolamento por perfil/configuração/seleção;
- inventário é revalidado também em cache hits;
- preflight, início e execução direta forçam uma nova leitura;
- falha de leitura não autoriza usar uma versão antiga do disco;
- não lê JWT, GPS ou SharedPreferences nem modifica o app.

No modo device, `minute_api` compara o mínimo observado do serviço contra a versão instalada e usa a mesma fonte no registro da trava. `physical_binding` substitui a versão antiga do snapshot de identidade pela versão instalada **do mesmo serial**, sem atribuir identidade de um celular a outro. Headers, `app/opened` e metadados novos seguem esse binding. Capturas originais continuam preservando sua versão histórica.

`config.APP_VERSION=1.29.0` continua sendo a referência do modo sem celular e da auditoria anterior; não foi promovida apenas para passar o bloqueio.

### Revisão explícita, não compatibilidade universal

Os pares revisados localmente são 1.29.0/1004038 e 1.30.0/1004039. Isso registra uma revisão local do protocolo, não certificação nem aceitação integral do provedor.

Se a Play Store instalar outro build/versão, o projeto o detecta, mas bloqueia a admissão/escrita antes de preparar mídia até a revisão da integração. Consultas diagnósticas continuam disponíveis. Um mínimo maior exigido pelo servidor continua sendo respeitado. A trava anterior não é apagada automaticamente depois da atualização; deixa de bloquear quando a versão efetiva revisada atende ao mínimo, preservando a evidência.

Não é possível garantir que versões futuras, novas políticas, quota, rede ou serviços de terceiros nunca produzirão erro. O objetivo é detectar incompatibilidade cedo, explicar a causa e preservar mídia/recibos sem duplicar efeitos remotos.

## Verificação

Testes isolados cobrem observação ADB simulada, TTL, force-refresh, concorrência, ausência/ambiguidade/troca de aparelho, atualização no mesmo processo, versão/build futuro desconhecido, headers/binding, trava persistida e ausência de fallback silencioso. Os testes não usam o celular, credenciais ou rede externa.

As consultas reais autorizadas descritas acima são leitura de quota. A conclusão de uma campanha só poderá ser comprovada por um envio real controlado, com autorização específica e inspeção dos recibos — não pelo total de testes locais.

Na verificação final desta manutenção, o aparelho, o cliente efetivo e o binding concordaram em `1.30.0/1004039`; a trava persistida permaneceu byte a byte igual. Após reinício normal do app oficial, sem limpar preferências, a renovação nativa foi observada e o gate local de App Check informou `token_available=true`, `source=device`, sem falha. Isso verifica disponibilidade local naquele momento, não validade indefinida nem aceite remoto de escrita.

Suíte completa executada pelo runner isolado: **3786 testes e 3894 subtestes aprovados, sem falhas ou skips informados**. Houve ainda revisão estática independente das corridas entre atualização do pacote, observação de versão e despacho; as regressões identificadas foram corrigidas e testadas. Nenhuma execução de upload real foi feita.
