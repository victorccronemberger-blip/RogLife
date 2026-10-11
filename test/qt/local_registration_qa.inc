  static bool registrationSmoke() {
    auto* style = new oclero::qlementine::QlementineStyle;
    qApp->setStyle(style);
    MainWindow window(style, nullptr, false);
    window._backendReady = true;
    QTcpServer server;
    if (!server.listen(QHostAddress::LocalHost, 0)) return false;
    window._api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    bool imported = false, proxyOk = true, importValid = false;
    int batches = 0, singles = 0, proxiedChecks = 0;
    bool requestValid = true;
    QString statusRequestId;
    QString statusState = QStringLiteral("idle");
    int statusTotal = 0, statusCompleted = 0, statusCreated = 0, statusFailed = 0;
    QJsonArray statusResults;
    QString statusStopReason;
    const QString proxyId = QStringLiteral("fictional-proxy-id");
    const QString fixtureText = QStringLiteral("203.0.113.10:8080:fixture-user:fixture-password\n");
    QObject::connect(&server, &QTcpServer::newConnection, &server, [&] {
      auto* socket = server.nextPendingConnection();
      auto bytes = std::make_shared<QByteArray>();
      auto answered = std::make_shared<bool>(false);
      QObject::connect(socket, &QTcpSocket::readyRead, socket, [&, socket, bytes, answered] {
        bytes->append(socket->readAll());
        const int end = bytes->indexOf("\r\n\r\n");
        if (*answered || end < 0) return;
        int length = 0;
        for (const auto& header : bytes->left(end).split('\n'))
          if (header.trimmed().toLower().startsWith("content-length:"))
            length = header.mid(header.indexOf(':') + 1).trimmed().toInt();
        if (bytes->size() < end + 4 + length) return;
        *answered = true;
        const auto route = bytes->split('\n').first();
        const auto body = QJsonDocument::fromJson(bytes->mid(end + 4)).object();
        QJsonObject out;
        if (route.startsWith("GET /api/accounts/proxies ")) {
          out = {{"proxies", imported ? QJsonArray{QJsonObject{{"id", proxyId}, {"label", "203.0.113.10:8080"}}} : QJsonArray{}}};
        } else if (route.startsWith("POST /api/accounts/proxies/import ")) {
          imported = true;
          importValid = body.value("text").toString() == fixtureText;
          out = {{"ok", true}, {"total", 1}};
        } else if (route.startsWith("GET /api/accounts/bulk-register/preflight?")) {
          const bool proxied = route.contains("proxy_id=fictional-proxy-id");
          if (proxied) ++proxiedChecks;
          const QJsonObject checks{{"hostinger", QJsonObject{{"ok", true}}},
              {"chrome", QJsonObject{{"ok", true}}}, {"crowtado_api", QJsonObject{{"ok", true}}},
              {"minute_api", QJsonObject{{"ok", true}}}, {"invite_code", QJsonObject{{"ok", true}}},
              {"proxy", QJsonObject{{"ok", proxyOk}, {"detail", "Fictional proxy unavailable"}}}};
          out = {{"ready", proxyOk}, {"checks", checks}};
        } else if (route.startsWith("POST /api/accounts/bulk-register ") || route.startsWith("POST /api/accounts/register?async=1 ")) {
          const bool single = route.contains("register?async=1");
          const QJsonObject profile = single ? body : body.value("profiles").toArray().first().toObject();
          requestValid &= profile.value("birth_month").toInt() == 5
              && profile.value("birth_year").toInt() == 1999 && profile.value("gender").toString() == "male";
          if (single) ++singles; else ++batches;
          requestValid &= body.value("proxy_id").toString() == proxyId && !body.value("request_id").toString().isEmpty();
          statusRequestId = body.value("request_id").toString();
          statusState = QStringLiteral("done");
          statusTotal = 1; statusCompleted = 1; statusStopReason.clear();
          if (!single) requestValid &= body.value("count").toInt() == 1 && body.value("domain").toString() == "example.invalid";
          if (single) {
            const QString email = body.value("email").toString();
            statusCreated = 1; statusFailed = 0;
            statusResults = {QJsonObject{{"email", email}, {"created", true}, {"error", QJsonValue::Null},
                {"steps", QJsonObject{{"save_partial", QJsonObject{{"status", "ok"}}},
                                      {"crowtado_signup", QJsonObject{{"status", "ok"}}}}}}};
          } else {
            statusCreated = 0; statusFailed = 1;
            statusStopReason = QStringLiteral("Lote interrompido: restrição confirmada");
            statusResults = {QJsonObject{{"email", "qa@example.invalid"}, {"created", false},
                {"error", "Acesso restrito"}, {"partial", false}, {"removable", false},
                {"steps", QJsonObject{{"ban_check", QJsonObject{{"status", "fail"}, {"detail", "HTTP 403"}}}}}}};
          }
          out = {{"ok", true}, {"request_id", statusRequestId}};
        } else if (route.startsWith("GET /api/accounts/bulk-register/status ")) {
          out = {{"state", statusState}, {"request_id", statusRequestId},
              {"total", statusTotal}, {"completed", statusCompleted}, {"created", statusCreated}, {"failed", statusFailed},
              {"stop_reason", statusStopReason}, {"results", statusResults}};
        } else if (route.startsWith("GET /api/accounts/banned/monitor ")) {
          out = {{"accounts", QJsonArray{QJsonObject{{"email", "qa@example.invalid"},
              {"reason", "Crowtado confirmou banimento durante o cadastro."}, {"stage", "Cadastro"},
              {"diagnostic", QJsonObject{{"detail", "Fictional HTTP 403"}}}}}}, {"runner", QJsonObject{}}};
        } else out = {{"accounts", QJsonArray{}}};
        const auto data = QJsonDocument(out).toJson(QJsonDocument::Compact);
        socket->write("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + QByteArray::number(data.size()) + "\r\nConnection: close\r\n\r\n" + data);
        socket->disconnectFromHost();
      });
      QObject::connect(socket, &QTcpSocket::disconnected, socket, &QObject::deleteLater);
    });
    auto until = [](auto condition) {
      QElapsedTimer clock; clock.start();
      while (!condition() && clock.elapsed() < 5000) {
        qApp->processEvents(QEventLoop::AllEvents, 20);
      }
      return condition();
    };
    window._bulkRegisterDomain->clear(); window._bulkRegisterDomain->addItem("example.invalid", "example.invalid");
    window.loadRegistrationProxies();
    if (!until([&] { return window._registrationProxiesReady; })) return false;
    if (window._accountProxy->count() != 1 || window._bulkRegisterCount->value() != 1) return false;
    QTemporaryDir temp; QFile file(temp.filePath("fixture.txt"));
    if (!file.open(QIODevice::WriteOnly)) return false;
    file.write(fixtureText.toUtf8()); file.close();
    window.importRegistrationProxiesFile(file.fileName());
    if (!until([&] { return imported && window._registrationProxiesReady && window._bulkRegisterProxy->count() == 2; })) return false;
    window._accountProxy->setCurrentIndex(1);
    proxyOk = false; window._bulkRegisterProxy->setCurrentIndex(1);
    if (!until([&] { return window._bulkRegisterStatus->text().contains("Fictional proxy unavailable"); })) return false;
    if (window._bulkRegisterStart->isEnabled()) return false;
    proxyOk = true; window.checkBulkRegisterDomain();
    if (!until([&] { return window._bulkRegisterStart->isEnabled(); })) return false;

    completeRegistrationProfileDialog();
    window.startBulkRegister();

    if (window._bulkRegisterProxy->isEnabled() || window._bulkProxyImport->isEnabled()) return false;
    if (!until([&] { return batches == 1 && !window._bulkRegisterPolling && !window._bulkRegisterStarting; })) return false;
    if (!window._bulkRegisterStatus->text().contains("Lote interrompido")) return false;
    window._accountEmail->setText("qa@example.invalid"); window._accountPassword->setText("fixture-password");

    completeRegistrationProfileDialog();
    window.addAccount(true);

    if (!until([&] { return singles == 1 && !window._bulkRegisterPolling && !window._bulkRegisterStarting; })) return false;
    if (!window._accountPassword->text().isEmpty()) return false;
    window.loadBanned();
    if (!until([&] { return window._bannedTable->rowCount() == 1; })) return false;
    return importValid && requestValid && proxiedChecks >= 2
        && window._bannedTable->item(0, 1)->text().contains("confirmou banimento")
        && window._bannedTable->item(0, 1)->toolTip().contains("HTTP 403");
  }
  static void completeRegistrationProfileDialog() {
    QTimer::singleShot(0, [] {
      auto* dialog = qobject_cast<QDialog*>(QApplication::activeModalWidget());
      if (!dialog || dialog->objectName() != QStringLiteral("registrationProfileDialog")) qFatal("Expected registration profile dialog");

      auto* buttons = dialog->findChild<QDialogButtonBox*>();

      buttons->button(QDialogButtonBox::Ok)->click();
      if (!dialog->isVisible()) qFatal("Empty profile must not be accepted");
      for (auto* input : dialog->findChildren<QSpinBox*>()) {
        if (input->objectName().startsWith("profileMonth")) input->setValue(5);
        if (input->objectName().startsWith("profileYear")) input->setValue(1999);
      }
      for (auto* input : dialog->findChildren<QComboBox*>())
        if (input->objectName().startsWith("profileGender")) input->setCurrentIndex(input->findData("male"));

      buttons->button(QDialogButtonBox::Ok)->click();
    });
  }

