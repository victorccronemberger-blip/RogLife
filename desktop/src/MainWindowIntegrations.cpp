#include "MainWindowSupport.hpp"
#include "MainWindow.hpp"

void MainWindow::loadIntegrations() {
  _api.get(QStringLiteral("/api/integrations"),
           [this](bool ok, const QJsonDocument& doc, const QString& error) {
    if (!ok) return showError(QStringLiteral("Integrações indisponíveis"), error);
    const auto root = doc.object();
    const auto ego = root.value(QStringLiteral("ego4d")).toObject();
    const auto host = root.value(QStringLiteral("hostinger")).toObject();
    const auto holo = root.value(QStringLiteral("holoassist")).toObject();
    const auto runtime = root.value(QStringLiteral("runtime")).toObject();
    const auto security = root.value(QStringLiteral("security")).toObject();
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
        ? QStringLiteral("Credenciais, catálogos e ferramentas estão disponíveis para a operação.")
        : QStringLiteral("Conclua os itens pendentes abaixo; cada teste explica exatamente o que corrigir."));
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
    _ego4dTest->setEnabled(egoConfigured);
    _ego4dPrepare->setEnabled(egoConfigured && !egoCatalog);

    const auto hostProfiles = host.value(QStringLiteral("profiles")).toArray();
    const QString selectedHostId = _hostingerProfile->currentData().toMap()
                                       .value(QStringLiteral("id")).toString();
    {
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
    selectHostingerIntegration(_hostingerProfile->currentIndex());
    const int hostCount = host.value(QStringLiteral("connection_count")).toInt();
    _hostingerStatus->setText(hostConfigured
        ? QStringLiteral("● %1 caixa(s) identificada(s) e disponível(is)").arg(hostCount)
        : QStringLiteral("● Nenhuma caixa conectada"));
    _hostingerStatus->setProperty("integrationState", hostConfigured ? "ok" : "missing");
    _hostingerStatus->style()->unpolish(_hostingerStatus);
    _hostingerStatus->style()->polish(_hostingerStatus);
    if (!hostConfigured) selectHostingerIntegration(-1);

    _holoIntegrationStatus->setText(holoReady
        ? QStringLiteral("✓ Catálogo e índices instalados. Nenhuma credencial necessária.")
        : QStringLiteral("○ Será preparado automaticamente quando HoloAssist for usado."));
    _runtimeIntegrationStatus->setText(runtimeReady
        ? QStringLiteral("✓ Motor, FFmpeg, FFprobe e navegador acompanham o aplicativo.")
        : QStringLiteral("! Componente ausente; use Reparar instalação nesta tela."));
    setStatus(QStringLiteral("Estado das integrações atualizado."));
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
  heroCopy->addWidget(_integrationsHeadline);
  _integrationsSummary = quietLabel(QStringLiteral(
      "O QMoney organiza credenciais, catálogos e ferramentas sem exigir arquivos manuais."));
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

  auto* egoBody = new QWidget;
  auto* egoLayout = new QVBoxLayout(egoBody);
  egoLayout->setContentsMargins(0, 0, 0, 0);
  egoLayout->setSpacing(12);
  auto* egoStatusRow = new QHBoxLayout;
  _ego4dStatus = new QLabel(QStringLiteral("Verificando credencial…"));
  _ego4dStatus->setObjectName(QStringLiteral("integrationStatus"));
  egoStatusRow->addWidget(_ego4dStatus, 1);
  _ego4dCatalog = quietLabel(QStringLiteral("Catálogo: verificando…"));
  egoStatusRow->addWidget(_ego4dCatalog);
  egoLayout->addLayout(egoStatusRow);
  auto* egoHelp = quietLabel(QStringLiteral(
      "Cole as chaves recebidas após a aprovação da licença Ego4D. O QMoney valida o acesso antes de salvar."));
  egoHelp->setWordWrap(true);
  egoLayout->addWidget(egoHelp);

  auto* egoFormBody = new QWidget;
  auto* egoForm = new QFormLayout(egoFormBody);
  configureForm(egoForm);
  egoForm->setContentsMargins(0, 0, 0, 0);
  _ego4dAccessKey = new QLineEdit;
  _ego4dAccessKey->setEchoMode(QLineEdit::PasswordEchoOnEdit);
  _ego4dAccessKey->setPlaceholderText(QStringLiteral("Access Key ID recebido por email"));
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
  hostLayout->addWidget(_hostingerStatus);
  auto* hostHelp = quietLabel(QStringLiteral(
      "Cole o token da API. O QMoney identifica sozinho as caixas, os endereços e "
      "os domínios, e passa a buscar cada código no lugar certo."));
  hostHelp->setWordWrap(true);
  hostLayout->addWidget(hostHelp);
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
  holoCopy->addWidget(_holoIntegrationStatus);
  localLayout->addLayout(holoCopy, 1);
  auto* runtimeCopy = new QVBoxLayout;
  auto* runtimeTitle = new QLabel(QStringLiteral("Ferramentas privadas"));
  runtimeTitle->setObjectName(QStringLiteral("integrationMiniTitle"));
  runtimeCopy->addWidget(runtimeTitle);
  _runtimeIntegrationStatus = quietLabel(QStringLiteral("Verificando FFmpeg e FFprobe…"));
  _runtimeIntegrationStatus->setWordWrap(true);
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
  return pageShell(QStringLiteral("Integrações"),
                   QStringLiteral("Configure tudo que o QMoney precisa sem procurar arquivos no computador."),
                   body);
}

