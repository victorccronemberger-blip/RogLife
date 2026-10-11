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
