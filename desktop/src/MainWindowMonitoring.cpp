#include "MainWindowSupport.hpp"
#include "MainWindow.hpp"


void MainWindow::onMonitoringPageActivated(int index) {
  if (index != 8) _bannedPoll.stop();
}

void MainWindow::shutdownMonitoringTimers() {
  _bannedPoll.stop();
}

void MainWindow::loadBanned() {
  if (_bannedPolling) return;
  _bannedPolling = true;
  _api.get(QStringLiteral("/api/accounts/banned/monitor"),
    [this](bool ok, const QJsonDocument& doc, const QString& error) {
      _bannedPolling = false;
      if (!ok) { _bannedState->setText(error); return; }
      const auto root = doc.object();
      const auto rows = root.value(QStringLiteral("accounts")).toArray();
      const auto runner = root.value(QStringLiteral("runner")).toObject();
      const bool running = runner.value(QStringLiteral("state")).toString() == QStringLiteral("running");
      _bannedRefresh->setEnabled(!running && !rows.isEmpty());
      if (running) {
        _bannedState->setText(QStringLiteral("Consultando %1 de %2 contas…")
            .arg(runner.value(QStringLiteral("completed")).toInt()).arg(runner.value(QStringLiteral("total")).toInt()));
        if (isPageActive(8)) _bannedPoll.start();
      } else {
        _bannedPoll.stop();
        _bannedState->setText(runner.value(QStringLiteral("error")).toString(
            rows.isEmpty() ? QStringLiteral("Nenhuma conta banida registrada.") : QStringLiteral("%1 contas no registro de banidas").arg(rows.size())));
      }
      _bannedTable->setRowCount(rows.size());
      for (int row = 0; row < rows.size(); ++row) {
        const auto account = rows[row].toObject();
        const auto monitor = account.value(QStringLiteral("monitor")).toObject();
        const auto balance = monitor.value(QStringLiteral("balance")).toObject();
        const QString suffix = monitor.value(QStringLiteral("balance_stale")).toBool() ? QStringLiteral(" *") : QString();
        const QString absent = monitor.value(QStringLiteral("balance_status")).toString() == QStringLiteral("not_applicable") ? QStringLiteral("Não se aplica") : QStringLiteral("Não consultado");
        QStringList values{account.value(QStringLiteral("email")).toString(),
            monitor.value(QStringLiteral("status_label")).toString(account.value(QStringLiteral("reason")).toString(account.value(QStringLiteral("has_password")).toBool() ? QStringLiteral("Ainda não consultada") : QStringLiteral("Sem senha salva"))),
            balance.contains(QStringLiteral("availableCents")) ? usdMoney(balance.value(QStringLiteral("availableCents")).toInteger()) + suffix : absent,
            balance.contains(QStringLiteral("pendingCents")) ? usdMoney(balance.value(QStringLiteral("pendingCents")).toInteger()) + suffix : absent,
            friendlyDate(account.value(QStringLiteral("banned_at")).toString()),
            friendlyDate(monitor.value(QStringLiteral("checked_at")).toString())};
        const QString detail = account.value(QStringLiteral("stage")).toString() + QStringLiteral("\n")
            + account.value(QStringLiteral("diagnostic")).toObject().value(QStringLiteral("detail")).toString() + QStringLiteral("\n")
            + monitor.value(QStringLiteral("detail")).toString() + QStringLiteral("\n")
            + monitor.value(QStringLiteral("balance_detail")).toString() + QStringLiteral("\nSaldo atualizado: ")
            + friendlyDate(monitor.value(QStringLiteral("balance_updated_at")).toString());
        for (int col = 0; col < values.size(); ++col) {
          auto* item = new QTableWidgetItem(values[col]);
          item->setToolTip(detail.trimmed());
          _bannedTable->setItem(row, col, item);
        }
      }
    });
}

QWidget* MainWindow::buildBannedPage() {
  auto* body = new QWidget;
  auto* layout = new QVBoxLayout(body);
  layout->setContentsMargins(0, 0, 0, 0);
  auto* actions = new QHBoxLayout;
  _bannedState = quietLabel(QStringLiteral("Carregando contas banidas…"));
  actions->addWidget(_bannedState, 1);
  _bannedRefresh = primaryButton(QStringLiteral("Consultar saldos e status"));
  actions->addWidget(_bannedRefresh);
  layout->addLayout(actions);
  auto* help = quietLabel(QStringLiteral(
      "O status é consultado no Minute e o saldo no Crowtado. Contas desbanidas permanecem fora das campanhas. "
      "Valores com * são da última consulta de saldo concluída."));
  help->setWordWrap(true);
  layout->addWidget(help);
  _bannedTable = new QTableWidget(0, 6);
  configureTable(_bannedTable);
  _bannedTable->setHorizontalHeaderLabels({QStringLiteral("E-mail"), QStringLiteral("Status atual"),
      QStringLiteral("Disponível (USD)"), QStringLiteral("Pendente (USD)"),
      QStringLiteral("Banimento"), QStringLiteral("Última consulta")});
  _bannedTable->horizontalHeader()->setSectionResizeMode(QHeaderView::ResizeToContents);
  _bannedTable->horizontalHeader()->setSectionResizeMode(0, QHeaderView::Stretch);
  layout->addWidget(_bannedTable, 1);
  _bannedPoll.setInterval(2000);
  connect(&_bannedPoll, &QTimer::timeout, this, &MainWindow::loadBanned);
  connect(_bannedRefresh, &QPushButton::clicked, this, [this] {
    _bannedRefresh->setEnabled(false);
    _api.post(QStringLiteral("/api/accounts/banned/refresh"), {},
      [this](bool ok, const QJsonDocument&, const QString& error) {
        if (!ok) {
          _bannedRefresh->setEnabled(true);
          return showError(QStringLiteral("Consulta não iniciada"), error);
        }
        _bannedPoll.start();
        loadBanned();
      });
  });
  return pageShell(QStringLiteral("Banidas"), QStringLiteral("Saldos e situação atual das contas removidas."), body);
}

