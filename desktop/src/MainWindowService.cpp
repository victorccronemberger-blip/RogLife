#include "MainWindowSupport.hpp"
#include "MainWindow.hpp"

void MainWindow::initializeBackendProbe() {
  _backendProbe.setInterval(650);
  connect(&_backendProbe, &QTimer::timeout, this, &MainWindow::probeBackend);
}

void MainWindow::closeEvent(QCloseEvent* event) {
  if (!_shutdownReady && _backend.state() != QProcess::NotRunning && _backendReady) {
    event->ignore();
    pollLocalShutdown();
    return;
  }
  shutdownCampaignTimers();
  _closing = true;
  shutdownAccountTimers();
  shutdownBalanceTimers();
  shutdownLibraryTimers();
  shutdownMonitoringTimers();
  _backendProbe.stop();
  stopBackend();
  QMainWindow::closeEvent(event);
}

void MainWindow::applyStructuralStyle(bool dark) {
  const QString bg = dark ? QStringLiteral("#111315") : QStringLiteral("#f1f3f2");
  const QString panel = dark ? QStringLiteral("#1a1d1f") : QStringLiteral("#ffffff");
  const QString sidebar = QStringLiteral("#0e1011");
  const QString text = dark ? QStringLiteral("#f2f3f1") : QStringLiteral("#202426");
  const QString muted = dark ? QStringLiteral("#969da1") : QStringLiteral("#697277");
  const QString border = dark ? QStringLiteral("#2c3134") : QStringLiteral("#d8dddb");
  const QString selected = QStringLiteral("#35241c");
  const QString field = dark ? QStringLiteral("#15181a") : QStringLiteral("#f7f8f7");
  const QString soft = dark ? QStringLiteral("#202427") : QStringLiteral("#edf0ee");

  setStyleSheet(QStringLiteral(R"(
    * { font-family: "Inter", "Segoe UI"; }
    QPushButton { color: %4; background: %9; border: 1px solid %6; border-radius: 6px; padding: 5px 12px; font-weight: 600; }
    QPushButton:hover { border-color: #ff7a36; }
    QPushButton:pressed { background: %8; }
    QPushButton:focus { border-color: #ff7a36; }
    QPushButton[role="primary"] { color: #181a1b; background: #ff7a36; border-color: #ff7a36; }
    QPushButton[role="primary"]:hover { background: #ff934f; }
    QPushButton[role="primary"]:pressed { background: #e86b2b; }
    QPushButton:disabled, QPushButton[role="primary"]:disabled { color: %5; background: %8; border-color: %6; }
    QPushButton:flat { background: transparent; border-color: transparent; }
    QPushButton:flat:hover { color: #ff7a36; background: %9; }
    #workspace { background: %1; }
    #sidebar { background: %3; color: #f7f8f7; border-right: 1px solid #282c2e; }
    #brandMark { background: transparent; border: none; }
    #brand { color: #ffffff; font-size: 21px; font-weight: 750; letter-spacing: -0.3px; }
    #brandRole { color: #747c80; font-size: 9px; font-weight: 750; letter-spacing: 1.3px; }
    #navigationLabel { color: #666e72; font-size: 9px; font-weight: 800; letter-spacing: 1.2px; padding-left: 7px; padding-bottom: 4px; }
    #sidebar #quiet { color: #858d91; font-size: 9px; font-weight: 700; letter-spacing: 0.8px; }
    #navigation { background: transparent; color: #abb1b4; outline: none; border: none; }
    #navigation::item { border-radius: 7px; padding-left: 13px; margin: 2px 0; }
    #navigation::item:hover { background: #191c1e; color: #ffffff; }
    #navigation::item:selected { background: %7; color: #ffffff; font-weight: 650; border-left: 2px solid #ff7a36; }
    #connectionCard { background: #151819; border: 1px solid #292e30; border-radius: 8px; }
    #backendState { color: #dfe3e1; font-size: 12px; font-weight: 650; }
    #sidebarUtility { color: #bac0c2; background: #171a1c; border: 1px solid #292e30; border-radius: 7px; text-align: left; padding: 8px 12px; }
    #sidebarUtility:hover { color: white; background: #202427; border-color: #3a4144; }
    #pageContext { color: #ff7a36; font-size: 9px; font-weight: 850; letter-spacing: 1.45px; }
    #modeBadge { color: %5; background: %8; border: 1px solid %6; border-radius: 10px; padding: 4px 10px; font-size: 9px; font-weight: 750; letter-spacing: 0.8px; }
    #pageTitle { color: %4; font-size: 30px; font-weight: 760; letter-spacing: -0.6px; }
    #pageSubtitle { color: %5; font-size: 13px; }
    #card, #pulseCard, #metricCard { background: %2; border: 1px solid %6; border-radius: 9px; }
    #pulseCard { border-top: 2px solid #ff7a36; }
    #metricCard:hover { border-color: #ff7a36; }
    #cardTitle { color: %4; font-size: 13px; font-weight: 720; letter-spacing: 0.15px; }
    #metricCaption { color: %5; font-size: 9px; font-weight: 800; letter-spacing: 1.1px; }
    #metricValue { color: %4; font-family: "Cascadia Mono", Consolas; font-size: 32px; font-weight: 700; }
    #metricTiming { color: %5; font-size: 8px; font-weight: 700; letter-spacing: 0.9px; }
    #balanceTotalCaption { color: %5; font-size: 9px; font-weight: 800; letter-spacing: 1.05px; }
    #balanceTotalUsd { color: %4; font-family: "Cascadia Mono", Consolas; font-size: 23px; font-weight: 720; }
    #balanceTotalBrl { color: #ff7a36; font-family: "Cascadia Mono", Consolas; font-size: 12px; font-weight: 680; }
    #balanceExchange { color: %5; font-size: 10px; border-top: 1px solid %6; padding-top: 8px; }
    #balanceDivider { color: %6; }
    #pulseTitle { color: %4; font-size: 21px; font-weight: 735; letter-spacing: -0.2px; }
    #securityBadge { color: #61c694; background: %9; border: 1px solid %6; border-radius: 13px; padding: 8px 13px; font-size: 10px; font-weight: 700; }
    #integrationStatus { color: %4; font-size: 13px; font-weight: 700; }
    #integrationStatus[integrationState="ok"] { color: #48c78e; }
    #integrationStatus[integrationState="missing"] { color: #e3aa55; }
    #integrationMiniTitle { color: %4; font-size: 13px; font-weight: 720; }
    #campaignStage { color: %4; font-size: 17px; font-weight: 735; letter-spacing: -0.1px; }
    #campaignStats { color: %5; font-family: "Cascadia Mono", Consolas; font-size: 10px; font-weight: 650; }
    #kicker { color: #ff7a36; font-size: 9px; font-weight: 850; letter-spacing: 1.35px; }
    #signalRail { background: #111416; border: 1px solid #2d3336; border-radius: 7px; }
    #signalLabel { color: #ff7a36; font-size: 8px; font-weight: 850; letter-spacing: 1.1px; }
    #signalDot { color: #48c78e; font-size: 24px; }
    #signalPort { color: #8d9599; font-family: "Cascadia Mono", Consolas; font-size: 9px; font-weight: 650; }
    #sequenceRow { color: %4; border-bottom: 1px solid %6; padding-left: 4px; font-family: "Cascadia Mono", Consolas; font-size: 11px; }
    #quickAction { color: %4; background: %9; border: 1px solid %6; border-radius: 6px; text-align: left; padding: 8px 12px; font-weight: 620; }
    #quickAction:hover { border-color: #ff7a36; color: #ff7a36; }
    #quiet { color: %5; }
    #appStatusBar { background: %2; border-top: 1px solid %6; }
    #statusVersion { color: %5; font-family: "Cascadia Mono", Consolas; font-size: 9px; }
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QListWidget, QTableWidget {
      color: %4; background-color: %8; border: 1px solid %6; border-radius: 6px;
    }
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { min-height: 40px; padding-left: 12px; padding-right: 12px; }
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus, QListWidget:focus { border-color: #ff7a36; }
    QComboBox { combobox-popup: 0; padding-right: 44px; }
    QComboBox::drop-down { width: 40px; border-left: 1px solid %6; }
    QComboBox::down-arrow { image: url(:/qmoney/icons/chevron-down.svg); width: 14px; height: 14px; }
    QComboBox QAbstractItemView { color: %4; background-color: %2; border: 1px solid %6; border-radius: 0; selection-background-color: #d9652b; selection-color: white; padding: 0; }
    QComboBox QAbstractItemView::item { padding: 0 12px; }
    QListWidget::item { padding: 7px 9px; border-radius: 4px; }
    QListWidget::item:selected { background: #3b2920; color: white; }
    QTableWidget { gridline-color: %6; selection-background-color: #3b2920; selection-color: white; alternate-background-color: %9; }
    QTableWidget::item { padding: 8px 10px; border-bottom: 1px solid %6; }
    QHeaderView::section { color: %5; background: %2; border: none; border-bottom: 1px solid %6; padding: 9px 10px; font-size: 9px; font-weight: 800; }
    QTableCornerButton::section { background: %2; border: none; border-bottom: 1px solid %6; }
    QPlainTextEdit { font-family: "Cascadia Mono", Consolas, monospace; padding: 10px; }
    #campaignTimeline { font-family: "Inter", "Segoe UI"; font-size: 12px; line-height: 1.35; padding: 12px; }
    QProgressBar { min-height: 7px; max-height: 7px; background: %8; border: none; border-radius: 3px; }
    QProgressBar::chunk { background: #ff7a36; border-radius: 3px; }
    #campaignProgress, #bulkRegisterProgress { min-height: 22px; max-height: 22px; color: %4; text-align: center; font-family: "Cascadia Mono", Consolas; font-size: 9px; font-weight: 700; }
    QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
    QScrollBar::handle:vertical { background: %6; min-height: 30px; border-radius: 4px; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
    QScrollBar::handle:horizontal { background: %6; min-width: 30px; border-radius: 4px; }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
    QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
    #sidebar QScrollBar::handle:vertical { background: #3a4144; }
    QToolTip { color: #f6f7f6; background: #202426; border: 1px solid #3a4144; padding: 6px; }
  )").arg(bg, panel, sidebar, text, muted, border, selected, field, soft));
}

void MainWindow::setDarkTheme(bool dark) {
  QSettings().setValue(QStringLiteral("darkTheme"), dark);
  _style->setThemeJsonPath(dark ? QStringLiteral(":/qmoney/theme-dark.json")
                                : QStringLiteral(":/qmoney/theme-light.json"));
  applyStructuralStyle(dark);
  _themeButton->setText(dark ? QStringLiteral("☀  Usar tema claro")
                             : QStringLiteral("◐  Usar tema escuro"));
}

void MainWindow::checkForUpdates(bool interactive) {
  if (_updates.isBusy()) return;
  _updateButton->setEnabled(false);
  _updateButton->setText(QStringLiteral("Verificando…"));
  Q_UNUSED(interactive)
  _updateButton->setEnabled(false);
  _updateButton->setText(QStringLiteral("Edição local atualizada"));
  setStatus(QStringLiteral("Atualizações remotas desativadas nesta edição local."));
}

void MainWindow::installUpdate(const QString& packagePath, const QString& verifiedSha256) {
  const QString appDir = QCoreApplication::applicationDirPath();
  const QString updater = appDir + QStringLiteral("/QMoneyUpdater.exe");
  if (!QFileInfo::exists(updater)) {
    _updateButton->setEnabled(true);
    _updateButton->setText(QStringLiteral("↻  Verificar atualização"));
    return showError(QStringLiteral("Atualização"),
                     QStringLiteral("O componente QMoneyUpdater.exe não foi encontrado."));
  }
  const QStringList arguments = {
      QStringLiteral("--package"), packagePath,
      QStringLiteral("--sha256"), verifiedSha256,
      QStringLiteral("--target"), appDir,
      QStringLiteral("--pid"), QString::number(QCoreApplication::applicationPid()),
      QStringLiteral("--launch"), QStringLiteral("QMoney.exe")};
  if (!QProcess::startDetached(updater, arguments, appDir)) {
    _updateButton->setEnabled(true);
    return showError(QStringLiteral("Atualização"),
                     QStringLiteral("Não foi possível iniciar o instalador da atualização."));
  }
  setStatus(QStringLiteral("Fechando para instalar a atualização…"));
  QTimer::singleShot(150, qApp, &QCoreApplication::quit);
}

void MainWindow::startBackend() {
  ++_probeGeneration;
  _probeInFlight = false;
  const QString appDir = QCoreApplication::applicationDirPath();
  QString packagedService = provisionEmbeddedService();
  if (packagedService.isEmpty())
    packagedService = appDir + QStringLiteral("/runtime/QMoneyService.exe");
  QString program;
  QStringList arguments;
  QString workingDirectory;
  QProcessEnvironment environment = QProcessEnvironment::systemEnvironment();
  if (QFileInfo::exists(packagedService)) {
    program = packagedService;
    arguments = {QStringLiteral("--no-browser"), QStringLiteral("--porta"),
                 QStringLiteral("8876"), QStringLiteral("--parent-pid"),
                 QString::number(QCoreApplication::applicationPid())};
    // Prefere a biblioteca de mídia que acompanha a instalação. No layout de
    // desenvolvimento o executável vive em dist/QMoney e os dados ficam dois
    // níveis acima; numa distribuição portátil eles podem ficar ao lado do EXE.
    workingDirectory = QStandardPaths::writableLocation(QStandardPaths::GenericDataLocation)
                       + QStringLiteral("/QMoney");
    const QString savedLibrary = QSettings().value(QStringLiteral("libraryRoot")).toString();
    const QString libraryRoot = selectLibraryRoot(savedLibrary, appDir, workingDirectory);
    QDir().mkpath(workingDirectory);
    // Contas e segredos pertencem ao usuário Windows, nunca à biblioteca.
    environment = packagedServiceEnvironment(environment);
    environment.insert(QStringLiteral("QMONEY_USER_ROOT"), workingDirectory);
    environment.insert(QStringLiteral("AWS_SHARED_CREDENTIALS_FILE"), workingDirectory + QStringLiteral("/secrets/aws/credentials"));
    environment.insert(QStringLiteral("AWS_CONFIG_FILE"), workingDirectory + QStringLiteral("/secrets/aws/config"));
    environment.insert(QStringLiteral("AWS_EC2_METADATA_DISABLED"), QStringLiteral("true"));
    environment.insert(QStringLiteral("QMONEY_LIBRARY_ROOT"), libraryRoot);
    environment.insert(QStringLiteral("QMONEY_RUNTIME_ROOT"), appDir + QStringLiteral("/runtime"));
    environment.insert(QStringLiteral("QMONEY_APP_VERSION"),
                       QCoreApplication::applicationVersion());
    environment.insert(QStringLiteral("PLAYWRIGHT_BROWSERS_PATH"),
                       appDir + QStringLiteral("/runtime/ms-playwright"));
  } else {
    const QString root = resolveDevelopmentProjectRoot(
        QString::fromUtf8(QMONEY_PROJECT_ROOT), appDir);
    program = root + QStringLiteral("/.venv/Scripts/python.exe");
    if (!QFileInfo::exists(program)) program = QStringLiteral("python");
    arguments = {QStringLiteral("-m"), QStringLiteral("moneymin.web"),
                 QStringLiteral("--no-browser"), QStringLiteral("--porta"),
                 QStringLiteral("8876"), QStringLiteral("--parent-pid"),
                 QString::number(QCoreApplication::applicationPid())};
    workingDirectory = root;
    environment = packagedServiceEnvironment(environment);
    if (!environment.contains(QStringLiteral("QMONEY_USER_ROOT"))) {
      const QString userRoot = QStandardPaths::writableLocation(QStandardPaths::GenericDataLocation)
          + QStringLiteral("/QMoney");
      environment.insert(QStringLiteral("QMONEY_USER_ROOT"), userRoot);
    }
    if (!environment.contains(QStringLiteral("QMONEY_LIBRARY_ROOT"))) {
      environment.insert(QStringLiteral("QMONEY_LIBRARY_ROOT"), selectLibraryRoot(
          QSettings().value(QStringLiteral("libraryRoot")).toString(), appDir,
          environment.value(QStringLiteral("QMONEY_USER_ROOT"))));
    }
    environment.insert(QStringLiteral("QMONEY_PORTABLE_ROOT"), appDir);
    environment.insert(QStringLiteral("PLAYWRIGHT_BROWSERS_PATH"), root + QStringLiteral("/tools/ms-playwright"));
  }
  environment.insert(QStringLiteral("QMONEY_APP_VERSION"), QCoreApplication::applicationVersion());
  environment.insert(QStringLiteral("QMONEY_LOCAL_EDITION"), QStringLiteral("1"));
  const QString localApiToken = QUuid::createUuid().toString(QUuid::Id128)
                                + QUuid::createUuid().toString(QUuid::Id128);
  environment.insert(QStringLiteral("QMONEY_LOCAL_API_TOKEN"), localApiToken);
  _api.setSessionToken(localApiToken.toUtf8());
  _backend.setWorkingDirectory(workingDirectory);
  _backend.setProcessEnvironment(environment);
  _backend.setProcessChannelMode(QProcess::MergedChannels);
#ifdef Q_OS_WIN
  _backend.setCreateProcessArgumentsModifier([](QProcess::CreateProcessArguments* args) {
    args->flags |= CREATE_NO_WINDOW;
  });
#endif
  disconnect(&_backend, nullptr, this, nullptr);
  drainBackendOutput();
  connect(&_backend, &QProcess::errorOccurred, this, [this](QProcess::ProcessError) {
    if (!_backendReady) setStatus(QStringLiteral("O motor local não pôde ser iniciado."));
  });
  connect(&_backend, qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this,
          [this](int, QProcess::ExitStatus) {
    if (_closing || _restartingBackend) return;
    setBackendReady(false, QStringLiteral("Reiniciando motor…"));
    if (_backendRestarts++ < 3)
      QTimer::singleShot(1200, this, &MainWindow::startBackend);
    else
      setStatus(QStringLiteral("O motor parou repetidamente. Exporte o diagnóstico para suporte."));
  });
  _backend.start(program, arguments);
  _probeAttempts = 0;
  _backendProbe.start();
  QTimer::singleShot(60, this, &MainWindow::probeBackend);
}

void MainWindow::drainBackendOutput() {
  connect(&_backend, &QProcess::readyReadStandardOutput, this, [this] {
    // Local subprocess logs can arrive in arbitrary fragments. Keep them out
    // of the user-facing status line and consume each chunk as it arrives.
    _backend.readAllStandardOutput();
  });
}

void MainWindow::stopBackend() {
  _backendProbe.stop();
  ++_probeGeneration;
  _probeInFlight = false;
  if (_backend.state() != QProcess::NotRunning) {
    terminateOwnedServiceTree(_backend.processId(), _backend.program());
    _backend.terminate();
    if (!_backend.waitForFinished(1800)) {
      _backend.kill();
      _backend.waitForFinished(1000);
    }
  }
}

void MainWindow::restartBackend() {
  setBackendReady(false, QStringLiteral("Reconectando…"));
  _backendRestarts = 0;
  _restartingBackend = true;
  stopBackend();
  QTimer::singleShot(350, this, [this] {
    _restartingBackend = false;
    startBackend();
  });
}

void MainWindow::probeBackend() {
  if (_closing || _restartingBackend || _backendReady || _probeInFlight) return;
  _probeInFlight = true;
  const int generation = _probeGeneration;
  ++_probeAttempts;
  _api.get(QStringLiteral("/api/health"),
           [this, generation](bool ok, const QJsonDocument& document, const QString&) {
    if (generation != _probeGeneration || _closing) return;
    _probeInFlight = false;
    const QString serviceVersion = document.object()
                                       .value(QStringLiteral("service"))
                                       .toObject()
                                       .value(QStringLiteral("app_version"))
                                       .toString();
    const bool compatible = ok && serviceVersion == QCoreApplication::applicationVersion();
    if (compatible) {
      _backendProbe.stop();
      _backendRestarts = 0;
      setBackendReady(true);
      _api.get(QStringLiteral("/api/preferences"), [this, generation](bool loaded, const QJsonDocument& prefs, const QString&) {
        if (!loaded || _closing || generation != _probeGeneration) return;
        if (prefs.object().value(QStringLiteral("holoassist_enabled")) != QJsonValue(false)) return;
        disableHoloAssistCampaignSources();
      });
      refreshCurrentPage();
      if (!_runtimeChecked && _backend.program().endsWith(QStringLiteral("QMoneyService.exe"), Qt::CaseInsensitive)) {
        _runtimeChecked = true;
        _api.get(QStringLiteral("/api/runtime"),
                 [this, generation](bool checked, const QJsonDocument& result, const QString&) {
          if (!checked || _closing || generation != _probeGeneration) return;
          if (!result.object().value(QStringLiteral("ready")).toBool() && !_updates.isBusy()) {
            setStatus(QStringLiteral("Componentes ausentes detectados. Preparando reparo da instalação…"));
            restartBackend();
          }
        });
      }
    } else if (_probeAttempts > 22) {
      _backendProbe.stop();
      setBackendReady(false, QStringLiteral("Serviço indisponível"));
    } else if (ok && !serviceVersion.isEmpty()) {
      setBackendReady(false, QStringLiteral("Versão do motor incompatível"));
    }
  });
}

void MainWindow::setBackendReady(bool ready, const QString& message) {
  const bool generationChanged = _backendReady != ready;
  if (generationChanged) ++_operationBackendGeneration;
  _backendReady = ready;

  onAccountsBackendAvailabilityChanged(ready, generationChanged);
  onCampaignBackendAvailabilityChanged(ready, generationChanged);
  onProviderCatalogBackendAvailabilityChanged(ready, generationChanged);
  onLibraryBackendAvailabilityChanged(ready, generationChanged);

  _backendState->setText(ready ? QStringLiteral("●  Conectado em 8876")
                               : QStringLiteral("●  %1").arg(message));
  _backendState->setStyleSheet(ready ? QStringLiteral("color:#61c694")
                                     : QStringLiteral("color:#e66c76"));
  if (!ready) return;

  setStatus(QStringLiteral("Serviço local pronto."));
  // The Release package does not distribute maintenance scripts. Once the
  // service is ready, the UI checks and prepares Ego4D when credentials exist.
  loadIntegrations();
  const QString healthPath = qApp->property("updateHealthPath").toString();
  if (!healthPath.isEmpty()) {
    QSaveFile marker(healthPath);
    if (marker.open(QIODevice::WriteOnly)) {
      marker.write("ok\n");
      marker.commit();
    }
    qApp->setProperty("updateHealthPath", QString());
  }
}

void MainWindow::pollLocalShutdown() {
  if (_shutdownPolling) return;
  _shutdownPolling = true;
  setStatus(QStringLiteral("Encerrando operações e preservando os registros…"));
  setEnabled(false);
  _api.post(QStringLiteral("/api/local/shutdown"), {},
      [this](bool ok, const QJsonDocument& doc, const QString& error) {
        _shutdownPolling = false;
        if (!ok) {
          setEnabled(true);
          return showError(QStringLiteral("Encerramento não concluído"), error);
        }
        if (doc.object().value(QStringLiteral("ready")) == QJsonValue(true)) {
          _shutdownReady = true;
          close();
        } else QTimer::singleShot(700, this, &MainWindow::pollLocalShutdown);
      });
}

void MainWindow::showRecovery() {
  _api.get(QStringLiteral("/api/recovery?async=1"),
      [this](bool ok, const QJsonDocument& doc, const QString& error) {
        if (!ok) return showError(QStringLiteral("Recuperação"), error);
        QDialog dialog(this);
        dialog.setWindowTitle(QStringLiteral("Envios preservados"));
        dialog.resize(820, 480);
        auto* layout = new QVBoxLayout(&dialog);
        auto* list = new QListWidget(&dialog);
        for (const auto value : doc.object().value(QStringLiteral("items")).toArray()) {
          const auto row = value.toObject();
          auto* item = new QListWidgetItem(QStringLiteral("%1 · %2 · %3")
              .arg(row.value(QStringLiteral("email")).toString(),
                   row.value(QStringLiteral("session_id")).toString(),
                   row.value(QStringLiteral("status")).toString()), list);
          item->setData(Qt::UserRole, row.value(QStringLiteral("email")));
          item->setData(Qt::UserRole + 1, row.value(QStringLiteral("can_resume")).toBool());
          item->setData(Qt::UserRole + 2, row.value(QStringLiteral("session_id")).toString());
        }
        layout->addWidget(list);
        auto* info = new QLabel(QStringLiteral("Sessões inconclusivas ficam preservadas para revisão. A retomada usa somente sessões autorizadas da conta escolhida."));
        info->setWordWrap(true);
        layout->addWidget(info);
        auto* buttons = new QDialogButtonBox(QDialogButtonBox::Close);
        auto* resume = buttons->addButton(QStringLiteral("Retomar conta selecionada"), QDialogButtonBox::ActionRole);
        resume->setEnabled(false);
        connect(list, &QListWidget::currentItemChanged, &dialog, [resume](QListWidgetItem* item) {
          resume->setEnabled(item && item->data(Qt::UserRole + 1).toBool());
        });
        connect(resume, &QPushButton::clicked, &dialog, [this, &dialog, list] {
          const auto* item = list->currentItem();
          if (!item || !item->data(Qt::UserRole + 1).toBool()) return;
          const QString email = item->data(Qt::UserRole).toString();
          const QString sessionId = item->data(Qt::UserRole + 2).toString();
          if (sessionId.isEmpty()) return showError(QStringLiteral("Recuperação não iniciada"),
              QStringLiteral("A sessão selecionada não tem um identificador válido. Atualize as pendências antes de retomar."));
          if (QMessageBox::question(&dialog, QStringLiteral("Confirmar retomada"),
              QStringLiteral("Retomar somente a sessão %1 da conta %2?").arg(sessionId, email),
              QMessageBox::Yes | QMessageBox::No, QMessageBox::No) != QMessageBox::Yes) return;
          _api.post(QStringLiteral("/api/recovery/resume"),
              {{QStringLiteral("email"), email}, {QStringLiteral("session_id"), sessionId},
               {QStringLiteral("confirmed"), true}},
              [this](bool resumed, const QJsonDocument&, const QString& issue) {
                if (!resumed) showError(QStringLiteral("Recuperação não iniciada"), issue);
                else setStatus(QStringLiteral("Retomada iniciada; consulte os recibos antes de criar outro envio."));
              });
          dialog.accept();
        });
        connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
        layout->addWidget(buttons);
        dialog.exec();
      });
}

