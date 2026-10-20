#include "MainWindowSupport.hpp"
#include "MainWindow.hpp"

void MainWindow::loadIntegrations() {
  loadIntegrationsSnapshot(false);
}

void MainWindow::refreshIntegrationsLive() {
  if (_closing || !_backendReady || !isVisible() || !isPageActive(2)) return;
  loadIntegrationsSnapshot(true);
}

void MainWindow::loadIntegrationsSnapshot(bool passive) {
  if (_closing) return;
  if (_integrationsReadPending && _integrationsReadGeneration == _operationBackendGeneration) {
    if (!passive) _integrationsReadQueued = true;
    return;
  }
  _integrationsReadPending = true;
  _integrationsReadQueued = false;
  const quint64 revision = ++_integrationsReadRevision;
  const int generation = _integrationsReadGeneration = _operationBackendGeneration;
  const quint64 pageRevision = _livePageRevision;
  _api.get(QStringLiteral("/api/integrations"),
           [this, passive, revision, generation, pageRevision](bool ok, const QJsonDocument& doc, const QString& error) {
    if (revision != _integrationsReadRevision) return;
    _integrationsReadPending = false;
    if (_closing || generation != _operationBackendGeneration) return;
    if (_integrationsReadQueued) {
      _integrationsReadQueued = false;
      QTimer::singleShot(0, this, [this] { loadIntegrationsSnapshot(false); });
    }
    if (passive && (pageRevision != _livePageRevision || !_backendReady || !isVisible() || !isPageActive(2)
        || qApp->activeModalWidget() || qApp->activePopupWidget())) return;
    const bool validSnapshot = doc.object().value(QStringLiteral("ego4d")).isObject()
        && doc.object().value(QStringLiteral("hostinger")).isObject();
    if (passive && !validSnapshot) ok = false;
    const QString failure = error.isEmpty() ? QStringLiteral("O serviço retornou um estado de integrações inválido.") : error;
    recordLiveDataResult(2, ok, failure);
    if (!ok) {
      _integrationsRenderedSignature.clear();
      if (passive) {
        _integrationsSummary->setText(QStringLiteral("Leitura indisponível; dados anteriores preservados. ") + failure);
        return;
      }
      return showError(QStringLiteral("Integrações indisponíveis"), failure);
    }
    const auto signature = doc.toJson(QJsonDocument::Compact);
    if (passive && signature == _integrationsRenderedSignature) return;
    _integrationsRenderedSignature = signature;
    const auto root = doc.object();
    const auto ego = root.value(QStringLiteral("ego4d")).toObject();
    const auto host = root.value(QStringLiteral("hostinger")).toObject();
    const auto holo = root.value(QStringLiteral("holoassist")).toObject();
    const auto runtime = root.value(QStringLiteral("runtime")).toObject();
    const auto security = root.value(QStringLiteral("security")).toObject();
    renderAppCheckSnapshot(root.value(QStringLiteral("appcheck")).toObject());
    const bool egoConfigured = ego.value(QStringLiteral("configured")).toBool();
  const bool egoCatalog = ego.value(QStringLiteral("catalog_ready")).toBool()
      && ego.value(QStringLiteral("annotations_ready")).toBool();
    const bool hostConfigured = host.value(QStringLiteral("configured")).toBool();
    const bool holoReady = holo.value(QStringLiteral("catalog_ready")).toBool()
                        && holo.value(QStringLiteral("indexes_ready")).toBool();
    const bool runtimeReady = runtime.value(QStringLiteral("ffmpeg_ready")).toBool()
                           && runtime.value(QStringLiteral("ffprobe_ready")).toBool()
                           && runtime.value(QStringLiteral("browser_ready")).toBool();
    const int readyCount = int(egoConfigured) + int(egoCatalog)
                         + int(hostConfigured) + int(holoReady) + int(runtimeReady);
    _integrationsHeadline->setText(readyCount == 5
        ? QStringLiteral("Todas as conexões estão prontas")
        : QStringLiteral("%1 de 5 componentes configurados").arg(readyCount));
    _integrationsSummary->setText(readyCount == 5
        ? QStringLiteral("Credenciais, catálogos e ferramentas disponíveis.")
        : QStringLiteral("Há componentes pendentes."));
    _integrationSecurity->setText(QStringLiteral("🔒  %1")
        .arg(security.value(QStringLiteral("provider")).toString(
            QStringLiteral("Proteção do Windows"))));
    _integrationSecurity->setToolTip(
        security.value(QStringLiteral("detail")).toString());

    const QString hint = ego.value(QStringLiteral("access_hint")).toString();
    _ego4dStatus->setText(egoConfigured
        ? QStringLiteral("● Credencial protegida %1").arg(hint)
        : QStringLiteral("● Credencial ainda não configurada"));
    _ego4dStatus->setProperty("integrationState", egoConfigured ? "ok" : "missing");
    _ego4dStatus->style()->unpolish(_ego4dStatus);
    _ego4dStatus->style()->polish(_ego4dStatus);
    _ego4dCatalog->setText(egoCatalog
        ? QStringLiteral("✓ Catálogo instalado")
        : QStringLiteral("! Catálogo pendente"));
    _ego4dAccessKey->setPlaceholderText(egoConfigured
        ? QStringLiteral("%1 — digite somente para substituir").arg(hint)
        : QStringLiteral("Access Key ID recebido por email"));
    _ego4dSecretKey->setPlaceholderText(egoConfigured
        ? QStringLiteral("Protegido — digite somente para substituir")
        : QStringLiteral("Secret Access Key"));
    const QString region = ego.value(QStringLiteral("region")).toString();
    if (_ego4dRegion->text().isEmpty() && region != QStringLiteral("automática"))
      _ego4dRegion->setPlaceholderText(region);
    if (!passive || _ego4dTest->text() != QStringLiteral("Testando…"))
      _ego4dTest->setEnabled(egoConfigured);
    _ego4dPrepare->setEnabled(egoConfigured && !egoCatalog && !_ego4dCatalogPreparing);

    const auto hostProfiles = host.value(QStringLiteral("profiles")).toArray();
    const QString selectedHostId = _hostingerProfile->currentData().toMap()
                                       .value(QStringLiteral("id")).toString();
    QJsonArray displayedProfiles;
    for (int index = 0; index < _hostingerProfile->count(); ++index)
      displayedProfiles.append(QJsonObject::fromVariantMap(_hostingerProfile->itemData(index).toMap()));
    if (displayedProfiles != hostProfiles) {
      const QSignalBlocker blocker(_hostingerProfile);
      _hostingerProfile->clear();
      int selectedIndex = -1;
      for (const QJsonValue& value : hostProfiles) {
        const auto profile = value.toObject();
        const QString name = profile.value(QStringLiteral("name")).toString(
            QStringLiteral("Caixa Hostinger"));
        const QString hint = profile.value(QStringLiteral("token_hint")).toString();
        _hostingerProfile->addItem(
            hint.isEmpty() ? name : QStringLiteral("%1 · %2").arg(name, hint),
            profile.toVariantMap());
        if (profile.value(QStringLiteral("id")).toString() == selectedHostId)
          selectedIndex = _hostingerProfile->count() - 1;
      }
      if (_hostingerProfile->count() > 0)
        _hostingerProfile->setCurrentIndex(selectedIndex >= 0 ? selectedIndex : 0);
    }
    if (!passive) selectHostingerIntegration(_hostingerProfile->currentIndex());
    else {
      _hostingerRemove->setEnabled(_hostingerProfile->currentIndex() >= 0);
      if (_hostingerTest->text() != QStringLiteral("Testando…"))
        _hostingerTest->setEnabled(_hostingerProfile->currentIndex() >= 0 || !_hostingerToken->text().isEmpty());
    }
    const int hostCount = host.value(QStringLiteral("connection_count")).toInt();
    _hostingerStatus->setText(hostConfigured
        ? QStringLiteral("● %1 caixa(s) identificada(s) e disponível(is)").arg(hostCount)
        : QStringLiteral("● Nenhuma caixa conectada"));
    _hostingerStatus->setProperty("integrationState", hostConfigured ? "ok" : "missing");
    _hostingerStatus->style()->unpolish(_hostingerStatus);
    _hostingerStatus->style()->polish(_hostingerStatus);
    if (!hostConfigured && !passive) selectHostingerIntegration(-1);

    _holoIntegrationStatus->setText(holoReady
        ? QStringLiteral("✓ Catálogo e índices instalados")
        : QStringLiteral("○ Preparação automática ao usar HoloAssist"));
    _runtimeIntegrationStatus->setText(runtimeReady
        ? QStringLiteral("✓ Motor e ferramentas disponíveis")
        : QStringLiteral("! Componente ausente; use Reparar instalação"));
    if (!passive) setStatus(QStringLiteral("Estado das integrações atualizado."));
  });
}

void MainWindow::saveEgo4dIntegration() {
  QJsonObject body;
  if (!_ego4dAccessKey->text().trimmed().isEmpty())
    body.insert(QStringLiteral("access_key_id"), _ego4dAccessKey->text().trimmed());
  if (!_ego4dSecretKey->text().trimmed().isEmpty())
    body.insert(QStringLiteral("secret_access_key"), _ego4dSecretKey->text().trimmed());
  if (!_ego4dSessionToken->text().trimmed().isEmpty())
    body.insert(QStringLiteral("session_token"), _ego4dSessionToken->text().trimmed());
  if (!_ego4dRegion->text().trimmed().isEmpty())
    body.insert(QStringLiteral("region"), _ego4dRegion->text().trimmed());
  _ego4dSave->setEnabled(false);
  _ego4dSave->setText(QStringLiteral("Validando…"));
  _api.put(QStringLiteral("/api/integrations/ego4d"), body,
           [this](bool ok, const QJsonDocument&, const QString& error) {
    _ego4dSave->setEnabled(true);
    _ego4dSave->setText(QStringLiteral("Validar e salvar"));
    if (!ok) return showError(QStringLiteral("Ego4D não configurado"), error);
    _ego4dAccessKey->clear();
    _ego4dSecretKey->clear();
    _ego4dSessionToken->clear();
    setStatus(QStringLiteral("Credencial validada. Preparando o catálogo Ego4D…"));
    loadIntegrations();
    loadReadiness();
  });
}

void MainWindow::testEgo4dIntegration() {
  QJsonObject body;
  if (!_ego4dAccessKey->text().trimmed().isEmpty())
    body.insert(QStringLiteral("access_key_id"), _ego4dAccessKey->text().trimmed());
  if (!_ego4dSecretKey->text().trimmed().isEmpty())
    body.insert(QStringLiteral("secret_access_key"), _ego4dSecretKey->text().trimmed());
  if (!_ego4dSessionToken->text().trimmed().isEmpty())
    body.insert(QStringLiteral("session_token"), _ego4dSessionToken->text().trimmed());
  if (!_ego4dRegion->text().trimmed().isEmpty())
    body.insert(QStringLiteral("region"), _ego4dRegion->text().trimmed());
  _ego4dTest->setEnabled(false);
  _ego4dTest->setText(QStringLiteral("Testando…"));
  _api.post(QStringLiteral("/api/integrations/ego4d/test"), body,
            [this](bool ok, const QJsonDocument& doc, const QString& error) {
    _ego4dTest->setEnabled(true);
    _ego4dTest->setText(QStringLiteral("Testar acesso"));
    if (!ok) return showError(QStringLiteral("Teste Ego4D"), error);
    QMessageBox::information(this, QStringLiteral("Ego4D conectado"),
        doc.object().value(QStringLiteral("message")).toString(
            QStringLiteral("Acesso ao catálogo confirmado.")));
  });
}

void MainWindow::prepareEgo4dCatalog() {
  if (_ego4dCatalogPreparing) return;
  _ego4dCatalogPreparing = true;
  _ego4dPrepare->setEnabled(false);
  _ego4dPrepare->setText(QStringLiteral("Preparando…"));
  _api.post(QStringLiteral("/api/integrations/ego4d/catalog"),
            QJsonObject{{QStringLiteral("include_annotations"), true}},
            [this](bool ok, const QJsonDocument& doc, const QString& error) {
    _ego4dCatalogPreparing = false;
    _ego4dPrepare->setText(QStringLiteral("Preparar catálogo"));
    if (!ok) {
      _ego4dPrepare->setEnabled(true);
      return showError(QStringLiteral("Catálogo Ego4D"), error);
    }
    setStatus(doc.object().value(QStringLiteral("message")).toString(
        QStringLiteral("Catálogo Ego4D preparado.")));
    loadIntegrations();
    loadReadiness();
    loadTasks();
  });
}

void MainWindow::saveHostingerIntegration() {
  QJsonObject body;
  body.insert(QStringLiteral("auto_detect"), true);
  body.insert(QStringLiteral("token"), _hostingerToken->text().trimmed());
  _hostingerSave->setEnabled(false);
  _hostingerSave->setText(QStringLiteral("Identificando…"));
  _api.put(QStringLiteral("/api/integrations/hostinger"), body,
           [this](bool ok, const QJsonDocument& doc, const QString& error) {
    _hostingerSave->setEnabled(true);
    _hostingerSave->setText(QStringLiteral("Identificar e conectar"));
    if (!ok) return showError(QStringLiteral("Hostinger não configurada"), error);
    _hostingerToken->clear();
    const int detected = doc.object().value(QStringLiteral("detected_count")).toInt();
    setStatus(QStringLiteral("%1 caixa(s) identificada(s) e conectada(s) automaticamente.")
                  .arg(detected));
    loadBulkRegisterDomains();
    loadIntegrations();
    loadReadiness();
  });
}

void MainWindow::testHostingerIntegration() {
  QJsonObject body;
  const auto profile = _hostingerProfile->currentData().toMap();
  const QString profileId = profile.value(QStringLiteral("id")).toString();
  if (!_hostingerToken->text().trimmed().isEmpty()) {
    body.insert(QStringLiteral("token"), _hostingerToken->text().trimmed());
  } else if (!profileId.isEmpty()) {
    body.insert(QStringLiteral("profile_id"), profileId);
  }
  _hostingerTest->setEnabled(false);
  _hostingerTest->setText(QStringLiteral("Testando…"));
  _api.post(QStringLiteral("/api/integrations/hostinger/test"), body,
            [this](bool ok, const QJsonDocument& doc, const QString& error) {
    _hostingerTest->setEnabled(true);
    _hostingerTest->setText(QStringLiteral("Testar"));
    if (!ok) return showError(QStringLiteral("Teste Hostinger"), error);
    const int boxes = doc.object().value(QStringLiteral("mailboxes")).toInt();
    QMessageBox::information(this, QStringLiteral("Hostinger conectada"),
        QStringLiteral("Credencial válida · %1 caixa(s) disponível(is).").arg(boxes));
  });
}

void MainWindow::selectHostingerIntegration(int index) {
  _hostingerToken->clear();
  _hostingerToken->setPlaceholderText(
      QStringLiteral("Cole o token de outra conta Hostinger"));
  _hostingerRemove->setEnabled(index >= 0);
  _hostingerTest->setEnabled(index >= 0);
  _hostingerSave->setEnabled(false);
  _hostingerSave->setText(QStringLiteral("Identificar e conectar"));
}

void MainWindow::removeHostingerIntegration() {
  const auto profile = _hostingerProfile->currentData().toMap();
  const QString profileId = profile.value(QStringLiteral("id")).toString();
  const QString name = profile.value(QStringLiteral("name")).toString();
  if (profileId.isEmpty()) return;
  if (QMessageBox::question(
          this, QStringLiteral("Remover conexão Hostinger"),
          QStringLiteral("Remover a conexão “%1”?\n\n"
                         "Os códigos dos domínios associados deixarão de ser lidos por ela.")
              .arg(name)) != QMessageBox::Yes) return;
  _hostingerRemove->setEnabled(false);
  _api.remove(QStringLiteral("/api/integrations/hostinger/") + encoded(profileId),
              [this](bool ok, const QJsonDocument&, const QString& error) {
    if (!ok) {
      _hostingerRemove->setEnabled(true);
      return showError(QStringLiteral("Conexão não removida"), error);
    }
    setStatus(QStringLiteral("Conexão Hostinger removida."));
    loadIntegrations();
    loadReadiness();
  });
}

QWidget* MainWindow::buildIntegrationsPage() {
  auto* body = new QWidget;
  auto* bodyLayout = new QVBoxLayout(body);
  bodyLayout->setContentsMargins(0, 0, 0, 0);

  auto* scroll = new QScrollArea;
  scroll->setWidgetResizable(true);
  scroll->setFrameShape(QFrame::NoFrame);
  auto* content = new QWidget;
  auto* layout = new QVBoxLayout(content);
  layout->setContentsMargins(0, 0, 8, 0);
  layout->setSpacing(14);

  auto* hero = new QWidget;
  auto* heroLayout = new QHBoxLayout(hero);
  heroLayout->setContentsMargins(0, 0, 0, 0);
  heroLayout->setSpacing(18);
  auto* heroCopy = new QVBoxLayout;
  heroCopy->setSpacing(5);
  auto* kicker = new QLabel(QStringLiteral("COFRE DE ACESSO"));
  kicker->setObjectName(QStringLiteral("kicker"));
  heroCopy->addWidget(kicker);
  _integrationsHeadline = new QLabel(QStringLiteral("Verificando suas conexões…"));
  _integrationsHeadline->setObjectName(QStringLiteral("pulseTitle"));
  _integrationsHeadline->setWordWrap(true);
  heroCopy->addWidget(_integrationsHeadline);
  _integrationsSummary = quietLabel(QStringLiteral("Carregando diagnóstico…"));
  _integrationsSummary->setWordWrap(true);
  heroCopy->addWidget(_integrationsSummary);
  heroLayout->addLayout(heroCopy, 1);
  _integrationSecurity = quietLabel(QStringLiteral("Proteção do Windows"));
  _integrationSecurity->setObjectName(QStringLiteral("securityBadge"));
  _integrationSecurity->setAlignment(Qt::AlignCenter);
  heroLayout->addWidget(_integrationSecurity);
  auto* heroCard = card(QString(), hero);
  heroCard->setObjectName(QStringLiteral("pulseCard"));
  layout->addWidget(heroCard);

  auto* appCheckBody = new QWidget;
  auto* appCheckLayout = new QVBoxLayout(appCheckBody);
  appCheckLayout->setContentsMargins(0, 0, 0, 0);
  appCheckLayout->setSpacing(12);
  _appCheckStatus = quietLabel(QStringLiteral("Consultando estado local do App Check…"));
  _appCheckStatus->setObjectName(QStringLiteral("appCheckStatus"));
  _appCheckStatus->setWordWrap(true);
  appCheckLayout->addWidget(_appCheckStatus);
  auto* appCheckActions = new QHBoxLayout;
  appCheckActions->addStretch();
  auto* appCheckRefresh = new QPushButton(QStringLiteral("Atualizar conexões"));
  appCheckRefresh->setObjectName(QStringLiteral("appCheckRefresh"));
  appCheckRefresh->setToolTip(QStringLiteral(
      "Atualiza o estado local e o cooldown sem emitir um token App Check."));
  connect(appCheckRefresh, &QPushButton::clicked, this, &MainWindow::loadIntegrations);
  appCheckActions->addWidget(appCheckRefresh);
  _appCheckRecheck = new QPushButton(QStringLiteral("Rechecar App Check"));
  _appCheckRecheck->setObjectName(QStringLiteral("appCheckRecheck"));
  _appCheckRecheck->setEnabled(false);
  _appCheckRecheck->setToolTip(QStringLiteral(
      "Com JWT configurado, verifica a validade local; com debug token autorizado, solicita emissão ao Firebase. Não valida CREATE."));
  connect(_appCheckRecheck, &QPushButton::clicked,
          this, &MainWindow::recheckAppCheck);
  appCheckActions->addWidget(_appCheckRecheck);
  appCheckLayout->addLayout(appCheckActions);
  layout->addWidget(card(QStringLiteral("Validação Minute (App Check)"), appCheckBody));

  auto* egoBody = new QWidget;
  auto* egoLayout = new QVBoxLayout(egoBody);
  egoLayout->setContentsMargins(0, 0, 0, 0);
  egoLayout->setSpacing(12);
  auto* egoStatusRow = new QHBoxLayout;
  _ego4dStatus = new QLabel(QStringLiteral("Verificando credencial…"));
  _ego4dStatus->setObjectName(QStringLiteral("integrationStatus"));
  _ego4dStatus->setWordWrap(true);
  egoStatusRow->addWidget(_ego4dStatus, 1);
  _ego4dCatalog = quietLabel(QStringLiteral("Catálogo: verificando…"));
  egoStatusRow->addWidget(_ego4dCatalog);
  egoLayout->addLayout(egoStatusRow);
  auto* egoFormBody = new QWidget;
  auto* egoForm = new QFormLayout(egoFormBody);
  configureForm(egoForm);
  egoForm->setContentsMargins(0, 0, 0, 0);
  _ego4dAccessKey = new QLineEdit;
  _ego4dAccessKey->setEchoMode(QLineEdit::PasswordEchoOnEdit);
  _ego4dAccessKey->setPlaceholderText(QStringLiteral("Access Key ID recebido por email"));
  _ego4dAccessKey->setToolTip(QStringLiteral("Chave recebida após a aprovação da licença Ego4D."));
  egoForm->addRow(QStringLiteral("Access Key ID"), _ego4dAccessKey);
  _ego4dSecretKey = new QLineEdit;
  _ego4dSecretKey->setEchoMode(QLineEdit::Password);
  _ego4dSecretKey->setPlaceholderText(QStringLiteral("Secret Access Key"));
  egoForm->addRow(QStringLiteral("Secret Access Key"), _ego4dSecretKey);
  auto updateEgoTest = [this] {
    if (!_ego4dAccessKey->text().trimmed().isEmpty()
        && !_ego4dSecretKey->text().trimmed().isEmpty())
      _ego4dTest->setEnabled(true);
  };
  connect(_ego4dAccessKey, &QLineEdit::textChanged, this, updateEgoTest);
  connect(_ego4dSecretKey, &QLineEdit::textChanged, this, updateEgoTest);
  _ego4dSessionToken = new QLineEdit;
  _ego4dSessionToken->setEchoMode(QLineEdit::Password);
  _ego4dSessionToken->setPlaceholderText(QStringLiteral("Opcional; deixe vazio para manter o salvo"));
  egoForm->addRow(QStringLiteral("Session Token"), _ego4dSessionToken);
  _ego4dRegion = new QLineEdit;
  _ego4dRegion->setPlaceholderText(QStringLiteral("Automática"));
  egoForm->addRow(QStringLiteral("Região AWS"), _ego4dRegion);
  egoLayout->addWidget(egoFormBody);

  auto* egoActions = new QHBoxLayout;
  auto* accessHelp = new QPushButton(QStringLiteral("Como obter acesso"));
  connect(accessHelp, &QPushButton::clicked, this, [] {
    QDesktopServices::openUrl(QUrl(QStringLiteral(
        "https://ego4d-data.org/docs/start-here/")));
  });
  egoActions->addWidget(accessHelp);
  _ego4dPrepare = new QPushButton(QStringLiteral("Preparar catálogo"));
  connect(_ego4dPrepare, &QPushButton::clicked,
          this, &MainWindow::prepareEgo4dCatalog);
  egoActions->addWidget(_ego4dPrepare);
  egoActions->addStretch();
  _ego4dTest = new QPushButton(QStringLiteral("Testar acesso"));
  connect(_ego4dTest, &QPushButton::clicked,
          this, &MainWindow::testEgo4dIntegration);
  egoActions->addWidget(_ego4dTest);
  _ego4dSave = primaryButton(QStringLiteral("Validar e salvar"));
  _ego4dSave->setToolTip(QStringLiteral("Valida o acesso antes de salvar as credenciais."));
  connect(_ego4dSave, &QPushButton::clicked,
          this, &MainWindow::saveEgo4dIntegration);
  egoActions->addWidget(_ego4dSave);
  egoLayout->addLayout(egoActions);
  layout->addWidget(card(QStringLiteral("Ego4D · Conteúdo licenciado"), egoBody));

  auto* hostBody = new QWidget;
  auto* hostLayout = new QVBoxLayout(hostBody);
  hostLayout->setContentsMargins(0, 0, 0, 0);
  hostLayout->setSpacing(12);
  _hostingerStatus = new QLabel(QStringLiteral("Verificando token…"));
  _hostingerStatus->setObjectName(QStringLiteral("integrationStatus"));
  _hostingerStatus->setWordWrap(true);
  hostLayout->addWidget(_hostingerStatus);
  auto* hostFormBody = new QWidget;
  auto* hostForm = new QFormLayout(hostFormBody);
  configureForm(hostForm);
  hostForm->setContentsMargins(0, 0, 0, 0);
  auto* hostSelector = new QWidget;
  auto* hostSelectorLayout = new QHBoxLayout(hostSelector);
  hostSelectorLayout->setContentsMargins(0, 0, 0, 0);
  hostSelectorLayout->setSpacing(8);
  _hostingerProfile = new ComboBox;
  configureCombo(_hostingerProfile, 480);
  connect(_hostingerProfile, qOverload<int>(&QComboBox::currentIndexChanged),
          this, &MainWindow::selectHostingerIntegration);
  hostSelectorLayout->addWidget(_hostingerProfile, 1);
  _hostingerRemove = new QPushButton(QStringLiteral("Remover"));
  connect(_hostingerRemove, &QPushButton::clicked,
          this, &MainWindow::removeHostingerIntegration);
  hostSelectorLayout->addWidget(_hostingerRemove);
  hostForm->addRow(QStringLiteral("Caixas conectadas"), hostSelector);
  _hostingerToken = new QLineEdit;
  _hostingerToken->setEchoMode(QLineEdit::Password);
  _hostingerToken->setPlaceholderText(
      QStringLiteral("Cole o token de outra conta Hostinger"));
  hostForm->addRow(QStringLiteral("Adicionar API"), _hostingerToken);
  connect(_hostingerToken, &QLineEdit::textChanged, this, [this] {
    const bool entered = !_hostingerToken->text().trimmed().isEmpty();
    _hostingerTest->setEnabled(entered || _hostingerProfile->currentIndex() >= 0);
    _hostingerSave->setEnabled(entered);
  });
  hostLayout->addWidget(hostFormBody);
  auto* hostActions = new QHBoxLayout;
  hostActions->addStretch();
  _hostingerTest = new QPushButton(QStringLiteral("Testar"));
  connect(_hostingerTest, &QPushButton::clicked,
          this, &MainWindow::testHostingerIntegration);
  hostActions->addWidget(_hostingerTest);
  _hostingerSave = primaryButton(QStringLiteral("Identificar e conectar"));
  _hostingerSave->setToolTip(QStringLiteral("O token da API identifica caixas, endereços e domínios para a leitura dos códigos."));
  _hostingerSave->setEnabled(false);
  connect(_hostingerSave, &QPushButton::clicked,
          this, &MainWindow::saveHostingerIntegration);
  hostActions->addWidget(_hostingerSave);
  hostLayout->addLayout(hostActions);
  layout->addWidget(card(QStringLiteral("Hostinger · Códigos de verificação"), hostBody));

  auto* local = new QWidget;
  auto* localLayout = new QHBoxLayout(local);
  localLayout->setContentsMargins(0, 0, 0, 0);
  localLayout->setSpacing(18);
  auto* holoCopy = new QVBoxLayout;
  auto* holoTitle = new QLabel(QStringLiteral("HoloAssist"));
  holoTitle->setObjectName(QStringLiteral("integrationMiniTitle"));
  holoCopy->addWidget(holoTitle);
  _holoIntegrationStatus = quietLabel(QStringLiteral("Verificando catálogo e índices…"));
  _holoIntegrationStatus->setWordWrap(true);
  _holoIntegrationStatus->setToolTip(QStringLiteral("HoloAssist não exige credenciais e é preparado automaticamente ao ser usado."));
  holoCopy->addWidget(_holoIntegrationStatus);
  localLayout->addLayout(holoCopy, 1);
  auto* runtimeCopy = new QVBoxLayout;
  auto* runtimeTitle = new QLabel(QStringLiteral("Ferramentas privadas"));
  runtimeTitle->setObjectName(QStringLiteral("integrationMiniTitle"));
  runtimeCopy->addWidget(runtimeTitle);
  _runtimeIntegrationStatus = quietLabel(QStringLiteral("Verificando FFmpeg e FFprobe…"));
  _runtimeIntegrationStatus->setWordWrap(true);
  _runtimeIntegrationStatus->setToolTip(QStringLiteral("Motor, FFmpeg, FFprobe e navegador acompanham o aplicativo."));
  runtimeCopy->addWidget(_runtimeIntegrationStatus);
  localLayout->addLayout(runtimeCopy, 1);
  auto* readinessButton = new QPushButton(QStringLiteral("Abrir prontidão"));
  connect(readinessButton, &QPushButton::clicked, this,
          [this] { navigate(1); });
  localLayout->addWidget(readinessButton);
  _repairInstall = new QPushButton(QStringLiteral("Reparar instalação"));
  _repairInstall->setToolTip(QStringLiteral(
      "Baixa novamente o pacote oficial assinado sem apagar contas ou configurações."));
  connect(_repairInstall, &QPushButton::clicked, this, [this] {
    if (_updates.isBusy()) return;
    _repairInstall->setEnabled(false);
    _repairInstall->setText(QStringLiteral("Localizando pacote…"));
    restartBackend();
  });
  localLayout->addWidget(_repairInstall);
  layout->addWidget(card(QStringLiteral("Componentes incluídos no QMoney"), local));
  layout->addStretch();

  scroll->setWidget(content);
  bodyLayout->addWidget(scroll);
  return pageShell(QStringLiteral("Integrações"), QString(), body);
}

