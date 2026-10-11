#include "MainWindowSupport.hpp"
#include "MainWindow.hpp"


void MainWindow::initializeBalanceTimers() {
  _balancePoll.setInterval(1500);
  connect(&_balancePoll, &QTimer::timeout, this, &MainWindow::loadBalances);
}

void MainWindow::onBalancesPageActivated(int index) {
  if (index != 6) _balancePoll.stop();
}

void MainWindow::shutdownBalanceTimers() {
  _balancePoll.stop();
}

void MainWindow::removePermanentlyDeletedAccountFromBalances(const QString& email) {
  if (!_balancesTable) return;
  for (int row = _balancesTable->rowCount() - 1; row >= 0; --row)
    if (_balancesTable->item(row, 0) && _balancesTable->item(row, 0)->text() == email)
      _balancesTable->removeRow(row);
}

void MainWindow::loadBalances() {
  _api.get(QStringLiteral("/api/balances"), [this](bool ok, const QJsonDocument& doc, const QString& error) {
    if (!ok) return showError(QStringLiteral("Falha ao carregar saldos"), error);
    const auto root = doc.object();
    _balancesSnapshot = root;
    _balancesExport->setEnabled(!root.value(QStringLiteral("accounts")).toArray().isEmpty());
    const auto accounts = root.value(QStringLiteral("accounts")).toArray();
    const auto balances = root.value(QStringLiteral("balances")).toObject();
    const auto accountKinds = root.value(QStringLiteral("account_kinds")).toObject();
    const auto withPassword = root.value(QStringLiteral("with_password")).toArray();
    const auto runner = root.value(QStringLiteral("runner")).toObject();
    const auto bulk = root.value(QStringLiteral("withdraw_bulk")).toObject();
    _lastWithdrawBulk = bulk;
    _balancesWithdrawHistory->setEnabled(
        bulk.value(QStringLiteral("state")).toString() != QStringLiteral("idle"));
    const auto exchange = root.value(QStringLiteral("exchange")).toObject();
    QStringList passwordAccounts;
    for (const auto value : withPassword) passwordAccounts << value.toString();
    QStringList orderedAccounts;
    for (const auto value : accounts) orderedAccounts << value.toString();
    std::sort(orderedAccounts.begin(), orderedAccounts.end(), [&balances](const QString& left, const QString& right) {
      const auto leftValue = balances.value(left).toObject().value(QStringLiteral("availableCents"));
      const auto rightValue = balances.value(right).toObject().value(QStringLiteral("availableCents"));
      const bool leftKnown = leftValue.isDouble();
      const bool rightKnown = rightValue.isDouble();
      if (leftKnown != rightKnown) return leftKnown;
      if (leftKnown && leftValue.toDouble() != rightValue.toDouble())
        return leftValue.toDouble() > rightValue.toDouble();
      return left.compare(right, Qt::CaseInsensitive) < 0;
    });
    _balancesTable->setRowCount(orderedAccounts.size());
    qint64 approvedTotal = 0;
    qint64 pendingTotal = 0;
    int staleTotals = 0;
    const auto walletAccounts = doc.object().value(QStringLiteral("wallet")).toObject().value(QStringLiteral("accounts")).toObject();
    int eligibleWithdrawals = 0;
    int row = 0;
    for (const QString& email : orderedAccounts) {
      const bool isClaru = accountKinds.value(email).toString() == QStringLiteral("claru");
      const auto balance = balances.value(email).toObject();
      _balancesTable->setItem(row, 0, cell(email));
      const bool hasAvailable = balance.value(QStringLiteral("availableCents")).isDouble();
      const bool hasPending = balance.value(QStringLiteral("pendingCents")).isDouble();
      const qint64 availableCents = hasAvailable
          ? static_cast<qint64>(balance.value(QStringLiteral("availableCents")).toDouble()) : 0;
      const qint64 pendingCents = hasPending
          ? static_cast<qint64>(balance.value(QStringLiteral("pendingCents")).toDouble()) : 0;
      if (hasAvailable) approvedTotal += availableCents;
      if (hasPending) pendingTotal += pendingCents;
      if ((hasAvailable || hasPending)
          && (!balance.value(QStringLiteral("error")).toString().isEmpty()
              || balance.value(QStringLiteral("stale")).toBool())) ++staleTotals;
      QString available = hasAvailable
          ? usdMoney(availableCents)
          : QStringLiteral("—");
      QString pending = hasPending
          ? usdMoney(pendingCents)
          : QStringLiteral("—");
      if ((!balance.value(QStringLiteral("error")).toString().isEmpty()
                       || balance.value(QStringLiteral("stale")).toBool())) {
        available = hasAvailable ? available + QStringLiteral(" *") : QStringLiteral("não confirmado");
        pending = hasPending ? pending + QStringLiteral(" *") : isClaru ? QStringLiteral("—") : QStringLiteral("não confirmado");
      }
      auto* availableItem = cell(available);
      auto* pendingItem = cell(pending);
      if (!balance.value(QStringLiteral("error")).toString().isEmpty() || balance.value(QStringLiteral("stale")).toBool()) {
        availableItem->setToolTip(QStringLiteral("Consulta inconclusiva. * indica o último saldo salvo.\n") + balance.value("error").toString());
        pendingItem->setToolTip(availableItem->toolTip());
      }
      if (isClaru) {
        pendingItem->setToolTip(QStringLiteral("O resumo Claru não informa saldo pendente. Nenhum valor foi estimado."));
        _balancesTable->item(row, 0)->setToolTip(QStringLiteral("Claru · login: ")
            + root.value("claru_connections").toObject().value(email).toObject().value("login_email").toString());
      }
      availableItem->setTextAlignment(Qt::AlignRight | Qt::AlignVCenter);
      pendingItem->setTextAlignment(Qt::AlignRight | Qt::AlignVCenter);
      _balancesTable->setItem(row, 1, availableItem);
      _balancesTable->setItem(row, 2, pendingItem);
      _balancesTable->setItem(row, 3, cell(friendlyDate(balance.value(QStringLiteral("updated_at")).toString())));
      const bool hasPassword = passwordAccounts.contains(email);
      const bool hasBalanceError = !balance.value(QStringLiteral("error")).toString().isEmpty()
          || balance.value(QStringLiteral("stale")).toBool();
      const auto payout = walletAccounts.value(email).toObject().value(QStringLiteral("payout")).toObject();
      const bool payoutEligible = payout.value(QStringLiteral("eligible")) == QJsonValue(true);
      if (payoutEligible)
        ++eligibleWithdrawals;
      auto* actions = new QWidget;
      auto* actionsLayout = new QHBoxLayout(actions);
      actionsLayout->setContentsMargins(5, 5, 5, 5);
      actionsLayout->setSpacing(7);
      auto* credentials = new QPushButton(
          isClaru ? (hasAvailable ? QStringLiteral("Ganhos Claru") : QStringLiteral("Consultar Claru"))
          : hasPassword ? QStringLiteral("Alterar acesso")
                      : QStringLiteral("Conectar Crowtado"));
      credentials->setMinimumHeight(32);
      credentials->setEnabled(true);
      credentials->setToolTip(isClaru
          ? QStringLiteral("Mostra saldo atual e ganhos por envios, indicações e bônus informados pelo Claru.")
          : QStringLiteral("Informa e valida a senha desta identidade diretamente no Crowtado."));
      connect(credentials, &QPushButton::clicked, this,
              [this, email, isClaru, hasAvailable, balance] {
        if (!isClaru) return configureCrowtadoAccess(email);
        if (!hasAvailable) {
          _api.post(QStringLiteral("/api/balances/refresh"), {{"emails", QJsonArray{email}}},
                    [this](bool ok, const QJsonDocument&, const QString& error) {
            if (!ok) return showError(QStringLiteral("Saldo Claru não consultado"), error);
            _balancePoll.start(); loadBalances();
          });
          return;
        }
        const auto amount = [this, &balance](const QString& field) {
          return balance.value(field).isDouble() ? usdMoney(static_cast<qint64>(balance.value(field).toDouble())) : QStringLiteral("—");
        };
        QMessageBox::information(this, QStringLiteral("Ganhos Claru"),
            QStringLiteral("%1\n\nSaldo atual: %2\nEnvios: %3\nIndicações: %4\nBônus: %5\n\n%6")
                .arg(email, amount("availableCents"), amount("submissionCents"), amount("referralCents"), amount("bonusCents"),
                     balance.value("error").toString().isEmpty() ? QStringLiteral("Atualizado: ") + friendlyDate(balance.value("updated_at").toString())
                                                               : QStringLiteral("Última leitura · ") + balance.value("error").toString()));
      });
      actionsLayout->addWidget(credentials);
      auto* withdraw = new QPushButton(isClaru ? QStringLiteral("Abrir pagamentos") : QStringLiteral("Solicitar saque"));
      withdraw->setMinimumHeight(32);
      withdraw->setEnabled(isClaru || (payoutEligible && !_withdrawPending.contains(email)
                           && runner.value(QStringLiteral("state")).toString() != QStringLiteral("running")
                           && bulk.value(QStringLiteral("state")).toString() != QStringLiteral("running")));
      withdraw->setToolTip(isClaru ? QStringLiteral("Abre o painel Claru. Use o login Claru desta conta.") : withdraw->isEnabled()
          ? QStringLiteral("Solicita um link de saque para o saldo disponível confirmado desta conta.")
          : payout.value(QStringLiteral("reason")).toString(QStringLiteral("Atualize o saldo e a elegibilidade antes de solicitar saque.")));
      connect(withdraw, &QPushButton::clicked, this, [this, email, withdraw, isClaru] {
        if (isClaru) { QDesktopServices::openUrl(QUrl(QStringLiteral("https://app.claru.ai/payments"))); return; }
        QJsonObject withdrawal{{QStringLiteral("email"), email}};
        if (!confirmWithdrawal(this, 1, withdrawal)) return;
        _withdrawPending.insert(email);
        withdraw->setEnabled(false);
        QPointer<QPushButton> button(withdraw);
        _api.post(QStringLiteral("/api/balances/withdraw"), withdrawal,
                  [this, email, button](bool ok, const QJsonDocument& doc, const QString& error) {
          _withdrawPending.remove(email);
          if (button) button->setEnabled(false);
          if (!ok) {
            showError(QStringLiteral("Saque não confirmado"), error);
            loadBalances();
            return;
          }
          QMessageBox::information(this, QStringLiteral("Resultado da solicitação"),
                                   doc.object().value(QStringLiteral("message")).toString());
          loadBalances();
        });
      });
      actionsLayout->addWidget(withdraw);
      _balancesTable->setCellWidget(row, 4, actions);
      ++row;
    }
    applyBalanceFilter();
    _balancesApprovedUsd->setText(usdMoney(approvedTotal));
    _balancesPendingUsd->setText(usdMoney(pendingTotal));
    _balancesTotalsNote->setVisible(staleTotals > 0);
    if (staleTotals > 0)
      _balancesTotalsNote->setText(QStringLiteral(
          "Os totais incluem o último valor salvo de %1 conta(s) com consulta inconclusiva; atualize os saldos antes de decidir sobre saques.")
          .arg(staleTotals));
    const bool exchangeAvailable = exchange.value(QStringLiteral("available")).toBool();
    const double usdBrlRate = exchange.value(QStringLiteral("rate")).toDouble();
    if (exchangeAvailable && usdBrlRate > 0.0) {
      _balancesApprovedBrl->setText(QStringLiteral("≈ %1").arg(brlMoney(approvedTotal, usdBrlRate)));
      _balancesPendingBrl->setText(QStringLiteral("≈ %1").arg(brlMoney(pendingTotal, usdBrlRate)));
      const QDate quoteDate = QDate::fromString(
          exchange.value(QStringLiteral("quote_date")).toString(), Qt::ISODate);
      const QString dateText = quoteDate.isValid()
          ? QLocale(QStringLiteral("pt_BR")).toString(quoteDate, QStringLiteral("dd/MM/yyyy"))
          : QStringLiteral("data indisponível");
      _balancesExchange->setText(QStringLiteral("%1Cotação BCB de venda · US$ 1 = R$ %2 · %3")
          .arg(exchange.value(QStringLiteral("stale")).toBool()
                   ? QStringLiteral("Última cotação salva · ") : QString())
          .arg(QLocale(QStringLiteral("pt_BR")).toString(usdBrlRate, 'f', 4))
          .arg(dateText));
    } else {
      _balancesApprovedBrl->setText(QStringLiteral("≈ R$ —"));
      _balancesPendingBrl->setText(QStringLiteral("≈ R$ —"));
      _balancesExchange->setText(
          QStringLiteral("Conversão para BRL indisponível · os totais em USD permanecem válidos."));
    }
    const bool running = runner.value(QStringLiteral("state")).toString() == QStringLiteral("running");
    _balancesRefresh->setEnabled(!running);
    _balancesStop->setVisible(running);
    _balancesStop->setEnabled(running && runner.value(QStringLiteral("phase")).toString() != QStringLiteral("stopping"));
    const bool bulkRunning = bulk.value(QStringLiteral("state")).toString() == QStringLiteral("running");
    const int refreshNeeded = root.value(QStringLiteral("refresh_needed")).toArray().size();
    _balancesRefreshNeeded->setText(QStringLiteral("Atualizar pendentes (%1)").arg(refreshNeeded));
    _balancesRefreshNeeded->setEnabled(!running && !bulkRunning && refreshNeeded > 0);
    _balancesWithdrawAll->setProperty("eligibleCount", eligibleWithdrawals);
    _balancesWithdrawAll->setEnabled(!running && !bulkRunning && !_bulkWithdrawAwaitingResult && _withdrawPending.isEmpty() && eligibleWithdrawals > 0);
    if (_bulkWithdrawAwaitingResult && !bulkRunning
        && bulk.value(QStringLiteral("state")).toString() != QStringLiteral("idle")) {
      _bulkWithdrawAwaitingResult = false;
      showWithdrawalReport(bulk);
    }
    int claruCount = 0, crowtadoConnected = 0;
    for (const auto value : accounts) {
      if (accountKinds.value(value.toString()).toString() == QStringLiteral("claru"))
        ++claruCount;
      else if (passwordAccounts.contains(value.toString())) ++crowtadoConnected;
    }
    _balancesState->setText(runner.value(QStringLiteral("phase")).toString() == QStringLiteral("cooldown")
        ? runner.value(QStringLiteral("current")).toString(QStringLiteral("Limite temporário do serviço. Aguardando para tentar novamente."))
        : bulkRunning
        ? QStringLiteral("Solicitando saques: %1 de %2 conta(s)…")
              .arg(bulk.value(QStringLiteral("done")).toInt())
              .arg(bulk.value(QStringLiteral("total")).toInt())
        : bulk.value(QStringLiteral("state")).toString() == QStringLiteral("interrupted")
        ? QStringLiteral("Último lote interrompido · confira o relatório antes de solicitar novamente")
        : bulk.value(QStringLiteral("state")).toString() == QStringLiteral("error")
        ? QStringLiteral("Último lote precisa de atenção · abra o relatório")
        : running
        ? runner.value(QStringLiteral("current")).toString(QStringLiteral("Consultando contas…"))
        : QStringLiteral("%1 identidade(s) · %2 Crowtado conectado(s) · %3 Claru conectada(s)")
              .arg(accounts.size()).arg(crowtadoConnected).arg(claruCount));
    if (running || bulkRunning) _balancePoll.start(); else _balancePoll.stop();
  });
}

void MainWindow::applyBalanceFilter() {
  if (!_balancesTable || !_balancesSearch || !_balancesOnlyAvailable || !_balancesOnlyPending) return;
  const QString search = _balancesSearch->text().trimmed();
  const bool onlyAvailable = _balancesOnlyAvailable->isChecked();
  const bool onlyPending = _balancesOnlyPending->isChecked();
  const auto balances = _balancesSnapshot.value(QStringLiteral("balances")).toObject();
  QSet<QString> pendingEmails;
  for (const auto value : _balancesSnapshot.value(QStringLiteral("refresh_needed")).toArray())
    pendingEmails.insert(value.toString());
  int visible = 0;
  for (int row = 0; row < _balancesTable->rowCount(); ++row) {
    const auto* item = _balancesTable->item(row, 0);
    const QString email = item ? item->text() : QString();
    const bool matches = email.contains(search, Qt::CaseInsensitive);
    const auto balance = balances.value(email).toObject();
    const bool hasAvailable = balance.value(QStringLiteral("availableCents")).toDouble() > 0
        && balance.value(QStringLiteral("error")).toString().isEmpty()
        && !balance.value(QStringLiteral("stale")).toBool();
    const bool show = matches && (!onlyAvailable || hasAvailable)
        && (!onlyPending || pendingEmails.contains(email));
    _balancesTable->setRowHidden(row, !show);
    if (show) ++visible;
  }
  if (_balancesFilterState)
    _balancesFilterState->setText(QStringLiteral("%1 de %2 contas")
        .arg(visible).arg(_balancesTable->rowCount()));
}

void MainWindow::showWithdrawalReport(const QJsonObject& bulk) {
  const QString state = bulk.value(QStringLiteral("state")).toString();
  const int total = bulk.value(QStringLiteral("total")).toInt();
  const int done = bulk.value(QStringLiteral("done")).toInt();
  int sent = 0;
  QStringList details;
  for (const auto value : bulk.value(QStringLiteral("results")).toArray()) {
    const auto result = value.toObject();
    if (result.value(QStringLiteral("ok")).toBool()) ++sent;
    details << QStringLiteral("%1: %2")
        .arg(result.value(QStringLiteral("email")).toString(),
             result.value(QStringLiteral("message")).toString());
  }
  const QString current = bulk.value(QStringLiteral("current")).toString();
  if ((state == QStringLiteral("interrupted") || state == QStringLiteral("error"))
      && !current.isEmpty())
    details << QStringLiteral("%1: resultado inconclusivo após interrupção; confira se o link chegou antes de repetir.").arg(current);
  const QString message = bulk.value(QStringLiteral("message")).toString();
  if (!message.isEmpty()) details << message;
  QMessageBox report(QMessageBox::Information, QStringLiteral("Último lote de saques"),
      QStringLiteral("%1 de %2 conta(s) processadas; %3 link(s) solicitado(s). "
                     "Cada saque ainda exige confirmação e 2FA no link da conta.")
          .arg(done).arg(total).arg(sent), QMessageBox::Ok, this);
  report.setDetailedText(details.join('\n'));
  report.exec();
}

void MainWindow::configureCrowtadoAccess(const QString& email) {
  bool accepted = false;
  const QString password = QInputDialog::getText(
      this, QStringLiteral("Conectar Crowtado"),
      QStringLiteral("Senha do Crowtado para %1:").arg(email),
      QLineEdit::Password, QString(), &accepted);
  if (!accepted || password.isEmpty()) return;

  setStatus(QStringLiteral("Validando acesso de %1 no Crowtado…").arg(email));
  _api.put(QStringLiteral("/api/balances/credentials"),
           {{QStringLiteral("email"), email},
            {QStringLiteral("password"), password}},
           [this, email](bool ok, const QJsonDocument&, const QString& error) {
    if (!ok)
      return showError(QStringLiteral("Crowtado não conectado"), error);
    setStatus(QStringLiteral("Crowtado conectado para %1. Consultando saldo…").arg(email));
    QJsonObject refresh;
    refresh.insert(QStringLiteral("emails"), QJsonArray{email});
    _api.post(QStringLiteral("/api/balances/refresh"), refresh,
              [this](bool refreshed, const QJsonDocument&, const QString& refreshError) {
      if (!refreshed)
        return showError(QStringLiteral("Acesso salvo; saldo ainda não consultado"),
                         refreshError);
      _balancePoll.start();
      loadBalances();
    });
  });
}

void MainWindow::loadHistory() {
  _api.get(QStringLiteral("/api/logs"), [this](bool ok, const QJsonDocument& doc, const QString& error) {
    if (!ok) return showError(QStringLiteral("Falha ao carregar histórico"), error);
    const auto logs = doc.object().value(QStringLiteral("logs")).toArray();
    _historyTable->setRowCount(logs.size());
    int row = 0;
    for (const auto value : logs) {
      const auto log = value.toObject();
      auto* date = cell(friendlyDate(log.value(QStringLiteral("started_at")).toString()));
      date->setData(Qt::UserRole, log.value(QStringLiteral("name")).toString());
      _historyTable->setItem(row, 0, date);
      QStringList accounts;
      for (const auto account : log.value(QStringLiteral("accounts")).toArray()) accounts << account.toString();
      _historyTable->setItem(row, 1, cell(accounts.join(QStringLiteral(", "))));
      _historyTable->setItem(row, 2, cell(QString::number(log.value(QStringLiteral("items")).toInt())));
      _historyTable->setItem(row, 3, cell(QStringLiteral("%1/%2")
          .arg(log.value(QStringLiteral("ok")).toInt()).arg(log.value(QStringLiteral("sends")).toInt())));
      ++row;
    }
    _historyTable->resizeRowsToContents();
    setStatus(QStringLiteral("%1 campanha(s) no histórico.").arg(logs.size()));
  });
}

QWidget* MainWindow::buildBalancesPage() {
  auto* body = new QWidget;
  auto* layout = new QVBoxLayout(body);
  layout->setContentsMargins(0, 0, 0, 0);
  layout->setSpacing(14);
  auto* header = new QWidget;
  auto* headerLayout = new QHBoxLayout(header);
  headerLayout->setContentsMargins(0, 0, 0, 0);
  auto* identityCopy = new QWidget;
  auto* identityLayout = new QVBoxLayout(identityCopy);
  identityLayout->setContentsMargins(0, 0, 0, 0);
  identityLayout->setSpacing(3);
  _balancesState = quietLabel(QStringLiteral("Aguardando leitura…"));
  identityLayout->addWidget(_balancesState);
  auto* identityHelp = quietLabel(QStringLiteral(
      "Crowtado usa o e-mail do Minute. Claru usa o login Claru para consultar saldo "
      "e o e-mail gerado pelo perfil para as campanhas."));
  identityHelp->setWordWrap(true);
  identityLayout->addWidget(identityHelp);
  headerLayout->addWidget(identityCopy, 1);
  _balancesRefresh = primaryButton(QStringLiteral("Atualizar todos"));
  connect(_balancesRefresh, &QPushButton::clicked, this, [this] {
    _balancesRefresh->setEnabled(false);
    _api.post(QStringLiteral("/api/balances/refresh"), {}, [this](bool ok, const QJsonDocument&, const QString& error) {
      if (!ok) {
        _balancesRefresh->setEnabled(true);
        showError(QStringLiteral("Não foi possível atualizar"), error);
      } else {
        _balancePoll.start();
        setStatus(QStringLiteral("Consulta de saldos iniciada."));
      }
    });
  });
  headerLayout->addWidget(_balancesRefresh);
  _balancesRefreshNeeded = new QPushButton(QStringLiteral("Atualizar pendentes"));
  _balancesRefreshNeeded->setEnabled(false);
  _balancesRefreshNeeded->setToolTip(QStringLiteral(
      "Consulta só contas Crowtado conectadas sem saldo confirmado, com erro ou com leitura de mais de 24 horas."));
  connect(_balancesRefreshNeeded, &QPushButton::clicked, this, [this] {
    const auto emails = _balancesSnapshot.value(QStringLiteral("refresh_needed")).toArray();
    if (emails.isEmpty()) return;
    _balancesRefreshNeeded->setEnabled(false);
    _api.post(QStringLiteral("/api/balances/refresh"),
              {{QStringLiteral("emails"), emails}},
              [this, count = emails.size()](bool ok, const QJsonDocument&, const QString& error) {
      if (!ok) {
        loadBalances();
        return showError(QStringLiteral("Não foi possível atualizar pendentes"), error);
      }
      _balancePoll.start();
      setStatus(QStringLiteral("Consultando %1 conta(s) pendente(s)…").arg(count));
    });
  });
  _balancesStop = new QPushButton(QStringLiteral("Parar consulta"));
  _balancesStop->hide();
  _balancesStop->setToolTip(QStringLiteral("Conclui a conta atual e deixa as próximas para outra consulta."));
  headerLayout->addWidget(_balancesStop);
  connect(_balancesStop, &QPushButton::clicked, this, [this] {
    _balancesStop->setEnabled(false);
    _api.post(QStringLiteral("/api/balances/stop"), {}, [this](bool ok, const QJsonDocument&, const QString& error) {
      loadBalances();
      if (!ok) showError(QStringLiteral("Consulta não interrompida"), error);
    });
  });
  _balancesWithdrawAll = new QPushButton(QStringLiteral("Sacar tudo"));
  _balancesWithdrawAll->setEnabled(false);
  _balancesWithdrawAll->setToolTip(QStringLiteral(
      "Solicita links para todas as contas Crowtado elegíveis, inclusive as ocultas pelo filtro da tabela."));
  connect(_balancesWithdrawAll, &QPushButton::clicked, this, [this] {
    const int eligible = _balancesWithdrawAll->property("eligibleCount").toInt();
    QJsonObject withdrawal;
    if (!confirmWithdrawal(this, eligible, withdrawal)) return;
    _bulkWithdrawAwaitingResult = true;
    _balancesWithdrawAll->setEnabled(false);
    _api.post(QStringLiteral("/api/balances/withdraw-all"), withdrawal,
              [this](bool ok, const QJsonDocument& doc, const QString& error) {
      if (!ok) {
        _bulkWithdrawAwaitingResult = false;
        loadBalances();
        return showError(QStringLiteral("Saque em lote não iniciado"), error);
      }
      _bulkWithdrawAwaitingResult = true;
      _balancePoll.start();
      setStatus(QStringLiteral("Solicitando links de saque para %1 conta(s)…")
          .arg(doc.object().value(QStringLiteral("total")).toInt()));
    });
  });
  headerLayout->addWidget(_balancesWithdrawAll);
  _balancesWithdrawHistory = new QPushButton(QStringLiteral("Último lote"));
  _balancesWithdrawHistory->setEnabled(false);
  _balancesWithdrawHistory->setToolTip(QStringLiteral(
      "Mostra o resultado por conta do último lote de solicitações, mesmo após reiniciar o aplicativo."));
  connect(_balancesWithdrawHistory, &QPushButton::clicked, this, [this] {
    showWithdrawalReport(_lastWithdrawBulk);
  });
  headerLayout->addWidget(_balancesWithdrawHistory);
  _balancesExport = new QPushButton(QStringLiteral("Exportar CSV"));
  _balancesExport->setEnabled(false);
  _balancesExport->setToolTip(QStringLiteral("Salva os saldos exibidos em uma planilha CSV."));
  connect(_balancesExport, &QPushButton::clicked, this, [this] {
    QString path = QFileDialog::getSaveFileName(this, QStringLiteral("Exportar saldos"),
        QStringLiteral("QMoney-saldos-%1.csv")
            .arg(QDateTime::currentDateTime().toString(QStringLiteral("yyyyMMdd-HHmmss"))),
        QStringLiteral("CSV (*.csv)"));
    if (path.isEmpty()) return;
    if (!path.endsWith(QStringLiteral(".csv"), Qt::CaseInsensitive)) path += QStringLiteral(".csv");
    const auto balances = _balancesSnapshot.value(QStringLiteral("balances")).toObject();
    const auto kinds = _balancesSnapshot.value(QStringLiteral("account_kinds")).toObject();
    const auto csvCell = [](QString value) {
      const QString trimmed = value.trimmed();
      if (!trimmed.isEmpty() && QStringLiteral("=+-@").contains(trimmed.front()))
        value.prepend(QLatin1Char('\''));
      value.replace(QLatin1Char('"'), QStringLiteral("\"\""));
      return QStringLiteral("\"") + value + QStringLiteral("\"");
    };
    QStringList lines{QStringLiteral("Conta;Tipo;Disponível (USD);Pendente (USD);Atualizado;Situação")};
    for (int row = 0; row < _balancesTable->rowCount(); ++row) {
      if (_balancesTable->isRowHidden(row)) continue;
      const auto* emailItem = _balancesTable->item(row, 0);
      if (!emailItem) continue;
      const QString email = emailItem->text();
      const auto balance = balances.value(email).toObject();
      const bool isClaru = kinds.value(email).toString() == QStringLiteral("claru");
      const auto money = [](const QJsonValue& value) {
        return value.isDouble()
            ? QString::number(value.toDouble() / 100.0, 'f', 2).replace(QLatin1Char('.'), QLatin1Char(','))
            : QString();
      };
      const QString state = !balance.value(QStringLiteral("error")).toString().isEmpty()
              || balance.value(QStringLiteral("stale")).toBool()
          ? QStringLiteral("Consulta inconclusiva; valores salvos")
          : balance.value(QStringLiteral("availableCents")).isDouble()
          ? QStringLiteral("Confirmado") : QStringLiteral("Ainda não consultado");
      lines << QStringList{
          csvCell(email), csvCell(isClaru ? QStringLiteral("Claru") : QStringLiteral("Crowtado")),
          csvCell(money(balance.value(QStringLiteral("availableCents")))),
          csvCell(money(balance.value(QStringLiteral("pendingCents")))),
          csvCell(balance.value(QStringLiteral("updated_at")).toString()), csvCell(state),
      }.join(QLatin1Char(';'));
    }
    QSaveFile output(path);
    const QByteArray bytes = QByteArray::fromHex("efbbbf") + lines.join(QLatin1Char('\n')).toUtf8();
    if (!output.open(QIODevice::WriteOnly) || output.write(bytes) != bytes.size() || !output.commit()) {
      showError(QStringLiteral("Saldos não exportados"), QStringLiteral("Não foi possível salvar o CSV."));
      return;
    }
    setStatus(QStringLiteral("Saldos exportados para %1.").arg(QDir::toNativeSeparators(path)));
  });
  headerLayout->addWidget(_balancesExport);
  layout->addWidget(card(QStringLiteral("Disponibilidade"), header));

  auto* filters = new QWidget;
  auto* filtersLayout = new QHBoxLayout(filters);
  filtersLayout->setContentsMargins(0, 0, 0, 0);
  _balancesSearch = new QLineEdit;
  _balancesSearch->setPlaceholderText(QStringLiteral("Buscar conta por e-mail"));
  _balancesSearch->setClearButtonEnabled(true);
  connect(_balancesSearch, &QLineEdit::textChanged, this, &MainWindow::applyBalanceFilter);
  filtersLayout->addWidget(_balancesSearch, 1);
  _balancesOnlyAvailable = new QCheckBox(QStringLiteral("Só com saldo disponível"));
  connect(_balancesOnlyAvailable, &QCheckBox::toggled, this, [this](bool checked) {
    if (checked) _balancesOnlyPending->setChecked(false);
    applyBalanceFilter();
  });
  filtersLayout->addWidget(_balancesOnlyAvailable);
  _balancesOnlyPending = new QCheckBox(QStringLiteral("Só pendentes de atualização"));
  _balancesOnlyPending->setToolTip(QStringLiteral(
      "Mostra contas conectadas sem leitura confirmada, com erro ou com saldo de mais de 24 horas."));
  connect(_balancesOnlyPending, &QCheckBox::toggled, this, [this](bool checked) {
    if (checked) _balancesOnlyAvailable->setChecked(false);
    applyBalanceFilter();
  });
  filtersLayout->addWidget(_balancesOnlyPending);
  filtersLayout->addWidget(_balancesRefreshNeeded);
  _balancesFilterState = quietLabel(QStringLiteral("0 contas"));
  filtersLayout->addWidget(_balancesFilterState);
  layout->addWidget(filters);

  _balancesTable = new QTableWidget(0, 5);
  configureTable(_balancesTable);
  _balancesTable->setHorizontalHeaderLabels(
      {QStringLiteral("Conta"), QStringLiteral("Disponível"), QStringLiteral("Pendente"),
       QStringLiteral("Atualizado"), QStringLiteral("Ação")});
  _balancesTable->horizontalHeader()->setSectionResizeMode(0, QHeaderView::Stretch);
  _balancesTable->horizontalHeader()->setSectionResizeMode(1, QHeaderView::Fixed);
  _balancesTable->horizontalHeader()->setSectionResizeMode(2, QHeaderView::Fixed);
  _balancesTable->horizontalHeader()->setSectionResizeMode(3, QHeaderView::Interactive);
  _balancesTable->horizontalHeader()->setSectionResizeMode(4, QHeaderView::Fixed);
  _balancesTable->setColumnWidth(1, 126);
  _balancesTable->setColumnWidth(2, 126);
  _balancesTable->setColumnWidth(3, 176);
  _balancesTable->setColumnWidth(4, 292);
  _balancesTable->horizontalHeaderItem(1)->setToolTip(
      QStringLiteral("Saldo aprovado Crowtado ou saldo atual informado pelo Claru, em dólar."));
  _balancesTable->horizontalHeaderItem(2)->setToolTip(
      QStringLiteral("Valor em dólar registrado pelo Crowtado que ainda não foi liberado para saque."));
  layout->addWidget(card(QStringLiteral("Saldos · Crowtado e Claru"), _balancesTable), 1);

  auto* summaryBody = new QWidget;
  auto* summaryLayout = new QVBoxLayout(summaryBody);
  summaryLayout->setContentsMargins(0, 0, 0, 0);
  summaryLayout->setSpacing(10);
  auto* totals = new QWidget;
  auto* totalsLayout = new QHBoxLayout(totals);
  totalsLayout->setContentsMargins(0, 0, 0, 0);
  totalsLayout->setSpacing(28);
  const auto totalBlock = [](const QString& title, QLabel** usd, QLabel** brl) {
    auto* block = new QWidget;
    auto* blockLayout = new QVBoxLayout(block);
    blockLayout->setContentsMargins(0, 0, 0, 0);
    blockLayout->setSpacing(3);
    auto* caption = new QLabel(title.toUpper());
    caption->setObjectName(QStringLiteral("balanceTotalCaption"));
    blockLayout->addWidget(caption);
    *usd = new QLabel(QStringLiteral("US$ 0,00"));
    (*usd)->setObjectName(QStringLiteral("balanceTotalUsd"));
    blockLayout->addWidget(*usd);
    *brl = new QLabel(QStringLiteral("≈ R$ —"));
    (*brl)->setObjectName(QStringLiteral("balanceTotalBrl"));
    blockLayout->addWidget(*brl);
    return block;
  };
  totalsLayout->addWidget(totalBlock(
      QStringLiteral("Total disponível"), &_balancesApprovedUsd, &_balancesApprovedBrl), 1);
  auto* divider = new QFrame;
  divider->setFrameShape(QFrame::VLine);
  divider->setObjectName(QStringLiteral("balanceDivider"));
  totalsLayout->addWidget(divider);
  totalsLayout->addWidget(totalBlock(
      QStringLiteral("Total pendente"), &_balancesPendingUsd, &_balancesPendingBrl), 1);
  summaryLayout->addWidget(totals);
  _balancesTotalsNote = quietLabel(QString());
  _balancesTotalsNote->setWordWrap(true);
  _balancesTotalsNote->hide();
  summaryLayout->addWidget(_balancesTotalsNote);
  _balancesExchange = quietLabel(QStringLiteral("Carregando cotação USD/BRL…"));
  _balancesExchange->setObjectName(QStringLiteral("balanceExchange"));
  summaryLayout->addWidget(_balancesExchange);
  layout->addWidget(card(QStringLiteral("Resumo em dólar"), summaryBody));

  return pageShell(QStringLiteral("Saldos"),
                   QStringLiteral("Acompanhe valores disponíveis e pendentes e solicite o link de saque."), body);
}

QWidget* MainWindow::buildHistoryPage() {
  auto* body = new QWidget;
  auto* layout = new QHBoxLayout(body);
  layout->setContentsMargins(0, 0, 0, 0);
  layout->setSpacing(14);
  _historyTable = new QTableWidget(0, 4);
  configureTable(_historyTable);
  _historyTable->setHorizontalHeaderLabels(
      {QStringLiteral("Início"), QStringLiteral("Contas"), QStringLiteral("Vídeos"), QStringLiteral("Envios OK")});
  _historyTable->horizontalHeader()->setSectionResizeMode(0, QHeaderView::Fixed);
  _historyTable->horizontalHeader()->setSectionResizeMode(1, QHeaderView::Stretch);
  _historyTable->horizontalHeader()->setSectionResizeMode(2, QHeaderView::Fixed);
  _historyTable->horizontalHeader()->setSectionResizeMode(3, QHeaderView::Fixed);
  _historyTable->setColumnWidth(0, 185);
  _historyTable->setColumnWidth(2, 92);
  _historyTable->setColumnWidth(3, 110);
  _historyTable->setSelectionBehavior(QAbstractItemView::SelectRows);
  _historyTable->setSelectionMode(QAbstractItemView::SingleSelection);
  _historyDetail = new QPlainTextEdit;
  _historyDetail->setReadOnly(true);
  _historyDetail->setPlaceholderText(QStringLiteral("Selecione uma campanha para ver o registro completo."));
  auto* verifyPreviews = new QPushButton(QStringLiteral("Verificar prévias no Minute"));
  connect(verifyPreviews, &QPushButton::clicked, this, [this, verifyPreviews] {
    const int row = _historyTable->currentRow();
    if (row < 0 || !_historyTable->item(row, 0)) {
      return showError(QStringLiteral("Selecione uma campanha"),
                       QStringLiteral("Escolha uma linha do histórico antes de verificar as prévias."));
    }
    const QString name = _historyTable->item(row, 0)->data(Qt::UserRole).toString();
    if (name.isEmpty()) return;
    verifyPreviews->setEnabled(false);
    verifyPreviews->setText(QStringLiteral("Consultando o Minute…"));
    setStatus(QStringLiteral("Consultando o processamento real das prévias no Minute…"));
    _api.post(QStringLiteral("/api/logs/") + encoded(name) + QStringLiteral("/status"), {},
              [this, verifyPreviews](bool ok, const QJsonDocument& doc, const QString& error) {
      verifyPreviews->setEnabled(true);
      verifyPreviews->setText(QStringLiteral("Verificar prévias no Minute"));
      if (!ok) return showError(QStringLiteral("Falha ao verificar prévias"), error);
      const auto summary = doc.object().value(QStringLiteral("summary")).toObject();
      const int ready = summary.value(QStringLiteral("ready")).toInt();
      const int pending = summary.value(QStringLiteral("pending")).toInt();
      const int unavailable = summary.value(QStringLiteral("unavailable")).toInt();
      const int errors = summary.value(QStringLiteral("errors")).toInt();
      QStringList attention;
      for (const auto value : doc.object().value(QStringLiteral("results")).toArray()) {
        const auto result = value.toObject();
        const QString status = result.value(QStringLiteral("status")).toString();
        const QString email = result.value(QStringLiteral("email")).toString();
        if (status.startsWith(QStringLiteral("unprocessed:unavailable"))) {
          attention << QStringLiteral("× %1 — prévia indisponível").arg(email);
        } else if (status != QStringLiteral("preview_ready")
                   && status != QStringLiteral("unprocessed:pending")
                   && status != QStringLiteral("processing")) {
          attention << QStringLiteral("× %1 — %2").arg(email, status);
        }
      }
      QStringList lines;
      lines << _historyDetail->toPlainText()
            << QString()
            << QStringLiteral("PROCESSAMENTO NO MINUTE")
            << QStringLiteral("✓ %1 arquivo(s) pronto(s)  ·  %2 processando  ·  %3 indisponível(is)  ·  %4 erro(s)")
                   .arg(ready).arg(pending).arg(unavailable).arg(errors);
      if (pending > 0)
        lines << QStringLiteral("Os arquivos foram recebidos; o Minute ainda não publicou essas prévias.");
      lines << attention;
      _historyDetail->setPlainText(lines.join(QLatin1Char('\n')));
      setStatus(QStringLiteral("Arquivos de prévia: %1 prontos, %2 processando, %3 indisponíveis.")
                    .arg(ready).arg(pending).arg(unavailable));
    });
  });
  connect(_historyTable, &QTableWidget::cellClicked, this, [this](int row, int) {
    const QString name = _historyTable->item(row, 0)->data(Qt::UserRole).toString();
    if (name.isEmpty()) return;
    _api.get(QStringLiteral("/api/logs/") + encoded(name),
             [this](bool ok, const QJsonDocument& doc, const QString& error) {
      if (!ok) return showError(QStringLiteral("Falha ao abrir registro"), error);
      const auto root = doc.object();
      const auto summary = root.value(QStringLiteral("summary")).toObject();
      QStringList lines;
      lines << QStringLiteral("RESULTADO DA CAMPANHA")
            << friendlyDate(root.value(QStringLiteral("started_at")).toString())
            << QString()
            << QStringLiteral("VISÃO GERAL")
            << QStringLiteral("%1 vídeo(s) · %2 envio(s) concluído(s) · %3 ignorado(s) · %4 falha(s)")
                   .arg(summary.value(QStringLiteral("videos")).toInt())
                   .arg(summary.value(QStringLiteral("success")).toInt())
                   .arg(summary.value(QStringLiteral("skipped")).toInt())
                   .arg(summary.value(QStringLiteral("failed")).toInt())
            << QString();
      const auto issues = root.value(QStringLiteral("issues")).toArray();
      if (!issues.isEmpty()) {
        lines << QStringLiteral("PENDÊNCIAS");
        for (const auto issueValue : issues) {
          const auto issue = issueValue.toObject();
          lines << QStringLiteral("! %1 — %2")
                       .arg(issue.value(QStringLiteral("title")).toString(),
                            issue.value(QStringLiteral("detail")).toString());
        }
        lines << QString();
      }
      lines << QStringLiteral("POR CONTA");
      for (const auto accountValue : root.value(QStringLiteral("accounts")).toArray()) {
        const auto account = accountValue.toObject();
        const int success = account.value(QStringLiteral("success")).toInt();
        const int skipped = account.value(QStringLiteral("skipped")).toInt();
        const int failed = account.value(QStringLiteral("failed")).toInt();
        const QString marker = failed > 0 ? QStringLiteral("×")
                             : success > 0 ? QStringLiteral("✓") : QStringLiteral("•");
        lines << QStringLiteral("%1  %2").arg(marker, account.value(QStringLiteral("email")).toString())
              << QStringLiteral("    %1 envio(s) concluído(s) · %2 ignorado(s) · %3 falha(s)")
                     .arg(success).arg(skipped).arg(failed);
      }
      lines << QString() << QStringLiteral("CONTEÚDO PROCESSADO");
      for (const auto itemValue : root.value(QStringLiteral("items")).toArray()) {
        const auto item = itemValue.toObject();
        const int duration = item.value(QStringLiteral("duration_s")).toInt();
        const int minutes = duration / 60;
        const int seconds = duration % 60;
        const QString durationText = minutes > 0
            ? QStringLiteral("%1min %2s").arg(minutes).arg(seconds, 2, 10, QLatin1Char('0'))
            : QStringLiteral("%1s").arg(seconds);
        const int failed = item.value(QStringLiteral("failed")).toInt();
        const QString marker = failed > 0 ? QStringLiteral("×") : QStringLiteral("✓");
        lines << QStringLiteral("%1  %2 · %3")
                     .arg(marker, item.value(QStringLiteral("task")).toString(), durationText)
              << QStringLiteral("    %1 envio(s) concluído(s) · %2 ignorado(s) · %3 falha(s)")
                     .arg(item.value(QStringLiteral("success")).toInt())
                     .arg(item.value(QStringLiteral("skipped")).toInt())
                     .arg(failed);
        for (const auto resultValue : item.value(QStringLiteral("accounts")).toArray()) {
          const auto result = resultValue.toObject();
          if (result.value(QStringLiteral("status")).toString() == QStringLiteral("success")) continue;
          lines << QStringLiteral("      ! %1 — %2")
                       .arg(result.value(QStringLiteral("email")).toString(),
                            result.value(QStringLiteral("detail")).toString());
        }
      }
      _historyDetail->setPlainText(lines.join(QLatin1Char('\n')));
    });
  });
  auto* detailBody = new QWidget;
  auto* detailLayout = new QVBoxLayout(detailBody);
  detailLayout->setContentsMargins(0, 0, 0, 0);
  detailLayout->setSpacing(10);
  detailLayout->addWidget(verifyPreviews, 0, Qt::AlignRight);
  detailLayout->addWidget(_historyDetail, 1);
  layout->addWidget(card(QStringLiteral("Campanhas"), _historyTable), 3);
  layout->addWidget(card(QStringLiteral("Registro"), detailBody), 2);
  return pageShell(QStringLiteral("Histórico"),
                   QStringLiteral("Audite campanhas anteriores e seus resultados por conta."), body);
}

