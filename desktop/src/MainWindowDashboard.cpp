#include "MainWindowSupport.hpp"
#include "MainWindow.hpp"

void MainWindow::loadHome() {
  _api.get(QStringLiteral("/api/accounts"), [this](bool ok, const QJsonDocument& doc, const QString& error) {
    if (!ok) return setStatus(error);
    _homeAccounts->setText(QString::number(doc.object().value(QStringLiteral("accounts")).toArray().size()));
  });
  _api.get(QStringLiteral("/api/logs"), [this](bool ok, const QJsonDocument& doc, const QString& error) {
    if (!ok) return setStatus(error);
    const auto logs = doc.object().value(QStringLiteral("logs")).toArray();
    _homeCampaigns->setText(QString::number(logs.size()));
    if (logs.isEmpty()) {
      _homeSuccess->setText(QStringLiteral("—"));
      _homePulseBody->setText(QStringLiteral("Ainda não há campanhas. Configure a primeira quando estiver pronto."));
    } else {
      const auto last = logs.first().toObject();
      _homeSuccess->setText(QStringLiteral("%1/%2")
          .arg(last.value(QStringLiteral("ok")).toInt())
          .arg(last.value(QStringLiteral("sends")).toInt()));
      _homePulseBody->setText(QStringLiteral("Última execução: %1 · %2 clipe(s) · %3 conta(s)")
          .arg(friendlyDate(last.value(QStringLiteral("started_at")).toString()))
          .arg(last.value(QStringLiteral("items")).toInt())
          .arg(last.value(QStringLiteral("accounts")).toArray().size()));
    }
  });
  _api.get(QStringLiteral("/api/campaigns/current"),
           [this](bool ok, const QJsonDocument& doc, const QString&) {
    if (!ok) return;
    const auto snap = doc.object();
    const QString state = snap.value(QStringLiteral("state")).toString();
    const auto totals = snap.value(QStringLiteral("totals")).toObject();
    const int total = totals.value(QStringLiteral("total_sends")).toInt();
    const int done = totals.value(QStringLiteral("done_sends")).toInt();
    const bool running = state == QStringLiteral("running") || state == QStringLiteral("stopping");
    _homePulseTitle->setText(running ? QStringLiteral("Campanha em movimento")
                                     : QStringLiteral("Operação pronta para a próxima campanha"));
    if (running) _homePulseBody->setText(snap.value(QStringLiteral("current")).toString());
    _homePulseProgress->setValue(total > 0 ? done * 100 / total : 0);
  });
}

void MainWindow::loadReadiness() {
  _readinessRefresh->setEnabled(false);
  _readinessRefresh->setText(QStringLiteral("Verificando…"));
  const QString provider = selectedCampaignDataset();
  _api.get(QStringLiteral("/api/readiness?dataset=%1").arg(encoded(provider)),
           [this](bool ok, const QJsonDocument& doc, const QString& error) {
    _readinessRefresh->setEnabled(true);
    _readinessRefresh->setText(QStringLiteral("Executar verificação"));
    if (!ok) return showError(QStringLiteral("Prontidão indisponível"), error);
    const auto root = doc.object();
    const auto checks = root.value(QStringLiteral("checks")).toArray();
    int passed = 0;
    _readinessTable->setRowCount(checks.size());
    for (int row = 0; row < checks.size(); ++row) {
      const auto check = checks[row].toObject();
      const QString status = check.value(QStringLiteral("status")).toString();
      QString label;
      if (status == QStringLiteral("ok")) { label = QStringLiteral("● PRONTO"); ++passed; }
      else if (status == QStringLiteral("warning")) label = QStringLiteral("● ATENÇÃO");
      else label = QStringLiteral("● BLOQUEIO");
      auto* state = cell(label);
      state->setForeground(status == QStringLiteral("ok") ? QColor(QStringLiteral("#48c78e"))
                           : status == QStringLiteral("warning") ? QColor(QStringLiteral("#e3aa55"))
                                                                  : QColor(QStringLiteral("#e66c76")));
      _readinessTable->setItem(row, 0, state);
      _readinessTable->setItem(row, 1, cell(check.value(QStringLiteral("name")).toString()));
      _readinessTable->setItem(row, 2, cell(check.value(QStringLiteral("detail")).toString()));
    }
    const int percent = checks.isEmpty() ? 0 : passed * 100 / checks.size();
    _readinessProgress->setValue(percent);
    const bool ready = root.value(QStringLiteral("ready")).toBool();
    _readinessHeadline->setText(ready ? QStringLiteral("Operação liberada")
                                      : QStringLiteral("Ação necessária antes da campanha"));
    _readinessSummary->setText(QStringLiteral("%1 de %2 verificações prontas · origem %3")
                                   .arg(passed).arg(checks.size())
                                   .arg(root.value(QStringLiteral("provider")).toString()));
    setStatus(ready ? QStringLiteral("Ambiente pronto para campanhas.")
                    : QStringLiteral("A prontidão encontrou bloqueios; consulte a matriz."));
  });

  _api.get(QStringLiteral("/api/storage/library"),
           [this](bool ok, const QJsonDocument& doc, const QString& error) {
    if (!ok) { _libraryUsage->setText(error); return; }
    const auto storage = doc.object();
    _currentLibraryRoot = storage.value(QStringLiteral("root")).toString();
    _libraryPath->setText(_currentLibraryRoot);
    _libraryUsage->setText(
        QStringLiteral("Biblioteca %1 · Ego4D %2 (%3 arquivos) · HoloAssist %4 (%5 arquivos) · livres %6")
            .arg(bytesText(static_cast<qint64>(storage.value(QStringLiteral("data_bytes")).toDouble())))
            .arg(bytesText(static_cast<qint64>(storage.value(QStringLiteral("ego4d_bytes")).toDouble())))
            .arg(storage.value(QStringLiteral("ego4d_files")).toInt())
            .arg(bytesText(static_cast<qint64>(storage.value(QStringLiteral("holoassist_bytes")).toDouble())))
            .arg(storage.value(QStringLiteral("holoassist_files")).toInt())
            .arg(bytesText(static_cast<qint64>(storage.value(QStringLiteral("free_bytes")).toDouble()))));
  });
}

QWidget* MainWindow::buildHomePage() {
  auto* body = new QWidget;
  auto* layout = new QVBoxLayout(body);
  layout->setContentsMargins(0, 0, 0, 0);
  layout->setSpacing(14);

  auto* pulseContent = new QWidget;
  auto* pulse = new QHBoxLayout(pulseContent);
  pulse->setContentsMargins(0, 0, 0, 0);
  pulse->setSpacing(22);

  auto* signal = new QFrame;
  signal->setObjectName(QStringLiteral("signalRail"));
  signal->setFixedWidth(104);
  auto* signalLayout = new QVBoxLayout(signal);
  signalLayout->setContentsMargins(12, 12, 12, 12);
  signalLayout->setSpacing(2);
  auto* signalTop = new QLabel(QStringLiteral("AO VIVO"));
  signalTop->setObjectName(QStringLiteral("signalLabel"));
  signalTop->setAlignment(Qt::AlignCenter);
  auto* signalDot = new QLabel(QStringLiteral("●"));
  signalDot->setObjectName(QStringLiteral("signalDot"));
  signalDot->setAlignment(Qt::AlignCenter);
  auto* signalPort = new QLabel(QStringLiteral("MOTOR\n8876"));
  signalPort->setObjectName(QStringLiteral("signalPort"));
  signalPort->setAlignment(Qt::AlignCenter);
  signalLayout->addWidget(signalTop);
  signalLayout->addStretch();
  signalLayout->addWidget(signalDot);
  signalLayout->addStretch();
  signalLayout->addWidget(signalPort);
  pulse->addWidget(signal);

  auto* pulseCopy = new QWidget;
  auto* pulseCopyLayout = new QVBoxLayout(pulseCopy);
  pulseCopyLayout->setContentsMargins(0, 4, 0, 4);
  pulseCopyLayout->setSpacing(8);
  auto* pulseHeader = new QHBoxLayout;
  auto* pulseKicker = new QLabel(QStringLiteral("PULSO DE CAMPANHA"));
  pulseKicker->setObjectName(QStringLiteral("kicker"));
  pulseHeader->addWidget(pulseKicker);
  pulseCopyLayout->addLayout(pulseHeader);
  _homePulseTitle = new QLabel(QStringLiteral("Aguardando o serviço local"));
  _homePulseTitle->setObjectName(QStringLiteral("pulseTitle"));
  pulseCopyLayout->addWidget(_homePulseTitle);
  _homePulseBody = quietLabel(QStringLiteral("Os dados aparecerão assim que o motor responder."));
  pulseCopyLayout->addWidget(_homePulseBody);
  pulseCopyLayout->addSpacing(2);
  _homePulseProgress = new QProgressBar;
  _homePulseProgress->setRange(0, 100);
  _homePulseProgress->setValue(0);
  _homePulseProgress->setTextVisible(false);
  pulseCopyLayout->addWidget(_homePulseProgress);
  pulse->addWidget(pulseCopy, 1);

  auto* newCampaign = primaryButton(QStringLiteral("Criar campanha  →"));
  newCampaign->setMinimumWidth(158);
  connect(newCampaign, &QPushButton::clicked, this, [this] { navigate(3); });
  pulse->addWidget(newCampaign, 0, Qt::AlignVCenter);
  auto* pulseCard = card(QString(), pulseContent);
  pulseCard->setObjectName(QStringLiteral("pulseCard"));
  layout->addWidget(pulseCard);

  auto* stats = new QWidget;
  auto* statsLayout = new QHBoxLayout(stats);
  statsLayout->setContentsMargins(0, 0, 0, 0);
  statsLayout->setSpacing(14);
  auto* accountsMetric = card(QString(), metric(QStringLiteral("—"), QStringLiteral("contas conectadas"), &_homeAccounts));
  accountsMetric->setObjectName(QStringLiteral("metricCard"));
  auto* campaignsMetric = card(QString(), metric(QStringLiteral("—"), QStringLiteral("campanhas registradas"), &_homeCampaigns));
  campaignsMetric->setObjectName(QStringLiteral("metricCard"));
  auto* successMetric = card(QString(), metric(QStringLiteral("—"), QStringLiteral("envios ok na última"), &_homeSuccess));
  successMetric->setObjectName(QStringLiteral("metricCard"));
  statsLayout->addWidget(accountsMetric);
  statsLayout->addWidget(campaignsMetric);
  statsLayout->addWidget(successMetric);
  layout->addWidget(stats);

  auto* lower = new QWidget;
  auto* lowerLayout = new QHBoxLayout(lower);
  lowerLayout->setContentsMargins(0, 0, 0, 0);
  lowerLayout->setSpacing(14);

  auto* sequence = new QWidget;
  auto* sequenceLayout = new QVBoxLayout(sequence);
  sequenceLayout->setContentsMargins(0, 0, 0, 0);
  sequenceLayout->setSpacing(0);
  const QStringList steps = {
      QStringLiteral("01   Conecte e valide as contas de destino"),
      QStringLiteral("02   Prepare o reservatório de mídia"),
      QStringLiteral("03   Inicie uma campanha monitorada")};
  for (const QString& step : steps) {
    auto* row = new QLabel(step);
    row->setObjectName(QStringLiteral("sequenceRow"));
    row->setMinimumHeight(42);
    sequenceLayout->addWidget(row);
  }
  lowerLayout->addWidget(card(QStringLiteral("Próxima sequência"), sequence), 3);

  auto* quick = new QWidget;
  auto* quickLayout = new QVBoxLayout(quick);
  quickLayout->setContentsMargins(0, 0, 0, 0);
  quickLayout->setSpacing(8);
  auto addQuick = [this, quickLayout](const QString& text, int destination) {
    auto* button = new QPushButton(text + QStringLiteral("   →"));
    button->setObjectName(QStringLiteral("quickAction"));
    button->setCursor(Qt::PointingHandCursor);
    connect(button, &QPushButton::clicked, this,
            [this, destination] { navigate(destination); });
    quickLayout->addWidget(button);
  };
  addQuick(QStringLiteral("Checar prontidão"), 1);
  addQuick(QStringLiteral("Configurar integrações"), 2);
  addQuick(QStringLiteral("Gerenciar contas"), 5);
  addQuick(QStringLiteral("Consultar histórico"), 7);
  lowerLayout->addWidget(card(QStringLiteral("Acesso direto"), quick), 2);
  layout->addWidget(lower);
  layout->addStretch();

  return pageShell(QStringLiteral("Visão geral"),
                   QStringLiteral("Acompanhe o estado da operação e siga para a próxima ação."), body);
}

QWidget* MainWindow::buildReadinessPage() {
  auto* body = new QWidget;
  auto* layout = new QVBoxLayout(body);
  layout->setContentsMargins(0, 0, 0, 0);
  layout->setSpacing(14);

  auto* command = new QWidget;
  auto* commandLayout = new QHBoxLayout(command);
  commandLayout->setContentsMargins(0, 0, 0, 0);
  commandLayout->setSpacing(18);
  auto* copy = new QVBoxLayout;
  copy->setSpacing(6);
  auto* kicker = new QLabel(QStringLiteral("LIBERAÇÃO OPERACIONAL"));
  kicker->setObjectName(QStringLiteral("kicker"));
  copy->addWidget(kicker);
  _readinessHeadline = new QLabel(QStringLiteral("Medindo o ambiente…"));
  _readinessHeadline->setObjectName(QStringLiteral("pulseTitle"));
  copy->addWidget(_readinessHeadline);
  _readinessSummary = quietLabel(
      QStringLiteral("O QMoney validará contas, ferramentas, catálogos e armazenamento."));
  copy->addWidget(_readinessSummary);
  _readinessProgress = new QProgressBar;
  _readinessProgress->setRange(0, 100);
  _readinessProgress->setTextVisible(false);
  copy->addWidget(_readinessProgress);
  commandLayout->addLayout(copy, 1);
  auto* commandActions = new QVBoxLayout;
  _readinessRefresh = primaryButton(QStringLiteral("Executar verificação"));
  connect(_readinessRefresh, &QPushButton::clicked, this, &MainWindow::loadReadiness);
  commandActions->addWidget(_readinessRefresh);
  _diagnosticsExport = new QPushButton(QStringLiteral("Exportar diagnóstico"));
  connect(_diagnosticsExport, &QPushButton::clicked, this, &MainWindow::exportDiagnostics);
  commandActions->addWidget(_diagnosticsExport);
  commandActions->addStretch();
  commandLayout->addLayout(commandActions);
  auto* commandCard = card(QString(), command);
  commandCard->setObjectName(QStringLiteral("pulseCard"));
  layout->addWidget(commandCard);

  _readinessTable = new QTableWidget(0, 3);
  configureTable(_readinessTable);
  _readinessTable->setHorizontalHeaderLabels(
      {QStringLiteral("Estado"), QStringLiteral("Componente"), QStringLiteral("Leitura")});
  _readinessTable->horizontalHeader()->setSectionResizeMode(0, QHeaderView::Fixed);
  _readinessTable->horizontalHeader()->setSectionResizeMode(1, QHeaderView::Interactive);
  _readinessTable->horizontalHeader()->setSectionResizeMode(2, QHeaderView::Stretch);
  _readinessTable->setColumnWidth(0, 118);
  _readinessTable->setColumnWidth(1, 230);
  layout->addWidget(card(QStringLiteral("Matriz de prontidão"), _readinessTable), 1);

  auto* library = new QWidget;
  auto* libraryLayout = new QHBoxLayout(library);
  libraryLayout->setContentsMargins(0, 0, 0, 0);
  libraryLayout->setSpacing(14);
  auto* libraryCopy = new QVBoxLayout;
  _libraryPath = new QLabel(QStringLiteral("Localizando biblioteca…"));
  _libraryPath->setObjectName(QStringLiteral("libraryPath"));
  _libraryPath->setTextInteractionFlags(Qt::TextSelectableByMouse);
  libraryCopy->addWidget(_libraryPath);
  _libraryUsage = quietLabel(QStringLiteral("Medindo uso e espaço livre."));
  libraryCopy->addWidget(_libraryUsage);
  libraryLayout->addLayout(libraryCopy, 1);
  auto* openLibrary = new QPushButton(QStringLiteral("Abrir pasta"));
  connect(openLibrary, &QPushButton::clicked, this, [this] {
    if (!_currentLibraryRoot.isEmpty())
      QDesktopServices::openUrl(QUrl::fromLocalFile(_currentLibraryRoot));
  });
  libraryLayout->addWidget(openLibrary);
  _libraryChoose = primaryButton(QStringLiteral("Escolher biblioteca"));
  connect(_libraryChoose, &QPushButton::clicked, this, &MainWindow::chooseLibrary);
  libraryLayout->addWidget(_libraryChoose);
  layout->addWidget(card(QStringLiteral("Biblioteca de mídia"), library));

  return pageShell(QStringLiteral("Prontidão"),
                   QStringLiteral("Confirme cada dependência antes de liberar uma campanha."), body);
}

void MainWindow::chooseLibrary() {
  const QString selected = QFileDialog::getExistingDirectory(
      this, QStringLiteral("Escolher raiz da biblioteca QMoney"), _currentLibraryRoot);
  if (selected.isEmpty()) return;
  if (!QFileInfo(selected).isDir())
    return showError(QStringLiteral("Biblioteca indisponível"),
                     QStringLiteral("Escolha uma pasta existente para a biblioteca local."));
  QSettings().setValue(QStringLiteral("libraryRoot"), QDir::cleanPath(selected));
  setStatus(QStringLiteral("Reiniciando o motor com a biblioteca selecionada…"));
  restartBackend();
}

void MainWindow::exportDiagnostics() {
  const QString suggested = QDir::homePath() + QStringLiteral("/QMoney-diagnostico-%1.json")
      .arg(QDateTime::currentDateTime().toString(QStringLiteral("yyyyMMdd-HHmmss")));
  const QString path = QFileDialog::getSaveFileName(
      this, QStringLiteral("Exportar diagnóstico sanitizado"), suggested,
      QStringLiteral("Relatório JSON (*.json)"));
  if (path.isEmpty()) return;
  _diagnosticsExport->setEnabled(false);
  _api.get(QStringLiteral("/api/diagnostics"),
           [this, path](bool ok, const QJsonDocument& doc, const QString& error) {
    _diagnosticsExport->setEnabled(true);
    if (!ok) return showError(QStringLiteral("Diagnóstico não exportado"), error);
    QSaveFile output(path);
    if (!output.open(QIODevice::WriteOnly) || output.write(doc.toJson(QJsonDocument::Indented)) < 0
        || !output.commit()) {
      return showError(QStringLiteral("Diagnóstico não exportado"),
                       QStringLiteral("Não foi possível gravar o arquivo escolhido."));
    }
    setStatus(QStringLiteral("Diagnóstico sanitizado exportado com sucesso."));
  });
}

