  static bool campaignVerificationMonitorSmoke() {
    auto* style = new oclero::qlementine::QlementineStyle;
    MainWindow window(style, nullptr, false);
    window._campaignVerifying = true;
    window._campaignVerificationClock.start();
    window._campaignVerificationPanel->show();
    window._campaignVerificationWatch.start();
    window.updateCampaignVerificationMonitor(QJsonObject{{"message", "Validando categorias"},
        {"completed", 2}, {"total", 4}});
    const bool measured = window._campaignVerificationProgress->value() == 50
        && window._campaignVerificationStatus->text().contains("50% desta etapa")
        && window._campaignVerificationStatus->text().contains("nenhum envio iniciado");
    window.updateCampaignVerificationMonitor(QJsonObject{{"message", "Conferindo acesso"}});
    const bool unknown = window._campaignVerificationProgress->maximum() == 0
        && !window._campaignVerificationStatus->text().contains("50%");
    window.onCampaignBackendAvailabilityChanged(false, true);
    return measured && unknown && !window._campaignVerificationWatch.isActive()
        && window._campaignVerificationPanel->isHidden() && !window._campaignVerifying;
  }

  static bool providerCatalogPageSmoke() {
    QTcpServer server;
    if (!server.listen(QHostAddress::LocalHost, 0)) return false;
    int posts = 0, gets = 0;
    bool identity = true, measured = false;
    QObject::connect(&server, &QTcpServer::newConnection, &server, [&] {
      auto* socket = server.nextPendingConnection();
      QObject::connect(socket, &QTcpSocket::readyRead, socket, [&, socket] {
        auto bytes = socket->property("input").toByteArray() + socket->readAll();
        socket->setProperty("input", bytes);
        if (!bytes.contains("\r\n\r\n") || socket->property("answered").toBool()) return;
        socket->setProperty("answered", true);
        const bool post = bytes.startsWith("POST ");
        if (post) { ++posts; identity &= bytes.startsWith("POST /api/catalogs/providers/prepare?"); }
        else { ++gets; identity &= bytes.startsWith("GET /api/catalogs/providers?") && bytes.contains("job_id=prepared-fixture"); }
        identity &= bytes.contains("dataset=ambos") && bytes.contains("min_dur_s=60") && bytes.contains("max_dur_s=1800");
        const bool loading = post || gets < 2;
        const auto data = QJsonDocument(loading
            ? QJsonObject{{"loading",true},{"state","running"},{"job_id","prepared-fixture"},
                {"phase","provider_summary"},{"message","Fixture index 2/4"},
                {"completed",2},{"total",4},{"elapsed_s",10},{"idle_s",0}}
            : QJsonObject{{"ready",true},{"message","Prepared fixture"},{"providers",QJsonArray{
                QJsonObject{{"provider","ego4d"},{"candidates",4},{"rules_with_candidates",2},
                    {"rules_checked",4},{"annotation_hours_upper_bound",1.5}}}}}).toJson(QJsonDocument::Compact);
        socket->write("HTTP/1.1 " + QByteArray(loading ? "202 Accepted" : "200 OK")
            + "\r\nContent-Type: application/json\r\nContent-Length: " + QByteArray::number(data.size())
            + "\r\nConnection: close\r\n\r\n" + data);
        socket->disconnectFromHost();
      });
      QObject::connect(socket, &QTcpSocket::disconnected, socket, &QObject::deleteLater);
    });
    auto* style = new oclero::qlementine::QlementineStyle;
    MainWindow window(style, nullptr, false);
    window._api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    window._api._pollDelayMs = 60;
    window._backendReady = true;
    window.onProviderCatalogBackendAvailabilityChanged(true, true);
    QEventLoop loop; QTimer check;
    QObject::connect(&check, &QTimer::timeout, &loop, [&] {
      measured |= window._providerCatalogPercent->text() == "50%"
          && window._providerCatalogProgress->value() == 50
          && !window._providerCatalogPrepare->isEnabled();
      if (!window._providerCatalogBusy) loop.quit();
    });
    window.prepareProviderCatalog(); check.start(10);
    QTimer::singleShot(2500, &loop, &QEventLoop::quit); loop.exec();
    const bool completed = !window._providerCatalogBusy
        && window._providerCatalogPercent->text() == "100%"
        && window._providerCatalogSummary->toPlainText().contains("4 candidatos")
        && window._providerCatalogPrepare->isEnabled();
    window._providerCatalogBusy = true;
    window.onProviderCatalogBackendAvailabilityChanged(false, true);
    const bool interrupted = !window._providerCatalogBusy && !window._providerCatalogPrepare->isEnabled();
    window.onProviderCatalogBackendAvailabilityChanged(true, true);
    return measured && completed && interrupted && window._providerCatalogPrepare->isEnabled()
        && identity && posts == 1 && gets == 2 && window._navigation->item(9)->text() == QStringLiteral("Catálogos");
  }

  static bool catalogMonitorSmoke() {
    QTcpServer server;
    if (!server.listen(QHostAddress::LocalHost, 0)) return false;
    int calls = 0;
    QObject::connect(&server, &QTcpServer::newConnection, &server, [&] {
      auto* socket = server.nextPendingConnection();
      QObject::connect(socket, &QTcpSocket::readyRead, socket, [&, socket] {
        auto bytes = socket->property("input").toByteArray() + socket->readAll();
        socket->setProperty("input", bytes);
        if (!bytes.contains("\r\n\r\n") || socket->property("answered").toBool()) return;
        socket->setProperty("answered", true);
        ++calls;
        const bool failed = bytes.contains("refresh=1");
        const bool loading = !failed && calls < 6;
        QJsonObject data = failed ? QJsonObject{{"error", "fixture catalog failure"}}
            : loading ? QJsonObject{{"loading", true}, {"job_id", "fixture-monitor"},
                {"message", "Conferindo categoria 2/4"}, {"phase", "campaign_categories"},
                {"completed", 1}, {"total", 4}, {"elapsed_s", 40}, {"idle_s", 45}}
            : QJsonObject{{"tasks", QJsonArray{QJsonObject{{"id", "garden"}, {"name", "Gardening"},
                {"clip_count", 1}, {"available_for_duration", true}}}}};
        const auto body = QJsonDocument(data).toJson(QJsonDocument::Compact);
        const auto response = "HTTP/1.1 " + QByteArray(failed ? "400 Error" : loading ? "202 Accepted" : "200 OK")
            + "\r\nContent-Type: application/json\r\nContent-Length: " + QByteArray::number(body.size())
            + "\r\nConnection: close\r\n\r\n" + body;
        QTimer::singleShot(calls == 2 ? 1800 : 0, socket, [socket, response] {
          socket->write(response); socket->disconnectFromHost();
        });
      });
      QObject::connect(socket, &QTcpSocket::disconnected, socket, &QObject::deleteLater);
    });
    auto* style = new oclero::qlementine::QlementineStyle;
    MainWindow window(style, nullptr, false);
    window._api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    {
      const QSignalBlocker blocker(window._campaignAccounts);
      window._campaignAccounts->clear();
      auto* account = new QListWidgetItem("fixture@example.invalid", window._campaignAccounts);
      account->setData(Qt::UserRole, "fixture@example.invalid"); account->setCheckState(Qt::Checked);
    }
    bool measured = false, heartbeat = false, success = false, failure = false;
    QEventLoop loop;
    QTimer check;
    QObject::connect(&check, &QTimer::timeout, &loop, [&] {
      const QString status = window._taskCatalogStatus->text();
      if (window._taskCatalogWatch.isActive() && window._taskCatalogProgress->value() == 25) {
        measured |= status.contains("40 s") && status.contains(QStringLiteral("Sem avanço"))
            && !window._campaignStart->isEnabled();
        heartbeat |= status.contains("41 s") || status.contains("42 s");
      }
      if (!success && window._taskCatalogProgress->value() == 100) {
        success = !window._taskCatalogWatch.isActive() && status.contains(QStringLiteral("concluído"));
        window._taskCatalogForceRefresh = true; window.loadTasks();
      } else if (success && status.contains("fixture catalog failure")) {
        failure = !window._taskCatalogWatch.isActive() && !window._campaignStart->isEnabled();
        loop.quit();
      }
    });
    check.start(25); window.loadTasks();
    QTimer::singleShot(8000, &loop, &QEventLoop::quit);
    loop.exec();
    return measured && heartbeat && success && failure && calls == 7;
  }

  static bool catalogLatestSelection() {
    QTcpServer server;
    if (!server.listen(QHostAddress::LocalHost, 0)) return false;
    int oldRequests = 0, newRequests = 0, staleCallbacks = 0;
    QObject::connect(&server, &QTcpServer::newConnection, &server, [&] {
      auto* socket = server.nextPendingConnection();
      QObject::connect(socket, &QTcpSocket::readyRead, socket, [&, socket] {
        auto bytes = socket->property("input").toByteArray() + socket->readAll();
        socket->setProperty("input", bytes);
        if (!bytes.contains("\r\n\r\n") || socket->property("answered").toBool()) return;
        socket->setProperty("answered", true);
        const bool old = bytes.contains("email=old");
        old ? ++oldRequests : ++newRequests;
        const auto body = QJsonDocument(old
            ? QJsonObject{{"loading", true}, {"job_id", "obsolete"}, {"message", "old selection"}}
            : QJsonObject{{"tasks", QJsonArray{}}}).toJson(QJsonDocument::Compact);
        socket->write("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + QByteArray::number(body.size()) + "\r\nConnection: close\r\n\r\n" + body);
        socket->disconnectFromHost();
      });
      QObject::connect(socket, &QTcpSocket::disconnected, socket, &QObject::deleteLater);
    });
    ApiClient api; api._pollDelayMs = 10;
    api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    QEventLoop loop;
    bool finished = false;
    api.get("/api/tasks?email=old", [&](bool, const QJsonDocument&, const QString&) { ++staleCallbacks; },
        [&](const QJsonObject&) {
          api.get("/api/tasks?email=new", [&](bool ok, const QJsonDocument&, const QString&) {
            finished = ok; QTimer::singleShot(60, &loop, &QEventLoop::quit);
          });
        });
    QTimer::singleShot(1000, &loop, &QEventLoop::quit); loop.exec();
    return finished && !staleCallbacks && oldRequests == 1 && newRequests == 1;
  }

  static bool catalogProgressProtocol(bool preflight, bool exhaust, bool idle) {
    QTcpServer server;
    if (!server.listen(QHostAddress::LocalHost, 0)) return false;
    int calls = 0, updates = 0;
    QList<QByteArray> requests;
    bool identity = true;
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
        *answered = true; ++calls; requests.append(*bytes);
        if (preflight) {
          const QByteArray firstLine = bytes->left(bytes->indexOf('\n')).trimmed();
          identity &= firstLine.contains("POST /api/campaigns/preflight?async=1&request_id=")
              && QRegularExpression(QStringLiteral("request_id=[0-9a-f]{32}")).match(QString::fromLatin1(firstLine)).hasMatch();
        }
        if (preflight && calls > 1)
          identity &= requests[0].split('\n')[0] == requests.last().split('\n')[0]
              && requests[0].mid(requests[0].indexOf("\r\n\r\n") + 4) == bytes->mid(end + 4);
        if (!preflight && calls > 1) identity &= bytes->contains("job_id=fixture-job");
        if (preflight && !exhaust && calls == 2) { socket->abort(); return; }
        QJsonObject result;
        QByteArray code = "200 OK";
        if (preflight && (exhaust || calls == 1)) {
          code = "504 Gateway Timeout";
          result = {{"loading",true},{"code","catalog_work_pending"},{"error","still checking"}};
        } else if (idle || calls < (preflight ? 4 : 301)) {
          result = {{"loading",true},{"job_id","fixture-job"},{"phase","nymeria_annotations"},
                    {"message",idle ? QStringLiteral("Classificando 1/1100")
                                    : QStringLiteral("Classificando %1/1100").arg(calls)},
                    {"elapsed_s", calls}};
        } else result = {{"ok",true},{"tasks",QJsonArray{}}};
        const auto data = QJsonDocument(result).toJson(QJsonDocument::Compact);
        socket->write("HTTP/1.1 " + code + "\r\nContent-Type: application/json\r\nContent-Length: "
                      + QByteArray::number(data.size()) + "\r\nConnection: close\r\n\r\n" + data);
        socket->disconnectFromHost();
      });
      QObject::connect(socket, &QTcpSocket::disconnected, socket, &QObject::deleteLater);
    });
    ApiClient api; api._pollDelayMs = 0;
    api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    QEventLoop loop;
    bool completed = false;
    ApiClient::Callback done = [&](bool ok, const QJsonDocument& doc, const QString&) {
      completed = exhaust || idle ? !ok : ok && doc.object().value("ok") == QJsonValue(true);
      loop.quit();
    };
    ApiClient::ProgressCallback progress = [&](const QJsonObject&) { ++updates; };
    if (preflight) api.post("/api/campaigns/preflight?async=1&request_id=0123456789abcdef0123456789abcdef",
                            {{"fixture",true}}, done, progress);
    else api.get("/api/tasks?email=fixture%40example.invalid", done, progress);
    QTimer::singleShot(10000, &loop, &QEventLoop::quit);
    loop.exec();
    return completed && identity && calls == (idle ? 600 : exhaust ? 5 : preflight ? 4 : 301)
        && (preflight || updates == (idle ? 600 : 300));
  }

  static bool catalogLimits() {
    return !ApiClient::pollLimitReached(251, 1, 400000, 1000)
        && ApiClient::pollLimitReached(251, 1, 400000, 300001)
        && ApiClient::pollLimitReached(1, 1, 1800001, 1)
        && !ApiClient::pollLimitReached(3000, 1, 1700000, 1);
  }
