  static bool claruSmoke() {
    const auto failure = [](int line) { std::cerr << "Claru native assertion failed at " << line << "\n"; return false; };
    auto* style = new oclero::qlementine::QlementineStyle;
    qApp->setStyle(style);
    MainWindow window(style, nullptr, false);
    window._backendReady = true;
    QTcpServer server;
    if (!server.listen(QHostAddress::LocalHost, 0)) return failure(__LINE__);
    window._api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    int profiles = 0, registrations = 0, logins = 0, batches = 0, balances = 0, balanceStops = 0;
    QString batchRequestId;
    QString statusRequestId;
    bool valid = true;
    const QJsonObject row{{"login_email", "fixture@example.invalid"},
        {"minute_email", "generated@supply.claru.ai"}, {"name", "Fixture User"}};
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
        if (route.startsWith("GET /api/accounts/claru ")) {
          out = {{"accounts", QJsonArray{row}}, {"invite_code", "WSNEHSKC"}};
        } else if (route.startsWith("POST /api/accounts/claru/connect ")) {
          ++profiles;
          valid &= body.value("email") == "fixture@example.invalid" && body.value("password") == "claru-fixture-password";
          out = {{"ok", true}, {"account", row}, {"invite_code", "WSNEHSKC"}};
        } else if (route.startsWith("GET /api/accounts/proxies ")) {
          out = {{"proxies", QJsonArray{}}};
        } else if (route.startsWith("POST /api/accounts/bulk-register ")) {
          ++batches;
          batchRequestId = body.value("request_id").toString();
          statusRequestId = batchRequestId;
          const auto entries = body.value("accounts").toArray();
          valid &= body.value("provider") == "claru" && entries.size() == 2 && !batchRequestId.isEmpty();
          if (entries.size() == 2) {
            valid &= entries[0].toObject().value("login_email") == "first@example.invalid"
                && entries[1].toObject().value("login_email") == "second@example.invalid"
                && entries[0].toObject().value("minute_password") == "minute-first-fixture"
                && entries[1].toObject().value("minute_password") == "minute-second-fixture";
          }
          out = {{"ok", true}, {"request_id", batchRequestId}};
        } else if (route.startsWith("GET /api/balances ")) {
          ++balances;
          out = {{"accounts", QJsonArray{"generated@supply.claru.ai"}},
              {"account_kinds", QJsonObject{{"generated@supply.claru.ai", "claru"}}},
              {"balances", QJsonObject{{"generated@supply.claru.ai", QJsonObject{{"provider", "claru"}, {"availableCents", 1234},
                  {"submissionCents", 1034}, {"referralCents", 100}, {"bonusCents", 100}}}}},
              {"wallet", QJsonObject{
                  {"accounts", QJsonObject{{"generated@supply.claru.ai", QJsonObject{
                      {"reading", QJsonObject{{"confirmed", true}, {"code", "confirmed"}}}}}}},
                  {"totals", QJsonObject{{"availableCents", 1234}, {"pendingCents", 0}}}}},
              {"with_password", QJsonArray{"generated@supply.claru.ai"}},
              {"runner", balanceStops ? QJsonObject{{"state", "stopped"}}
                  : QJsonObject{{"state", "running"}, {"phase", "cooldown"},
                      {"current", "Crowtado limitou as consultas. Aguardando para tentar novamente."}}}};
        } else if (route.startsWith("POST /api/balances/stop ")) {
          ++balanceStops;
          out = {{"ok", true}};
        } else if (route.startsWith("POST /api/accounts/register?async=1 ") || route.startsWith("POST /api/accounts ")) {
          const bool registering = route.contains("register?async=1");
          if (registering) ++registrations; else ++logins;
          if (registering) statusRequestId = body.value("request_id").toString();
          valid &= body.value("email") == "generated@supply.claru.ai"
              && body.value("password") == "minute-fixture-password"
              && !bytes->contains("claru-fixture-password") && !bytes->contains("fixture@example.invalid");
          if (registering) valid &= !body.value("request_id").toString().isEmpty() && body.contains("proxy_id");
          out = registering ? QJsonObject{{"ok", true}, {"request_id", statusRequestId}}
                            : QJsonObject{{"ok", true}};
        } else if (route.startsWith("GET /api/accounts/bulk-register/status ")) {
          QJsonArray results;
          if (batches) {
            const QJsonObject savedStep{{"status", "ok"}, {"detail", "fixture checkpoint"}};
            const QJsonObject savedSteps{{"save_partial", savedStep}};
            const QJsonObject first{{"email", "first@supply.claru.ai"}, {"claru_login", "first@example.invalid"},
                {"minute_email", "first@supply.claru.ai"}, {"created", true},
                {"error", QJsonValue::Null}, {"steps", savedSteps}};
            const QJsonObject second{{"email", "second@supply.claru.ai"}, {"claru_login", "second@example.invalid"},
                {"minute_email", "second@supply.claru.ai"}, {"created", true},
                {"error", QJsonValue::Null}, {"steps", savedSteps}};
            results = {first, second};
          } else if (registrations) {
            const QJsonObject savedStep{{"status", "ok"}, {"detail", "fixture checkpoint"}};
            results = {QJsonObject{{"email", "generated@supply.claru.ai"}, {"created", true},
                {"error", QJsonValue::Null}, {"steps", QJsonObject{{"save_partial", savedStep}}}}};
          }
          out = {{"state", "done"}, {"domain", "supply.claru.ai"}, {"total", batches ? 2 : 1},
              {"completed", batches ? 2 : 1}, {"created", batches ? 2 : 1}, {"failed", 0}, {"results", results},
              {"provider", batches ? QStringLiteral("claru") : QString()}, {"request_id", statusRequestId}};
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
      while (!condition() && clock.elapsed() < 5000) qApp->processEvents(QEventLoop::AllEvents, 20);
      return condition();
    };
    if (!window._claruMinuteEmail->isReadOnly() || window._claruRegisterMinute->isEnabled()) return failure(__LINE__);
    window.loadRegistrationProxies();
    if (!until([&] { return window._registrationProxiesReady; })) return failure(__LINE__);
    window._claruEmail->setText("fixture@example.invalid");
    window._claruPassword->setText("claru-fixture-password");
    window.connectClaruAccount();
    if (!until([&] { return !window._accountTransferBusy; })) return failure(__LINE__);
    if (profiles != 1 || window._claruMinuteEmail->text() != "generated@supply.claru.ai"
        || !window._claruPassword->text().isEmpty() || !window._claruRegisterMinute->isEnabled()) return failure(__LINE__);
    window._claruMinutePassword->setText("minute-fixture-password");
    window.connectClaruMinute(true);
    if (!until([&] { return registrations == 1 && !window._bulkRegisterStarting
          && !window._bulkRegisterPolling && !window._accountTransferBusy; })) return failure(__LINE__);
    if (!window._claruMinutePassword->text().isEmpty() || !window._claruStatus->text().contains("1 criada")) return failure(__LINE__);
    window._claruMinutePassword->setText("minute-fixture-password");
    window.connectClaruMinute(false);
    if (!until([&] { return logins == 1 && !window._accountTransferBusy; })) return failure(__LINE__);
    window._claruEmail->setText("another@example.invalid");
    if (!window._claruMinuteEmail->text().isEmpty() || window._claruAddMinute->isEnabled()) return failure(__LINE__);
    QTemporaryDir input;
    QFile manifest(input.filePath("claru-minute.json"));
    if (!manifest.open(QIODevice::WriteOnly)) return failure(__LINE__);
    manifest.write(QJsonDocument(QJsonArray{
        QJsonObject{{"login_email", "first@example.invalid"}, {"claru_password", "claru-first-fixture"}, {"minute_password", "minute-first-fixture"}},
        QJsonObject{{"login_email", "second@example.invalid"}, {"claru_password", "claru-second-fixture"}, {"minute_password", "minute-second-fixture"}}
    }).toJson());
    manifest.close();
    window.importClaruBatchFile(manifest.fileName());
    if (window._claruBatchAccounts.size() != 2 || window._claruBatchTable->rowCount() != 2 || !window._claruBatchStart->isEnabled()) return failure(__LINE__);
    window.startClaruBatch(false);
    if (!until([&] { return batches == 1 && !window._bulkRegisterStarting && !window._bulkRegisterPolling && !window._accountTransferBusy; })) return failure(__LINE__);
    if (!window._claruBatchAccounts.isEmpty() || window._claruBatchTable->rowCount() != 2
        || window._claruBatchTable->item(0, 1)->text() != "first@supply.claru.ai"
        || window._claruBatchTable->item(1, 1)->text() != "second@supply.claru.ai") {
      std::cerr << "Queued: " << window._claruBatchAccounts.size() << ", table: " << window._claruBatchTable->rowCount()
                << ", snapshot: " << QJsonDocument(window._claruBatchSnapshot).toJson().constData() << "\n";
      return failure(__LINE__);
    }
    window.show();
    window._pages->setCurrentIndex(6);
    qApp->processEvents();
    window.loadBalances();
    if (!until([&] { return balances == 1 && window._balancesTable->rowCount() == 1; })) return failure(__LINE__);
    if (window._balancesTable->item(0, 1)->text() != window.usdMoney(1234)
        || window._balancesTable->item(0, 2)->text() != QStringLiteral("—")
        || window._balancesApprovedUsd->text() != window.usdMoney(1234)) return failure(__LINE__);
    const auto actions = window._balancesTable->cellWidget(0, 4)->findChildren<QPushButton*>();
    if (actions.size() != 2 || actions[0]->text() != QStringLiteral("Ganhos Claru")
        || actions[1]->text() != QStringLiteral("Abrir pagamentos")) return failure(__LINE__);
    if (!window._balancesStop->isVisible() || !window._balancesStop->isEnabled()
        || !window._balancesState->text().contains(QStringLiteral("Crowtado limitou"))) return failure(__LINE__);
    window._balancesStop->click();
    if (!until([&] { return balanceStops == 1 && balances >= 2 && !window._balancesStop->isVisible(); }))
      return failure(__LINE__);
    window.close();
    return valid || failure(__LINE__);
  }
