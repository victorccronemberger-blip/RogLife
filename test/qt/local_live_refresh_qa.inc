  static bool livePageRefreshSmoke() {
    QTcpServer server;
    if (!server.listen(QHostAddress::LocalHost, 0)) return false;
    int done = 1, logCount = 1, reads = 0, writes = 0;
    bool readinessFail = false;
    QObject::connect(&server, &QTcpServer::newConnection, &server, [&] {
      while (server.hasPendingConnections()) {
        auto* socket = server.nextPendingConnection();
        QObject::connect(socket, &QTcpSocket::readyRead, socket, [&, socket] {
          const auto bytes = socket->property("bytes").toByteArray() + socket->readAll();
          socket->setProperty("bytes", bytes);
          if (!bytes.contains("\r\n\r\n") || socket->property("answered").toBool()) return;
          socket->setProperty("answered", true);
          const auto request = bytes.left(bytes.indexOf("\r\n")).split(' ');
          if (request.value(0) != "GET") ++writes;
          ++reads;
          const auto path = request.value(1).split('?').first();
          QJsonObject body;
          int status = 200;
          if (path == "/api/accounts") body = {{"accounts", QJsonArray{"fixture@example.test"}}};
          else if (path == "/api/logs") {
            QJsonArray logs;
            for (int i = 0; i < logCount; ++i) logs.append(QJsonObject{{"name", QString::number(i)},
                {"items", 1}, {"ok", done}, {"sends", 10}, {"accounts", QJsonArray{}}});
            body = {{"logs", logs}};
          } else if (path == "/api/campaigns/current") body = {{"state", "running"},
              {"current", "Fixture em execução"}, {"totals", QJsonObject{{"done_sends", done}, {"total_sends", 10}}}};
          else if (path == "/api/readiness") {
            status = readinessFail ? 503 : 200;
            body = readinessFail ? QJsonObject{{"error", "fixture temporariamente indisponível"}}
                : QJsonObject{{"ready", true}, {"checks", QJsonArray{QJsonObject{{"status", "ok"}, {"name", "Fixture"}}}}};
          }
          const auto json = QJsonDocument(body).toJson(QJsonDocument::Compact);
          socket->write("HTTP/1.1 " + QByteArray::number(status) + " OK\r\nContent-Type: application/json\r\nContent-Length: "
              + QByteArray::number(json.size()) + "\r\nConnection: close\r\n\r\n" + json);
          socket->disconnectFromHost();
        });
      }
    });
    auto until = [](const std::function<bool()>& predicate) {
      QElapsedTimer timer; timer.start();
      while (!predicate() && timer.elapsed() < 2500) { qApp->processEvents(); QThread::msleep(5); }
      return predicate();
    };
    auto fail = [](int line) { std::cerr << "Live refresh regression at " << line << '\n'; return false; };
    MainWindow window(nullptr, nullptr, false);
    window._api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    window._backendReady = true;
    window._livePagePoll.setInterval(40);
    window.show();
    window.navigate(0);
    if (!until([&] { return window._homePulseProgress->value() == 10; })) return fail(__LINE__);
    done = 7;
    if (!until([&] { return window._homePulseProgress->value() == 70; })) return fail(__LINE__);
    if (window._homePulseBody->text() != QStringLiteral("Fixture em execução")) return fail(__LINE__);
    window.navigate(7);
    if (!until([&] { return window._historyTable->rowCount() == 1; })) return fail(__LINE__);
    window._historyTable->setCurrentCell(0, 0);
    logCount = 2;
    if (!until([&] { return window._historyTable->rowCount() == 2; })) return fail(__LINE__);
    if (window._historyTable->currentRow() != 0) return fail(__LINE__);
    window.navigate(1);
    if (!until([&] { return window._readinessHeadline->text() == QStringLiteral("Operação liberada"); })) return fail(__LINE__);
    readinessFail = true;
    window._readinessRefreshAge.invalidate();
    if (!until([&] { return window._readinessSummary->text().contains(QStringLiteral("Dados anteriores")); })) return fail(__LINE__);
    if (qApp->activeModalWidget()) return fail(__LINE__);
    window.hide();
    if (!until([&] { return !window._readinessReadPending; })) return fail(__LINE__);
    const int before = reads;
    window.refreshLivePage(); qApp->processEvents();
    if (reads != before || writes) return fail(__LINE__);
    window._backendReady = false;
    window.show(); window.refreshLivePage(); qApp->processEvents();
    return reads == before && writes == 0;
  }
