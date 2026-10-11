#include "MainWindowSupport.hpp"
#include "MainWindow.hpp"

MainWindow::MainWindow(oclero::qlementine::QlementineStyle* style, QWidget* parent, bool startServices)
    : QMainWindow(parent), _style(style), _api(this), _backend(this) {
  setWindowTitle(QStringLiteral("QMoney — Operations Desk"));
  resize(1280, 820);
  setMinimumSize(980, 680);

  initializeBackendProbe();
  initializeCampaignTimers();
  initializeBalanceTimers();

  buildShell();
  qInfo() << "QMoney: shell pronto";
  restorePendingCampaignStart();
  const bool dark = QSettings().value(QStringLiteral("darkTheme"), false).toBool();
  // Aplicado apos a primeira passagem do event loop. O Qlementine instala
  // filtros de evento durante a construcao dos widgets; adiar o stylesheet
  // evita uma repolish aninhada nessa mesma pilha.
  QTimer::singleShot(0, this, [this, dark] { applyStructuralStyle(dark); });
  _themeButton->setText(dark ? QStringLiteral("☀  Usar tema claro")
                             : QStringLiteral("◐  Usar tema escuro"));
  qInfo() << "QMoney: tema estrutural pronto";
  if (startServices) startBackend();
  qInfo() << "QMoney: backend solicitado";

  connect(&_updates, &UpdateManager::statusChanged, this, &MainWindow::setStatus);
  connect(&_updates, &UpdateManager::errorOccurred, this,
          [this](const QString& message, bool interactive) {
            _updateButton->setEnabled(true);
            _updateButton->setText(QStringLiteral("↻  Verificar atualização"));
            if (_repairInstall) {
              _repairInstall->setEnabled(true);
              _repairInstall->setText(QStringLiteral("Reparar instalação"));
            }
            if (interactive) QMessageBox::warning(
                this, _updates.isRepair() ? QStringLiteral("Reparo da instalação")
                                          : QStringLiteral("Atualizações"),
                message);
          });
  connect(&_updates, &UpdateManager::checkFinished, this,
          [this](bool available, bool interactive) {
            if (!_updates.isBusy()) _updateButton->setEnabled(true);
            if (_repairInstall && !_updates.isBusy()) {
              _repairInstall->setEnabled(true);
              _repairInstall->setText(QStringLiteral("Reparar instalação"));
            }
            if (!available) {
              _updateButton->setText(QStringLiteral("✓  QMoney %1").arg(QCoreApplication::applicationVersion()));
              if (interactive)
                QMessageBox::information(this, QStringLiteral("Atualizações"),
                                         QStringLiteral("Você já está usando a versão mais recente."));
            }
          });
  connect(&_updates, &UpdateManager::updateAvailable, this,
          [this](const QString& version, const QString& notes) {
            const bool repair = _updates.isRepair();
            _updateButton->setEnabled(true);
            _updateButton->setText(repair
                ? QStringLiteral("Reparando componentes…")
                : QStringLiteral("⬇  Instalar QMoney %1").arg(version));
            const QString safeNotes = notes.trimmed().isEmpty()
                                          ? QStringLiteral("Esta versão não possui notas adicionais.")
                                          : notes.trimmed();
            const auto answer = QMessageBox::question(
                this, repair ? QStringLiteral("Reparar QMoney")
                             : QStringLiteral("QMoney %1 disponível").arg(version),
                repair
                    ? QStringLiteral(
                          "O QMoney baixará novamente o pacote oficial assinado e "
                          "restaurará FFmpeg, FFprobe, navegador privado e o motor local.\n\n"
                          "Suas contas, credenciais e campanhas serão preservadas. Continuar?")
                    : QStringLiteral("Uma nova versão está pronta para baixar.\n\n%1\n\nInstalar agora?")
                          .arg(safeNotes));
            if (answer == QMessageBox::Yes) {
              _updateButton->setEnabled(false);
              if (_repairInstall) _repairInstall->setEnabled(false);
              _updateButton->setText(repair ? QStringLiteral("Baixando reparo…")
                                            : QStringLiteral("Baixando atualização…"));
              _updates.downloadAndInstall();
            } else if (repair) {
              _updateButton->setText(
                  QStringLiteral("✓  QMoney %1").arg(QCoreApplication::applicationVersion()));
              if (_repairInstall) {
                _repairInstall->setEnabled(true);
                _repairInstall->setText(QStringLiteral("Reparar instalação"));
              }
            }
          });
  connect(&_updates, &UpdateManager::progress, this,
          [this](qint64 received, qint64 total) {
            if (total > 0)
              _updateButton->setText(QStringLiteral("Baixando… %1%").arg(received * 100 / total));
            if (total > 0 && _repairInstall && _updates.isRepair())
              _repairInstall->setText(QStringLiteral("Baixando… %1%").arg(received * 100 / total));
          });
  connect(&_updates, &UpdateManager::installReady, this, &MainWindow::installUpdate);
  QTimer::singleShot(1800, this, [this] { checkForUpdates(false); });
}

MainWindow::~MainWindow() {
  _closing = true;
  stopBackend();
}

void MainWindow::navigate(int index) {
  if (index < 0) return;
  _pages->setCurrentIndex(index);
  onCampaignPageActivated(index);
  onLibraryPageActivated(index);
  onBalancesPageActivated(index);
  onMonitoringPageActivated(index);
  if (_backendReady) refreshCurrentPage();
}

void MainWindow::refreshCurrentPage() {
  if (!_backendReady) return probeBackend();
  switch (_pages->currentIndex()) {
    case 0: loadHome(); break;
    case 1: loadReadiness(); break;
    case 2: loadIntegrations(); break;
    case 3: loadCampaignData(); break;
    case 4: loadAccelerator(); loadNymeriaLibrary(); pollNymeriaJob(); break;
    case 5:
      refreshAccountsPage();
      break;
    case 6: loadBalances(); break;
    case 7: loadHistory(); break;
    case 8: loadBanned(); break;
    case 9: loadProviderCatalogStatus(); break;
    default: break;
  }
}

void MainWindow::showError(const QString& title, const QString& error) {
  QMessageBox::warning(this, title, error);
  setStatus(error);
}

void MainWindow::showAccountIssues(const QString& title, const QStringList& blockers,
                                  const QJsonArray& issues, std::function<void()> continueAction) {
  const QString checkedAt = QDateTime::currentDateTime().toString(QStringLiteral("dd/MM/yyyy HH:mm:ss"));
  QStringList sections;
  sections << QStringLiteral("Verificação em %1").arg(checkedAt);
  if (!blockers.isEmpty()) sections << blockers.join(QLatin1Char('\n'));
  for (const auto& value : issues) {
    const auto issue = value.toObject();
    const QString email = issue.value(QStringLiteral("email")).toString();
    sections << QStringLiteral("Conta: %1\nEtapa: %2\nMotivo: %3\nComo resolver: %4\nDetalhe: %5")
        .arg(email, issue.value(QStringLiteral("stage")).toString(),
             issue.value(QStringLiteral("reason")).toString(),
             issue.value(QStringLiteral("action")).toString(),
             issue.value(QStringLiteral("detail")).toString());
  }
  const QString report = sections.join(QStringLiteral("\n\n"));
  QDialog dialog(this);
  dialog.setWindowTitle(title);
  dialog.setSizeGripEnabled(true);
  const QSize available = screen()->availableGeometry().size();
  dialog.resize(qMin(820, available.width() - 40), qMin(540, available.height() - 80));
  auto* layout = new QVBoxLayout(&dialog);
  layout->setContentsMargins(22, 20, 22, 20);
  layout->setSpacing(14);
  auto* heading = new QLabel(title);
  heading->setWordWrap(true);
  auto titleFont = heading->font();
  titleFont.setPointSize(16);
  titleFont.setBold(true);
  heading->setFont(titleFont);
  layout->addWidget(heading);
  auto* explanation = new QLabel(QStringLiteral(
      "A verificação não foi concluída para os itens abaixo. "
      "Um token no prazo não garante acesso ao serviço. "
      "Falhas de rede não significam necessariamente senha incorreta."));
  explanation->setWordWrap(true);
  layout->addWidget(explanation);
  if (continueAction)
    explanation->setText(explanation->text() + QStringLiteral(
        "\nContinuar aplica somente as opções desta verificação às contas selecionadas e preserva os demais acessos."));
  bool continueRequested = false;
  auto* details = new QPlainTextEdit;
  details->setReadOnly(true);
  details->setLineWrapMode(QPlainTextEdit::WidgetWidth);
  details->setPlainText(report);
  layout->addWidget(details, 1);
  auto* buttons = new QDialogButtonBox;
  if (continueAction) {
    auto* remove = buttons->addButton(QStringLiteral("Continuar com contas aprovadas"), QDialogButtonBox::ActionRole);
    connect(remove, &QPushButton::clicked, &dialog, [&dialog, &continueRequested] {
      continueRequested = true;
      dialog.accept();
    });
  }
  auto* copy = buttons->addButton(QStringLiteral("Copiar diagnóstico"), QDialogButtonBox::ActionRole);
  auto* accounts = buttons->addButton(QStringLiteral("Abrir Contas"), QDialogButtonBox::ActionRole);
  auto* close = buttons->addButton(QStringLiteral("Fechar"), QDialogButtonBox::RejectRole);
  close->setDefault(true);
  connect(copy, &QPushButton::clicked, &dialog, [report] { QApplication::clipboard()->setText(report); });
  connect(accounts, &QPushButton::clicked, &dialog, [this, &dialog] {
    dialog.accept();
    navigate(5);
    navigate(5);
  });
  connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
  layout->addWidget(buttons);
  setStatus(title);
  dialog.exec();
  if (continueRequested) continueAction();
}

void MainWindow::setStatus(const QString& text) {
  const QString full = text.simplified();
  const QString compact = full.size() > 180 ? full.left(177) + QStringLiteral("…") : full;
  _status->setText(compact);
  _status->setToolTip(full == compact ? QString() : full);
}

QString MainWindow::usdMoney(qint64 cents) const {
  return QLocale(QStringLiteral("pt_BR")).toCurrencyString(cents / 100.0, QStringLiteral("US$"));
}

QString MainWindow::brlMoney(qint64 usdCents, double usdBrlRate) const {
  return QLocale(QStringLiteral("pt_BR")).toCurrencyString(
      (usdCents / 100.0) * usdBrlRate, QStringLiteral("R$"));
}

QString MainWindow::friendlyDate(const QString& iso) const {
  if (iso.isEmpty()) return QStringLiteral("—");
  QDateTime dt = QDateTime::fromString(iso, Qt::ISODate);
  if (!dt.isValid()) return iso;
  return QLocale(QStringLiteral("pt_BR")).toString(dt.toLocalTime(), QStringLiteral("dd/MM/yyyy HH:mm"));
}

QString MainWindow::encoded(const QString& value) const {
  return QString::fromLatin1(QUrl::toPercentEncoding(value));
}



bool MainWindow::isPageActive(int index) const {
  return _pages && _pages->currentIndex() == index;
}

void MainWindow::notifyPermanentCampaignAccountRemoval(const QString& email) {
  removePermanentlyDeletedAccountFromAccounts(email);
  removePermanentlyDeletedAccountFromBalances(email);
}
