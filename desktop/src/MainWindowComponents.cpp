#include "MainWindowSupport.hpp"
#include "MainWindow.hpp"

void MainWindow::buildShell() {
  auto* root = new QWidget;
  auto* rootLayout = new QHBoxLayout(root);
  rootLayout->setContentsMargins(0, 0, 0, 0);
  rootLayout->setSpacing(0);

  auto* sidebar = new QWidget;
  sidebar->setObjectName(QStringLiteral("sidebar"));
  sidebar->setFixedWidth(264);
  auto* side = new QVBoxLayout(sidebar);
  side->setContentsMargins(22, 26, 20, 18);
  side->setSpacing(10);

  auto* brandRow = new QHBoxLayout;
  auto* mark = new QLabel;
  mark->setObjectName(QStringLiteral("brandMark"));
  mark->setAlignment(Qt::AlignCenter);
  mark->setFixedSize(44, 44);
  mark->setPixmap(QPixmap(QStringLiteral(":/qmoney/icon.png"))
                      .scaled(42, 42, Qt::KeepAspectRatio, Qt::SmoothTransformation));
  auto* brandCopy = new QVBoxLayout;
  brandCopy->setSpacing(0);
  auto* brand = new QLabel(QStringLiteral("QMoney"));
  brand->setObjectName(QStringLiteral("brand"));
  brandCopy->addWidget(brand);
  auto* brandRole = quietLabel(QStringLiteral("OPERATIONS DESK"));
  brandRole->setObjectName(QStringLiteral("brandRole"));
  brandCopy->addWidget(brandRole);
  brandRow->addWidget(mark);
  brandRow->addSpacing(6);
  brandRow->addLayout(brandCopy, 1);
  side->addLayout(brandRow);
  side->addSpacing(20);

  auto* navigationLabel = new QLabel(QStringLiteral("ESPAÇOS DE TRABALHO"));
  navigationLabel->setObjectName(QStringLiteral("navigationLabel"));
  side->addWidget(navigationLabel);

  _navigation = new QListWidget;
  _navigation->setObjectName(QStringLiteral("navigation"));
  _navigation->setFrameShape(QFrame::NoFrame);
  _navigation->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
  const QStringList pages = {
      QStringLiteral("Visão geral"), QStringLiteral("Prontidão"),
      QStringLiteral("Integrações"), QStringLiteral("Nova campanha"),
      QStringLiteral("Acelerador"),
      QStringLiteral("Contas"), QStringLiteral("Saldos"),
      QStringLiteral("Histórico"), QStringLiteral("Banidas"), QStringLiteral("Catálogos")};
  const QStringList icons = {QStringLiteral(":/qmoney/icons/home.svg"),
                             QStringLiteral(":/qmoney/icons/readiness.svg"),
                             QStringLiteral(":/qmoney/icons/integrations.svg"),
                             QStringLiteral(":/qmoney/icons/play.svg"),
                             QStringLiteral(":/qmoney/icons/bolt.svg"),
                             QStringLiteral(":/qmoney/icons/users.svg"),
                             QStringLiteral(":/qmoney/icons/wallet.svg"),
                             QStringLiteral(":/qmoney/icons/history.svg"),
                             QStringLiteral(":/qmoney/icons/users.svg"),
                             QStringLiteral(":/qmoney/icons/bolt.svg")};
  _navigation->setIconSize(QSize(19, 19));
  for (int i = 0; i < pages.size(); ++i) {
    auto* item = new QListWidgetItem(QIcon(icons[i]), pages[i]);
    item->setSizeHint(QSize(210, 46));
    _navigation->addItem(item);
  }
  _navigation->setCurrentRow(0);
  connect(_navigation, &QListWidget::currentRowChanged, this, &MainWindow::navigate);
  side->addWidget(_navigation, 1);

  auto* connectionCard = new QFrame;
  connectionCard->setObjectName(QStringLiteral("connectionCard"));
  auto* connectionLayout = new QVBoxLayout(connectionCard);
  connectionLayout->setContentsMargins(14, 12, 14, 12);
  connectionLayout->setSpacing(5);
  connectionLayout->addWidget(quietLabel(QStringLiteral("MOTOR LOCAL")));
  _backendState = new QLabel(QStringLiteral("●  Iniciando…"));
  _backendState->setObjectName(QStringLiteral("backendState"));
  connectionLayout->addWidget(_backendState);
  side->addWidget(connectionCard);

  _themeButton = new QPushButton;
  _themeButton->setObjectName(QStringLiteral("sidebarUtility"));
  connect(_themeButton, &QPushButton::clicked, this, [this] {
    setDarkTheme(!QSettings().value(QStringLiteral("darkTheme"), false).toBool());
  });
  side->addWidget(_themeButton);

  _updateButton = new QPushButton(
      QStringLiteral("↻  Verificar atualização"));
  _updateButton->setObjectName(QStringLiteral("sidebarUtility"));
  connect(_updateButton, &QPushButton::clicked, this, [this] {
    if (_updateButton->text().startsWith(QStringLiteral("⬇"))) {
      _updateButton->setEnabled(false);
      _updates.downloadAndInstall();
    } else {
      checkForUpdates(true);
    }
  });
  side->addWidget(_updateButton);

  auto* workspace = new QWidget;
  workspace->setObjectName(QStringLiteral("workspace"));
  auto* workspaceLayout = new QVBoxLayout(workspace);
  workspaceLayout->setContentsMargins(0, 0, 0, 0);
  workspaceLayout->setSpacing(0);

  _pages = new QStackedWidget;
  qInfo() << "QMoney: construindo home";
  _pages->addWidget(buildHomePage());
  qInfo() << "QMoney: construindo prontidao";
  _pages->addWidget(buildReadinessPage());
  qInfo() << "QMoney: construindo integracoes";
  _pages->addWidget(buildIntegrationsPage());
  qInfo() << "QMoney: construindo campanha";
  _pages->addWidget(buildCampaignPage());
  qInfo() << "QMoney: construindo acelerador";
  _pages->addWidget(buildAcceleratorPage());
  qInfo() << "QMoney: construindo contas";
  _pages->addWidget(buildAccountsPage());
  qInfo() << "QMoney: construindo saldos";
  _pages->addWidget(buildBalancesPage());
  qInfo() << "QMoney: construindo historico";
  _pages->addWidget(buildHistoryPage());
  _pages->addWidget(buildBannedPage());
  _pages->addWidget(buildProviderCatalogPage());
  qInfo() << "QMoney: paginas prontas";
  workspaceLayout->addWidget(_pages, 1);

  auto* statusBar = new QWidget;
  statusBar->setObjectName(QStringLiteral("appStatusBar"));
  auto* statusLayout = new QHBoxLayout(statusBar);
  statusLayout->setContentsMargins(24, 8, 24, 8);
  _status = quietLabel(QStringLiteral("Preparando o serviço local…"));
  _status->setWordWrap(false);
  _status->setMinimumWidth(0);
  statusLayout->addWidget(_status, 1);
  auto* version = new QLabel(QStringLiteral("QMoney %1  •  Desktop nativo")
                                 .arg(QCoreApplication::applicationVersion()));
  version->setObjectName(QStringLiteral("statusVersion"));
  statusLayout->addWidget(version);
  statusLayout->addSpacing(18);
  auto* refresh = new QPushButton(QStringLiteral("Sincronizar dados"));
  refresh->setFlat(true);
  connect(refresh, &QPushButton::clicked, this, &MainWindow::refreshCurrentPage);
  statusLayout->addWidget(refresh);
  workspaceLayout->addWidget(statusBar);

  rootLayout->addWidget(sidebar);
  rootLayout->addWidget(workspace, 1);
  setCentralWidget(root);
}

QWidget* MainWindow::pageShell(const QString& title, const QString& subtitle, QWidget* body) {
  auto* shell = new QWidget;
  auto* outer = new QVBoxLayout(shell);
  outer->setContentsMargins(28, 20, 28, 20);
  outer->setSpacing(9);
  auto* contextRow = new QHBoxLayout;
  auto* context = new QLabel(QStringLiteral("QMONEY  /  %1").arg(title.toUpper()));
  context->setObjectName(QStringLiteral("pageContext"));
  contextRow->addWidget(context);
  contextRow->addStretch();
  outer->addLayout(contextRow);
  auto* titleLabel = new QLabel(title);
  titleLabel->setObjectName(QStringLiteral("pageTitle"));
  titleLabel->setMinimumHeight(44);
  outer->addWidget(titleLabel);
  auto* subtitleLabel = quietLabel(subtitle);
  subtitleLabel->setObjectName(QStringLiteral("pageSubtitle"));
  outer->addWidget(subtitleLabel);
  outer->addSpacing(2);
  outer->addWidget(body, 1);
  return shell;
}

QWidget* MainWindow::card(const QString& title, QWidget* content) {
  auto* frame = new QFrame;
  frame->setObjectName(QStringLiteral("card"));
  auto* layout = new QVBoxLayout(frame);
  layout->setContentsMargins(18, 16, 18, 16);
  layout->setSpacing(13);
  if (!title.isEmpty()) {
    auto* heading = new QLabel(title);
    heading->setObjectName(QStringLiteral("cardTitle"));
    layout->addWidget(heading);
  }
  if (content) layout->addWidget(content);
  return frame;
}

QWidget* MainWindow::metric(const QString& value, const QString& caption, QLabel** valueLabel) {
  auto* box = new QWidget;
  auto* layout = new QVBoxLayout(box);
  layout->setContentsMargins(2, 1, 2, 1);
  layout->setSpacing(3);
  auto* label = new QLabel(caption.toUpper());
  label->setObjectName(QStringLiteral("metricCaption"));
  layout->addWidget(label);
  *valueLabel = new QLabel(value);
  (*valueLabel)->setObjectName(QStringLiteral("metricValue"));
  layout->addWidget(*valueLabel);
  auto* timing = new QLabel(QStringLiteral("LEITURA ATUAL"));
  timing->setObjectName(QStringLiteral("metricTiming"));
  layout->addWidget(timing);
  return box;
}

QPushButton* MainWindow::primaryButton(const QString& text) {
  auto* button = new QPushButton(text);
  button->setProperty("role", QStringLiteral("primary"));
  button->setDefault(true);
  return button;
}

