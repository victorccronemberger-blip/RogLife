  static bool registrationRecoverySmoke() {
    const auto failure = [](int line) { std::cerr << "Registration recovery assertion failed at " << line << "\n"; return false; };
    auto* style = new oclero::qlementine::QlementineStyle;
    qApp->setStyle(style);
    MainWindow window(style, nullptr, false);
    QTcpServer server;
    if (!server.listen(QHostAddress::LocalHost, 0)) return failure(__LINE__);
    window._api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    int statusReads = 0, proxyReads = 0, domainReads = 0, preflightReads = 0, posts = 0;
    QString state = QStringLiteral("running");
    QJsonArray results;
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
        const QByteArray route = bytes->split('\n').first();
        QJsonObject out;
        if (route.startsWith("GET /api/accounts/bulk-register/status ")) {
          ++statusReads;
          out = {{"state", state}, {"request_id", "restored-registration"}, {"total", 1},
              {"completed", state == "running" ? 0 : 1}, {"created", 0},
              {"failed", state == "running" ? 0 : 1}, {"current_email", "restored@example.invalid"},
              {"current_step", "Minute"}, {"results", results}};
        } else if (route.startsWith("GET /api/accounts/proxies ")) {
          ++proxyReads; out = {{"proxies", QJsonArray{}}};
        } else if (route.startsWith("GET /api/accounts/domains ")) {
          ++domainReads; out = {{"domains", QJsonArray{QJsonObject{{"domain", "example.invalid"}}}}};
        } else if (route.startsWith("GET /api/accounts/bulk-register/preflight?")) {
          ++preflightReads;
          out = {{"ready", true}, {"checks", QJsonObject{
              {"hostinger", QJsonObject{{"ok", true}}}, {"chrome", QJsonObject{{"ok", true}}},
              {"crowtado_api", QJsonObject{{"ok", true}}}, {"minute_api", QJsonObject{{"ok", true}}},
              {"invite_code", QJsonObject{{"ok", true}}}}}};
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
    window.setBackendReady(true);
    if (!until([&] { return statusReads >= 1 && window._bulkRegisterStatus->text().contains("restored@example.invalid"); }))
      return failure(__LINE__);
    if (!window._bulkRegisterPolling || posts != 0) return failure(__LINE__);
    state = QStringLiteral("done");
    results = {QJsonObject{{"email", "restored@example.invalid"}, {"created", false},
        {"error", "Minute HTTP 502"}, {"partial", true}, {"removable", true},
        {"steps", QJsonObject{{"minute_register", QJsonObject{{"status", "fail"}, {"detail", "HTTP 502 · resposta temporária"}}}}}}};
    window.pollBulkRegister();
    if (!until([&] { return !window._bulkRegisterPolling && preflightReads > 0
          && window._bulkRegisterStatus->text().contains("Pronto para criar"); })) return failure(__LINE__);
    if (posts != 0 || proxyReads == 0 || domainReads == 0
        || window._bulkRegisterTable->rowCount() != 1
        || !window._bulkRegisterTable->item(0, 4)->text().contains("Minute")
        || !window._bulkRegisterTable->item(0, 4)->toolTip().contains("HTTP 502")
        || !window._bulkRegisterStatus->text().contains("Concluído")
        || !window._bulkRegisterStatus->text().contains("Pronto para criar")) return failure(__LINE__);
    auto* actions = qobject_cast<QPushButton*>(window._bulkRegisterTable->cellWidget(0, 5));
    if (!actions || !actions->menu()) return failure(__LINE__);
    bool canResume = false;
    for (auto* action : actions->menu()->actions()) canResume |= action->text() == QStringLiteral("Retomar esta conta");
    return canResume || failure(__LINE__);
  }

  static bool claruAdmissionRecoverySmoke() {
    const auto failure = [](int line) { std::cerr << "Claru admission recovery assertion failed at " << line << "\n"; return false; };
    auto* style = new oclero::qlementine::QlementineStyle;
    qApp->setStyle(style);
    MainWindow window(style, nullptr, false);
    QTcpServer server;
    if (!server.listen(QHostAddress::LocalHost, 0)) return failure(__LINE__);
    window._api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    window._backendReady = true;
    window._registrationProxiesReady = true;
    window._bulkRegisterDomain->addItem(QStringLiteral("example.invalid"), QStringLiteral("example.invalid"));
    window._claruBatchAccounts = {QJsonObject{{"login_email", "queued@example.invalid"},
        {"minute_password", "fixture-minute-secret"}, {"claru_password", "fixture-claru-secret"}}};
    window.renderClaruBatch({});
    QString state = QStringLiteral("running");
    QString statusRequestId = QStringLiteral("foreign-request");
    QString acceptedRequestId;
    bool resumePost = false;
    int posts = 0, statusReads = 0;
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
        const QByteArray route = bytes->split('\n').first();
        QJsonObject out;
        if (route.startsWith("GET /api/accounts/bulk-register/status ")) {
          ++statusReads;
          const bool running = state == QStringLiteral("running");
          const QJsonArray foreignResults = running ? QJsonArray{} : QJsonArray{QJsonObject{
              {"email", "foreign@example.invalid"}, {"created", true}, {"error", QJsonValue::Null},
              {"steps", QJsonObject{{"crowtado_signup", QJsonObject{{"status", "ok"}}}}}}};
          out = {{"state", state}, {"request_id", statusRequestId}, {"provider", "claru"},
              {"total", 1}, {"completed", running ? 0 : 1}, {"created", running ? 0 : 1},
              {"failed", 0}, {"results", foreignResults}};
        } else if (route.startsWith("POST /api/accounts/bulk-register ")) {
          ++posts;
          const auto requestBody = QJsonDocument::fromJson(bytes->mid(end + 4, length)).object();
          acceptedRequestId = requestBody.value("request_id").toString();
          resumePost = requestBody.value("accounts").toArray().size() == 1
              && requestBody.value("accounts").toArray().first().toObject().keys() == QStringList{QStringLiteral("login_email")};
          state = QStringLiteral("running");
          statusRequestId = acceptedRequestId;
          out = {{"ok", true}, {"request_id", acceptedRequestId}};
        } else if (route.startsWith("GET /api/accounts/proxies ")) {
          out = {{"proxies", QJsonArray{}}};
        } else if (route.startsWith("GET /api/accounts/domains ")) {
          out = {{"domains", QJsonArray{QJsonObject{{"domain", "example.invalid"}}}}};
        } else if (route.startsWith("GET /api/accounts/bulk-register/preflight?")) {
          out = {{"ready", true}, {"checks", QJsonObject{
              {"hostinger", QJsonObject{{"ok", true}}}, {"chrome", QJsonObject{{"ok", true}}},
              {"crowtado_api", QJsonObject{{"ok", true}}}, {"minute_api", QJsonObject{{"ok", true}}},
              {"invite_code", QJsonObject{{"ok", true}}}}}};
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
    window.startClaruBatch(false);
    if (!until([&] { return statusReads >= 2 && window._claruBatchRequestId.isEmpty()
          && window._bulkRegisterPolling; })) return failure(__LINE__);
    if (posts != 0 || window._claruBatchAccounts.size() != 1
        || window._claruBatchAccounts[0].toObject().value("minute_password") != "fixture-minute-secret"
        || !window._bulkRegisterFollowingForeignRequest || window._bulkRegisterForeignRequestId != "foreign-request"
        || window._claruBatchTable->rowCount() != 1
        || window._claruBatchTable->item(0, 0)->text() != "queued@example.invalid")
      return failure(__LINE__);
    statusRequestId = QStringLiteral("foreign-next-request");
    window.pollBulkRegister();
    if (!until([&] { return window._bulkRegisterForeignRequestId == "foreign-next-request"; })) return failure(__LINE__);
    if (window._claruBatchTable->rowCount() != 1
        || window._claruBatchTable->item(0, 0)->text() != "queued@example.invalid") return failure(__LINE__);
    state = QStringLiteral("done");
    window.pollBulkRegister();
    if (!until([&] { return !window._bulkRegisterPolling && window._claruBatchStart->isEnabled(); })) return failure(__LINE__);
    if (posts != 0 || window._claruBatchAccounts.size() != 1
        || window._claruBatchTable->item(0, 0)->text() != "queued@example.invalid") return failure(__LINE__);
    window._claruPending = {QJsonObject{{"login_email", "old-pending@example.invalid"}}};
    window.startClaruBatch(true);
    if (!until([&] { return posts == 1 && window._bulkRegisterOwnRequestAccepted; })) return failure(__LINE__);
    const QString acceptedClaruId = window._bulkRegisterAcceptedRequestId;
    if (acceptedClaruId.isEmpty() || acceptedClaruId != acceptedRequestId) return failure(__LINE__);
    window._bulkRegisterPoll.stop();
    state = QStringLiteral("done");
    statusRequestId = QStringLiteral("foreign-terminal");
    window.pollBulkRegister();
    if (!until([&] { return window._bulkRegisterStatus->text().contains("outro lote"); })) return failure(__LINE__);
    return resumePost && !acceptedRequestId.isEmpty() && window._claruBatchAccounts.size() == 1
        && window._claruBatchAccounts[0].toObject().value("minute_password") == "fixture-minute-secret"
        && window._claruBatchTable->rowCount() == 1
        && window._claruBatchTable->item(0, 0)->text() == "queued@example.invalid"
        && window._bulkRegisterPolling && window._bulkRegisterAcceptedRequestId == acceptedClaruId
        && !window._bulkRegisterFollowingForeignRequest ? true : failure(__LINE__);
  }

  static bool claruCheckpointRecoverySmoke() {
    const auto failure = [](int line) { std::cerr << "Claru checkpoint recovery assertion failed at " << line << "\n"; return false; };
    auto* style = new oclero::qlementine::QlementineStyle;
    qApp->setStyle(style);
    MainWindow window(style, nullptr, false);
    QTcpServer server;
    if (!server.listen(QHostAddress::LocalHost, 0)) return failure(__LINE__);
    window._api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    window._backendReady = true;
    window._registrationProxiesReady = true;
    window._claruBatchAccounts = {QJsonObject{{"login_email", "queued@example.invalid"},
        {"minute_password", "fixture-minute-secret"}, {"claru_password", "fixture-claru-secret"}}};
    window.renderClaruBatch({});
    QString state = QStringLiteral("idle");
    QString statusRequestId;
    QString snapshotMode = QStringLiteral("invalid");
    QString checkpointStatus = QStringLiteral("created");
    int posts = 0, statusReads = 0;
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
        const QByteArray route = bytes->split('\n').first();
        QJsonObject out;
        QByteArray status = "200 OK";
        if (route.startsWith("GET /api/accounts/bulk-register/status ")) {
          ++statusReads;
          if (state == QStringLiteral("idle")) {
            out = {{"state", "idle"}, {"request_id", ""}, {"total", 0}, {"completed", 0},
                {"created", 0}, {"failed", 0}, {"results", QJsonArray{}}};
          } else if (snapshotMode == QStringLiteral("invalid")) {
            out = {{"state", "running"}, {"request_id", statusRequestId}, {"provider", "claru"},
                {"total", 1}, {"completed", 1}, {"created", 0}, {"failed", 0},
                {"results", QJsonArray{QJsonObject{{"email", "foreign@example.invalid"}}}}};
          } else {
            const QJsonObject savePartial{{"status", checkpointStatus}, {"detail", "Credencial protegida localmente"}};
            const QJsonObject result{{"email", "generated@supply.claru.ai"},
                {"claru_login", "queued@example.invalid"}, {"minute_email", "generated@supply.claru.ai"},
                {"created", false}, {"error", "Minute falhou após o checkpoint"}, {"partial", true},
                {"steps", QJsonObject{{"save_partial", savePartial},
                    {"minute_identity", QJsonObject{{"status", "fail"},
                        {"detail", "Estado de acesso não confirmado; senha fixture-claru-secret não é diagnóstico."},
                        {"code", "rate_limit"}, {"remote_effect_possible", true},
                        {"retry_after_seconds", 1800}, {"retry_at", 1791486000},
                        {"http_status", 429}, {"phase", "signup_submission"},
                        {"provider_error_code", "user_locked"}}}}}};
            out = {{"state", "done"}, {"request_id", statusRequestId}, {"provider", "claru"},
                {"total", 1}, {"completed", 1}, {"created", 0}, {"failed", 1},
                {"results", QJsonArray{result}}};
          }
        } else if (route.startsWith("POST /api/accounts/bulk-register ")) {
          ++posts;
          statusRequestId = QJsonDocument::fromJson(bytes->mid(end + 4, length)).object()
                                .value("request_id").toString();
          state = QStringLiteral("running");
          snapshotMode = QStringLiteral("invalid");
          status = "503 Service Unavailable";
          out = {{"ok", false}, {"error_code", "request_outcome_unknown"}, {"error", "fixture lost response"}};
        } else if (route.startsWith("GET /api/accounts/proxies ")) {
          out = {{"proxies", QJsonArray{}}};
        } else if (route.startsWith("GET /api/accounts/domains ")) {
          out = {{"domains", QJsonArray{QJsonObject{{"domain", "example.invalid"}}}}};
        } else if (route.startsWith("GET /api/accounts/bulk-register/preflight?")) {
          const QJsonObject checks{{"hostinger", QJsonObject{{"ok", true}}},
              {"chrome", QJsonObject{{"ok", true}}}, {"crowtado_api", QJsonObject{{"ok", true}}},
              {"minute_api", QJsonObject{{"ok", true}}}, {"invite_code", QJsonObject{{"ok", true}}}};
          out = {{"ready", true}, {"checks", checks}};
        } else out = {{"accounts", QJsonArray{}}};
        const auto data = QJsonDocument(out).toJson(QJsonDocument::Compact);
        socket->write("HTTP/1.1 " + status + "\r\nContent-Type: application/json\r\nContent-Length: "
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
    window.startClaruBatch(false);
    if (!until([&] { return posts == 1 && window._bulkRegisterAcceptedRequestId == statusRequestId
          && window._bulkRegisterStatus->text().contains("inconsistente"); })) return failure(__LINE__);
    const auto accountsBody = window._bulkRegisterPendingBody.value("accounts").toArray();
    if (posts != 1 || accountsBody.size() != 1
        || accountsBody[0].toObject().value("minute_password") != "fixture-minute-secret"
        || window._claruBatchAccounts.size() != 1
        || window._claruBatchAccounts[0].toObject().value("claru_password") != "fixture-claru-secret"
        || window._claruBatchTable->rowCount() != 1
        || window._claruBatchTable->item(0, 0)->text() != "queued@example.invalid") return failure(__LINE__);

    snapshotMode = QStringLiteral("checkpoint");
    window.pollBulkRegister();
    if (!until([&] { return window._bulkRegisterStatus->text().contains("Resultados inconsistentes"); })) return failure(__LINE__);
    const auto unconfirmedBody = window._bulkRegisterPendingBody.value("accounts").toArray();
    if (posts != 1 || !window._bulkRegisterPolling || unconfirmedBody.size() != 1
        || unconfirmedBody[0].toObject().value("minute_password") != "fixture-minute-secret"
        || window._claruBatchAccounts.size() != 1
        || window._claruBatchAccounts[0].toObject().value("claru_password") != "fixture-claru-secret"
        || window._claruBatchTable->rowCount() != 1
        || window._claruBatchTable->item(0, 0)->text() != "queued@example.invalid") return failure(__LINE__);
    checkpointStatus = QStringLiteral("ok");
    window.pollBulkRegister();
    if (!until([&] { return !window._bulkRegisterPolling && window._claruBatchAccounts.isEmpty(); })) return failure(__LINE__);
    return posts == 1 && statusReads >= 3 && window._bulkRegisterAcceptedRequestId.isEmpty()
        && window._bulkRegisterPendingBody.isEmpty() && window._claruBatchTable->rowCount() == 1
        && window._claruBatchTable->item(0, 0)->text() == "queued@example.invalid"
        && window._claruBatchTable->item(0, 2)->text().contains("checkpoint")
        && window._bulkRegisterTable->rowCount() == 1
        && window._bulkRegisterTable->item(0, 4)->text().contains("Identidade Minute")
        && window._bulkRegisterTable->item(0, 4)->toolTip().contains("user_locked")
        && window._bulkRegisterTable->item(0, 4)->toolTip().contains("HTTP 429")
        && window._bulkRegisterTable->item(0, 4)->toolTip().contains("[oculto]")
        && !window._bulkRegisterTable->item(0, 4)->toolTip().contains("fixture-claru-secret")
        && window._bulkRegisterTable->item(0, 4)->toolTip().contains("efeito remoto ainda não confirmado")
        && !window._bulkRegisterTable->item(0, 4)->text().contains("banida", Qt::CaseInsensitive)
            ? true : failure(__LINE__);
  }

  static bool registrationRateLimitSmoke() {
    const auto failure = [](int line) { std::cerr << "Registration rate-limit assertion failed at " << line << "\n"; return false; };
    auto* style = new oclero::qlementine::QlementineStyle;
    qApp->setStyle(style);
    MainWindow window(style, nullptr, false);
    QTcpServer server;
    if (!server.listen(QHostAddress::LocalHost, 0)) return failure(__LINE__);
    window._api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    window._backendReady = true;
    window._registrationProxiesReady = true;
    window._accountEmail->setText(QStringLiteral("limited@example.invalid"));
    window._accountPassword->setText(QStringLiteral("fixture-rate-limit-password"));
    window._bulkRegisterTable->setRowCount(1);
    window._bulkRegisterTable->setItem(0, 0, new QTableWidgetItem(QStringLiteral("prior-result")));
    int statusReads = 0;
    int posts = 0;
    QString requestId;
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
        const QByteArray route = bytes->split('\n').first();
        QJsonObject out;
        QByteArray httpStatus = "200 OK";
        if (route.startsWith("GET /api/accounts/bulk-register/status ")) {
          ++statusReads;
          out = {{"state", "idle"}, {"request_id", ""}, {"total", 0}, {"completed", 0},
              {"created", 0}, {"failed", 0}, {"results", QJsonArray{}}};
        } else if (route.startsWith("POST /api/accounts/register?async=1 ")) {
          ++posts;
          requestId = QJsonDocument::fromJson(bytes->mid(end + 4, length)).object()
                          .value("request_id").toString();
          httpStatus = "429 Too Many Requests";
          out = {{"error", "Limite de criação atingido. Aguarde 30 minutos antes de tentar novamente: fixture-rate-limit-password"},
              {"error_code", "rate_limit"}, {"retry_after_seconds", 1800}, {"not_admitted", true}};
        } else out = {{"accounts", QJsonArray{}}};
        const auto data = QJsonDocument(out).toJson(QJsonDocument::Compact);
        socket->write("HTTP/1.1 " + httpStatus + "\r\nContent-Type: application/json\r\nContent-Length: "
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
    completeRegistrationProfileDialog();
    window.addAccount(true);
    if (!until([&] { return !window._bulkRegisterStarting && !window._accountTransferBusy
          && window._bulkRegisterPendingRequestId.isEmpty(); })) return failure(__LINE__);
    const QString status = window._bulkRegisterStatus->text();
    return posts == 1 && statusReads == 1 && !requestId.isEmpty()
        && window._accountPassword->text() == QStringLiteral("fixture-rate-limit-password")
        && window._bulkRegisterTable->rowCount() == 1
        && window._bulkRegisterTable->item(0, 0)->text() == QStringLiteral("prior-result")
        && status.contains(QStringLiteral("Aguarde 30 minutos"), Qt::CaseInsensitive)
        && status.contains(QStringLiteral("HTTP 429"))
        && status.contains(QStringLiteral("[oculto]"))
        && !status.contains(QStringLiteral("fixture-rate-limit-password"))
        && !window._bulkRegisterPolling && window._bulkRegisterPendingBody.isEmpty()
        ? true : failure(__LINE__);
  }

  static bool claruConnectionGenerationSmoke() {
    const auto failure = [](int line) { std::cerr << "Claru connection generation assertion failed at " << line << "\n"; return false; };
    auto* style = new oclero::qlementine::QlementineStyle;
    qApp->setStyle(style);
    MainWindow window(style, nullptr, false);
    QTcpServer server;
    if (!server.listen(QHostAddress::LocalHost, 0)) return failure(__LINE__);
    window._api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    window._backendReady = true;
    QList<QTcpSocket*> heldConnections;
    int connectRequests = 0;
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
        const QByteArray route = bytes->split('\n').first();
        if (route.startsWith("POST /api/accounts/claru/connect ") || route.startsWith("POST /api/accounts ")) {
          if (route.startsWith("POST /api/accounts/claru/connect ")) ++connectRequests;
          heldConnections.append(socket);
          return;
        }
        const QByteArray data = route.startsWith("GET /api/accounts/bulk-register/status ")
            ? QJsonDocument(QJsonObject{{"state", "idle"}, {"request_id", ""}, {"total", 0},
                {"completed", 0}, {"created", 0}, {"failed", 0}, {"results", QJsonArray{}}})
                  .toJson(QJsonDocument::Compact)
            : QByteArray("{}");
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
    auto reply = [](QTcpSocket* socket, const QByteArray& status, const QByteArray& data) {
      socket->write("HTTP/1.1 " + status + "\r\nContent-Type: application/json\r\nContent-Length: "
          + QByteArray::number(data.size()) + "\r\nConnection: close\r\n\r\n" + data);
      socket->disconnectFromHost();
    };
    window._claruEmail->setText(QStringLiteral("old@example.invalid"));
    window._claruPassword->setText(QStringLiteral("old-claru-secret"));
    window.connectClaruAccount();
    if (!until([&] { return connectRequests == 1; })) return failure(__LINE__);
    window.setBackendReady(false, QStringLiteral("fixture restart"));
    window.setBackendReady(true);
    if (!until([&] { return !window._bulkRegisterPolling; })) return failure(__LINE__);
    window._claruEmail->setText(QStringLiteral("new@example.invalid"));
    window._claruPassword->setText(QStringLiteral("new-claru-secret"));
    window.connectClaruAccount();
    if (!until([&] { return connectRequests == 2 && heldConnections.size() == 2; })) return failure(__LINE__);
    const QByteArray oldResponse = QJsonDocument(QJsonObject{{"ok", true}, {"account", QJsonObject{
        {"login_email", "old@example.invalid"}, {"minute_email", "old@supply.claru.ai"}}}}).toJson(QJsonDocument::Compact);
    reply(heldConnections[0], "200 OK", oldResponse);
    qApp->processEvents();
    if (!window._accountTransferBusy || window._claruPassword->text() != QStringLiteral("new-claru-secret")
        || !window._claruMinuteEmail->text().isEmpty()) return failure(__LINE__);
    reply(heldConnections[1], "500 Internal Server Error", "not-json");
    if (!until([&] { return !window._accountTransferBusy; })) return failure(__LINE__);
    if (window._claruPassword->text() != QStringLiteral("new-claru-secret")
        || !window._claruMinuteEmail->text().isEmpty()) return failure(__LINE__);

    window._claruMinuteEmail->setText(QStringLiteral("target@supply.claru.ai"));
    window._claruMinutePassword->setText(QStringLiteral("old-minute-secret"));
    window.connectClaruMinute(false);
    if (!until([&] { return heldConnections.size() == 3; })) return failure(__LINE__);
    window.setBackendReady(false, QStringLiteral("fixture restart"));
    window.setBackendReady(true);
    if (!until([&] { return !window._bulkRegisterPolling; })) return failure(__LINE__);
    window._claruMinutePassword->setText(QStringLiteral("new-minute-secret"));
    window.connectClaruMinute(false);
    if (!until([&] { return heldConnections.size() == 4 && window._accountTransferBusy; })) return failure(__LINE__);
    reply(heldConnections[2], "200 OK", QByteArray("{\"ok\":true}"));
    qApp->processEvents();
    if (!window._accountTransferBusy || window._claruMinutePassword->text() != QStringLiteral("new-minute-secret"))
      return failure(__LINE__);
    reply(heldConnections[3], "500 Internal Server Error", QByteArray("{\"error\":\"fixture failure\"}"));
    if (!until([&] { return !window._accountTransferBusy; })) return failure(__LINE__);
    return window._claruMinutePassword->text() == QStringLiteral("new-minute-secret") ? true : failure(__LINE__);
  }

  static bool campaignStartRecoverySmoke() {
    const auto failure = [](int line) { std::cerr << "Campaign start recovery assertion failed at " << line << "\n"; return false; };
    QTcpServer server;
    if (!server.listen(QHostAddress::LocalHost, 0)) return failure(__LINE__);
    const QString identity = QStringLiteral("fixture-preflight-identity-88");
    int posts = 0, lookups = 0;
    int currentQueries = 0;
    bool recoveredRunning = false;
    bool markerAtPost = false;
    QString lookupMode = QStringLiteral("pending");
    QString postMode = QStringLiteral("uncertain");
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
        const QByteArray route = bytes->split('\n').first();
        QJsonObject out;
        QByteArray status = "200 OK";
        if (route.startsWith("POST /api/campaigns ")) {
          ++posts;
          const auto saved = QJsonDocument::fromJson(QSettings().value(QStringLiteral("campaign/pendingStart")).toByteArray()).object();
          markerAtPost = saved.value("start_request_id").toString() == identity;
          if (postMode == "mismatch")
            out = {{"ok", true}, {"start_request_id", "foreign-start"}, {"already_running", false}};
          else {
            status = "409 Conflict";
            out = {{"ok", false}, {"error_code", "start_state_unreadable"}, {"error", "preserve fixture"}};
          }
        } else if (route.startsWith("GET /api/campaigns/current?")) {
          ++currentQueries;
          out = {{"state", recoveredRunning ? "running" : "idle"},
                 {"start_request_id", recoveredRunning ? identity : "foreign-request"}, {"totals", QJsonObject{}}};
        } else if (route.startsWith("GET /api/campaigns/starts/")) {
          ++lookups;
          if (lookupMode == "malformed")
            out = {{"ok", true}, {"start_request_id", "wrong-id"}, {"found", true}, {"status", "admitted"},
                   {"may_start", false}, {"terminal", false}};
          else if (lookupMode == "terminal")
            out = {{"ok", true}, {"start_request_id", identity}, {"found", true}, {"status", "review"},
                   {"may_start", false}, {"terminal", true}, {"delivery_confirmed", false},
                   {"execution_state", "partial"}, {"log_name", "campaign_fixture.json"}};
          else
            out = {{"ok", true}, {"start_request_id", identity}, {"found", true}, {"status", "admitted"},
                   {"may_start", false}, {"terminal", false}, {"delivery_confirmed", false},
                   {"execution_state", "review"}, {"log_name", QJsonValue::Null}};
        } else out = {{"ok", true}};
        const auto data = QJsonDocument(out).toJson(QJsonDocument::Compact);
        socket->write("HTTP/1.1 " + status + "\r\nContent-Type: application/json\r\nContent-Length: "
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
    QTimer closeWarnings;
    QObject::connect(&closeWarnings, &QTimer::timeout, [] {
      if (auto* warning = qobject_cast<QMessageBox*>(qApp->activeModalWidget())) warning->accept();
    });
    closeWarnings.start(5);
    {
      auto* style = new oclero::qlementine::QlementineStyle;
      qApp->setStyle(style);
      MainWindow first(style, nullptr, false);
      first._backendReady = true;
      first._api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
      first.submitCampaign({{QStringLiteral("preflight_id"), identity}});
      if (!until([&] { return posts == 1 && lookups >= 1 && !first._campaignStartLookupInFlight; })) return failure(__LINE__);
      const auto saved = QJsonDocument::fromJson(QSettings().value(QStringLiteral("campaign/pendingStart")).toByteArray()).object();
      if (!markerAtPost || saved.value("start_request_id").toString() != identity
          || !first._campaignStartUncertain || first._campaignStart->isEnabled()) return failure(__LINE__);
      first._campaignPoll.stop();
    }
    lookupMode = QStringLiteral("malformed");
    auto* restoredStyle = new oclero::qlementine::QlementineStyle;
    qApp->setStyle(restoredStyle);
    MainWindow restored(restoredStyle, nullptr, false);
    restored._backendReady = true;
    restored._api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    auto* task = new QListWidgetItem(QStringLiteral("fixture task"), restored._campaignTasks);
    task->setData(Qt::UserRole, QStringLiteral("fixture-task"));
    task->setCheckState(Qt::Checked);
    restored.pollCampaign();
    if (!until([&] { return lookups >= 2 && !restored._campaignStartLookupInFlight; })) return failure(__LINE__);
    if (posts != 1 || !restored._campaignStartUncertain || restored._campaignStart->isEnabled()
        || QJsonDocument::fromJson(QSettings().value(QStringLiteral("campaign/pendingStart")).toByteArray())
             .object().value("start_request_id").toString() != identity) return failure(__LINE__);
    lookupMode = QStringLiteral("terminal");
    restored.lookupCampaignStart();
    if (!until([&] { return lookups >= 3 && !restored._campaignStartUncertain; })) return failure(__LINE__);
    closeWarnings.stop();
    if (posts != 1 || !QSettings().value(QStringLiteral("campaign/pendingStart")).toByteArray().isEmpty()
        || !restored._campaignStart->isEnabled()) return failure(__LINE__);
    lookupMode = QStringLiteral("malformed");
    postMode = QStringLiteral("mismatch");
    restored.submitCampaign({{QStringLiteral("preflight_id"), identity}});
    if (!until([&] { return posts == 2 && lookups >= 4 && !restored._campaignStartLookupInFlight; })) return failure(__LINE__);
    const auto unresolved = QJsonDocument::fromJson(QSettings().value(QStringLiteral("campaign/pendingStart")).toByteArray()).object();
    if (!(markerAtPost && unresolved.value("start_request_id").toString() == identity
        && restored._campaignStartUncertain && !restored._campaignStart->isEnabled()
        && posts == 2)) return failure(__LINE__);
    recoveredRunning = true;
    restored._campaignPoll.stop();
    restored._campaignPoll.setInterval(20);
    const int initialQueries = currentQueries;
    restored.pollCampaign();
    if (!until([&] { return currentQueries >= initialQueries + 2; })) return failure(__LINE__);
    const bool followsExecution = restored._campaignActive && restored._campaignPoll.isActive()
        && !restored._campaignStartUncertain && posts == 2;
    restored._campaignPoll.stop();
    if (!followsExecution) return failure(__LINE__);
    restored._backendReady = false;
    restored.pollCampaign();
    if (!until([&] { return currentQueries >= initialQueries + 3; })
        || restored._campaignPoll.isActive()) return failure(__LINE__);
    restored._backendReady = true;
    restored._closing = true;
    restored.pollCampaign();
    if (!until([&] { return currentQueries >= initialQueries + 4; })
        || restored._campaignPoll.isActive()) return failure(__LINE__);
    return true;
  }
