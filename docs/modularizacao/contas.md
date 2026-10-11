# Refatoração de contas, registro e carteira

A cópia `Finally` separa agora os serviços por responsabilidade. As fachadas `moneymin/web/services/accounts.py`, `registration.py` e `wallet.py` continuam exportando os mesmos nomes e o mesmo `REQUIRES` usado pela composição e pelos patches em `server.*`.

## Serviços

- `services/accounts_domain/`: persistência e catálogo, saúde/identidade, organização e restrições confirmadas.
- `services/registration_domain/`: checkpoints do lote, admissão e validações, execução e retomada das etapas.
- `services/wallet_domain/`: métodos de payout, saldo/atualização e credenciais.

Cada unidade declara seu próprio `REQUIRES` calculado a partir dos acessos a `_services`. As funções continuam recebendo bindings explícitos; as dependências compartilhadas são acessadas por esse objeto. Não foi introduzido estado global novo nem injeção por cópia de globals.

## Rotas

As fachadas de rota mantêm nomes, assinaturas e `REQUIRES`. Implementações foram agrupadas em `routes/accounts_domain/` (catálogo, organização, banimento, transferência e saúde), `routes/registration_domain/` (cadastro, proxies, domínios e lote) e `routes/wallet_domain/` (resumo, payout, atualização e saques). `routes/mail.py` ficou como unidade pequena e coesa.

## Compatibilidade e segurança

As fachadas permanecem como pontos de composição para que `DependencyBindings.bind` use o escopo já declarado e a substituição de dependências expostas por `server.*` continue alcançando handlers e serviços. Os corpos foram movidos sem alterar regras de identificação, fingerprint, antifraude, proxy, restrições, persistência ou fluxo de saque.

A suíte integrada offline passou com 3.097 testes e 3.422 subtestes; contratos HTTP e persistência estão incluídos. Nenhuma conta, campanha, transferência, upload ou saque real foi iniciado. A evidência está em `RELATORIO_FINAL.md` e `validation-final.json`.
