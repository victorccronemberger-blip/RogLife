  static bool providerCatalogLiveStatus() {
    QTcpServer server;
    if (!server.listen(QHostAddress::LocalHost, 0)) return false;
    int mode = 0, posts = 0, reads = 0, recoveryReads = 0, unacceptedReads = 0, uncertainReads = 0;
    bool indexReady = false, delayOldSelection = false, identity = true;
    QObject::connect(&server, &QTcpServer::newConnection, &server, [&] {
      auto* socket = server.nextPendingConnection();
      QObject::connect(socket, &QTcpSocket::readyRead, socket, [&, socket] {
        const auto bytes = socket->property("input").toByteArray() + socket->readAll();
        socket->setProperty("input", bytes);
        if (!bytes.contains("\r\n\r\n") || socket->property("answered").toBool()) return;
        socket->setProperty("answered", true);
        const auto line = bytes.left(bytes.indexOf("\r\n")).split(' ');
        const QUrl url(QString::fromUtf8(line.value(1)));
        const QUrlQuery query(url);
        const bool post = line.value(0) == "POST";
        post ? ++posts : ++reads;
        identity &= query.hasQueryItem("dataset") && query.hasQueryItem("min_dur_s")
            && query.hasQueryItem("max_dur_s");
        const auto dataset = query.queryItemValue("dataset");
        QJsonObject result{{"state", "idle"},
            {"message", QStringLiteral("Fixture %1/%2").arg(dataset, query.queryItemValue("min_dur_s"))},
            {"indexes_found", QJsonObject{{"ego4d", indexReady}, {"nymeria", indexReady}}}};
        QByteArray status = "200 OK";
        int delay = delayOldSelection && dataset == "ego4d" ? 350 : 0;
        if (post && mode == 1) result = {{"ready", false}, {"error", "Fixture: preparação não confirmada"}};
        if (mode == 2) {
          identity &= post || query.queryItemValue("job_id") == "fixture-recover";
          if (!post) ++recoveryReads;
          if (recoveryReads == 2) { socket->abort(); return; }
          if (recoveryReads < 4) {
            status = recoveryReads == 1 ? "504 Gateway Timeout" : "202 Accepted";
            result = {{"loading", true}, {"state", "running"}, {"job_id", "fixture-recover"},
                {"phase", "nymeria_source_sync"}, {"message", "Fixture: obtendo anotações"},
                {"completed", recoveryReads == 3 ? 825 : 550}, {"total", 1100}, {"elapsed_s", 40}, {"idle_s", 0}};
            if (recoveryReads == 1) {
              result.insert("code", "catalog_work_pending");
              result.insert("error", "Fixture: worker continua ativo");
            }
            if (recoveryReads == 3) delay = 1350;
          } else {
            mode = 0;
            const QJsonObject sources{{"completed", 1100}, {"total", 1100}, {"cataloged", 845},
                {"no_annotations", 255}, {"invalid_annotations", 0}, {"download_failed", 0}, {"errors", 0}};
            const QJsonObject provider{{"provider", "nymeria"}, {"candidates", 4},
                {"rules_with_candidates", 2}, {"rules_checked", 4}, {"sources", sources}};
            result = {{"ready", true}, {"message", "Fixture: anotações preparadas"},
                {"providers", QJsonArray{provider}}};
          }
        }
        if (post && mode == 3) {
          status = "409 Conflict";
          const QJsonObject sources{{"completed", 1100}, {"total", 1100}, {"cataloged", 845},
              {"no_annotations", 233}, {"invalid_annotations", 0}, {"download_failed", 22}, {"errors", 22}};
          const QJsonObject provider{{"provider", "nymeria"}, {"error", "Fixture: fonte indisponível"},
              {"candidates", 678}, {"rules_with_candidates", 7}, {"rules_checked", 10},
              {"annotation_hours_upper_bound", 45}, {"state", "partial"}, {"sources", sources}};
          const QJsonObject ego{{"provider", "ego4d"}, {"candidates", 1236},
              {"rules_with_candidates", 20}, {"rules_checked", 25}, {"state", "ready"}};
          result = {{"ready", false}, {"state", "partial"}, {"error", "Fixture: anotações incompletas"},
              {"providers", QJsonArray{ego, provider}}};
        }
        if (mode == 4) {
          identity &= !query.hasQueryItem("job_id");
          if (post) {
            status = "202 Accepted";
            result = {{"loading", true}, {"state", "queued"}, {"message", "Fixture: outro catálogo ocupado"}};
          } else ++unacceptedReads;
        }
        if (mode == 5 || mode == 6) {
          identity &= !query.hasQueryItem("job_id");
          if (post) {
            if (mode == 5) status = "503 Service Unavailable";
            result = {{"error", "Fixture: resposta interrompida após aceitar trabalho"}};
          } else {
            ++uncertainReads;
            result = {{"ready", true}, {"message", "Fixture: preparação recuperada por consulta"}};
          }
        }
        const auto body = post && mode == 6 ? QByteArray("{\"ready\":")
            : QJsonDocument(result).toJson(QJsonDocument::Compact);
        const auto response = "HTTP/1.1 " + status
            + "\r\nContent-Type: application/json\r\nContent-Length: " + QByteArray::number(body.size())
            + "\r\nConnection: close\r\n\r\n" + body;
        QTimer::singleShot(delay, socket, [socket, response] {
          socket->write(response); socket->disconnectFromHost();
        });
      });
      QObject::connect(socket, &QTcpSocket::disconnected, socket, &QObject::deleteLater);
    });
    auto* style = new oclero::qlementine::QlementineStyle;
    MainWindow window(style, nullptr, false);
    window._api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    window._api._pollDelayMs = 40;
    window._backendReady = true;
    window._pages->setCurrentIndex(9);
    window.onProviderCatalogPageActivated(9);
    window._providerCatalogWatch.setInterval(50);
    auto await = [&](const std::function<bool()>& predicate, int timeout = 3000) {
      QEventLoop loop;
      QTimer check;
      QObject::connect(&check, &QTimer::timeout, &loop, [&] { if (predicate()) loop.quit(); });
      check.start(10);
      QTimer::singleShot(timeout, &loop, &QEventLoop::quit);
      loop.exec();
      return predicate();
    };
    if (!await([&] { return window._providerCatalogSummary->toPlainText().contains("a preparar"); })) return false;
    indexReady = true;
    if (!await([&] { return window._providerCatalogSummary->toPlainText().contains("encontrado"); })) return false;
    delayOldSelection = true;
    window._providerCatalogMinimum->setValue(2);
    window._providerCatalogDataset->setCurrentIndex(window._providerCatalogDataset->findData("ego4d"));
    window._providerCatalogDataset->setCurrentIndex(window._providerCatalogDataset->findData("nymeria"));
    if (!await([&] { return window._providerCatalogStatus->text().contains("Fixture nymeria/120"); })) return false;
    QEventLoop staleWait;
    QTimer::singleShot(450, &staleWait, &QEventLoop::quit);
    staleWait.exec();
    if (!window._providerCatalogStatus->text().contains("Fixture nymeria/120")
        || window._providerCatalogSummary->toPlainText().contains("Ego4D")) return false;
    mode = 1;
    window.prepareProviderCatalog();
    if (!await([&] { return !window._providerCatalogBusy
        && window._providerCatalogStatus->text().contains("não confirmada"); })) return false;
    if (window._providerCatalogPercent->text() != "Falha") return false;
    // A passive inventory response must keep a preparation failure visible.
    const int beforeReads = reads;
    if (!await([&] { return reads > beforeReads + 1; })) return false;
    if (!window._providerCatalogStatus->text().contains("não confirmada")) return false;
    mode = 2;
    bool heartbeat = false, measured = false;
    QTimer observe;
    QObject::connect(&observe, &QTimer::timeout, &observe, [&] {
      heartbeat |= window._providerCatalogBusy && window._providerCatalogStatus->text().contains("Tempo: 41 s");
      measured |= window._providerCatalogProgress->value() == 75;
    });
    observe.start(10);
    window.prepareProviderCatalog();
    if (!await([&] { return !window._providerCatalogBusy
        && window._providerCatalogPercent->text() == "100%"; }, 6000)) return false;
    mode = 0;
    observe.stop();
    if (!heartbeat || !measured || posts != 2 || recoveryReads != 4
        || !window._providerCatalogSummary->toPlainText().contains("1100/1100 fontes")
        || !window._providerCatalogSummary->toPlainText().contains("845 disponíveis")
        || !window._providerCatalogSummary->toPlainText().contains("255 sem ações atômicas")
        || !window._providerCatalogSummary->toPlainText().contains("0 inválidas")
        || !window._providerCatalogSummary->toPlainText().contains("0 falhas de aquisição")) return false;
    // A removed index invalidates current readiness, even after a prior success.
    indexReady = false;
    if (!await([&] { return window._providerCatalogPercent->text() == "A preparar"; })) return false;
    if (!window._providerCatalogStatus->text().contains("não está mais disponível")
        || window._providerCatalogSummary->toPlainText().contains("Anotações e índices disponíveis")) return false;
    mode = 3;
    window._providerCatalogDataset->setCurrentIndex(window._providerCatalogDataset->findData("ambos"));
    window.prepareProviderCatalog();
    if (!await([&] { return !window._providerCatalogBusy
        && window._providerCatalogSummary->toPlainText().contains("fonte indisponível"); })) return false;
    const bool partial = window._providerCatalogSummary->toPlainText().contains("22 falhas de aquisição")
        && window._providerCatalogSummary->toPlainText().contains("233 sem ações atômicas")
        && window._providerCatalogSummary->toPlainText().contains("0 inválidas")
        && window._providerCatalogSummary->toPlainText().contains("678 candidatos")
        && window._providerCatalogSummary->toPlainText().contains("7/10 regras")
        && window._providerCatalogSummary->toPlainText().contains("1236 candidatos")
        && window._providerCatalogStatus->text().contains("anotações incompletas")
        && !window._providerCatalogStatus->text().contains("HTTP 409")
        && window._providerCatalogPercent->text() == "Parcial"
        && window._providerCatalogProgress->value() != 100;
    indexReady = true;
    if (!await([&] { return window._providerCatalogSummary->toPlainText().contains("Índice NymeriaPlus: encontrado"); })) return false;
    if (window._providerCatalogPercent->text() != "Parcial"
        || !window._providerCatalogSummary->toPlainText().contains("678 candidatos")
        || window._providerCatalogSummary->toPlainText().contains("Anotações e índices disponíveis")) return false;
    // A full queue has not accepted a job. Observe with GET; never repeat POST.
    mode = 4;
    window.prepareProviderCatalog();
    if (!await([&] { return !window._providerCatalogBusy && unacceptedReads > 0; })) return false;
    const bool notAccepted = window._providerCatalogPercent->text() == "Falha"
        && window._providerCatalogStatus->text().contains("não confirmou");
    // Unknown acceptance (5xx or truncated JSON) remains observable while the
    // page is hidden. Recovery is a GET of the selection, never a second POST.
    for (const int interruptedMode : {5, 6}) {
      mode = interruptedMode;
      window._pages->setCurrentIndex(9);
      window.onProviderCatalogPageActivated(9);
      window.prepareProviderCatalog();
      window._pages->setCurrentIndex(0);
      window.onProviderCatalogPageActivated(0);
      if (!await([&] { return !window._providerCatalogBusy
          && window._providerCatalogStatus->text().contains("recuperada por consulta"); })) return false;
      if (window._providerCatalogWatch.isActive()) return false;
    }
    window._pages->setCurrentIndex(0);
    window.onProviderCatalogPageActivated(0);
    const bool stopped = !window._providerCatalogWatch.isActive();
    window.shutdownProviderCatalogTimers();
    const QJsonObject legacySources{{"completed", 4}, {"total", 4}, {"cataloged", 3}, {"errors", 1}};
    const QJsonObject legacyProvider{{"provider", "nymeria"}, {"sources", legacySources}, {"error", "Fixture: falha legada"}};
    window.renderProviderCatalogReply(false, QJsonDocument(QJsonObject{{"ready", false}, {"state", "partial"},
        {"error", "Fixture: parcial legado"}, {"providers", QJsonArray{legacyProvider}}}), "Fixture (HTTP 409)", true);
    const bool legacy = window._providerCatalogSummary->toPlainText().contains("1 falhas")
        && !window._providerCatalogSummary->toPlainText().contains("inválidas")
        && !window._providerCatalogSummary->toPlainText().contains("ações atômicas")
        && !window._providerCatalogSummary->toPlainText().contains("0 candidatos");
    return identity && partial && notAccepted && stopped && legacy && posts == 6 && uncertainReads == 2
        && providerCatalogCampaignHandoff();
  }

  static bool providerCatalogCampaignHandoff() {
    QTcpServer server;
    if (!server.listen(QHostAddress::LocalHost, 0)) return false;
    int tasks = 0, writes = 0;
    bool identity = true;
    QObject::connect(&server, &QTcpServer::newConnection, &server, [&] {
      auto* socket = server.nextPendingConnection();
      QObject::connect(socket, &QTcpSocket::readyRead, socket, [&, socket] {
        const auto bytes = socket->property("input").toByteArray() + socket->readAll();
        socket->setProperty("input", bytes);
        if (!bytes.contains("\r\n\r\n") || socket->property("answered").toBool()) return;
        socket->setProperty("answered", true);
        const auto line = bytes.left(bytes.indexOf("\r\n")).split(' ');
        const QUrl url(QString::fromUtf8(line.value(1)));
        const QUrlQuery query(url);
        if (line.value(0) != "GET") ++writes;
        int delay = 0;
        QJsonObject result{{"state", "idle"}};
        if (url.path() == "/api/tasks") {
          ++tasks;
          identity &= query.queryItemValue("dataset") == "ego4d"
              && query.queryItemValue("min_dur_s") == "60" && query.queryItemValue("max_dur_s") == "120";
          const QJsonObject garden{{"id", tasks == 1 ? "obsolete" : "garden"},
              {"name", "Gardening"}, {"clip_count", 3}, {"available_for_duration", true}};
          const QJsonObject repair{{"id", "repair"}, {"name", "Repair"},
              {"clip_count", 2}, {"available_for_duration", true}};
          result = {{"tasks", QJsonArray{garden, repair}}};
          if (tasks == 1) delay = 800;
        } else if (url.path() == "/api/accounts") {
          result = {{"accounts", QJsonArray{QJsonObject{{"email", "fixture@example.invalid"}}}}};
        } else if (url.path() == "/api/catalogs/providers") {
          result = {{"state", "idle"}, {"indexes_found", QJsonObject{{"ego4d", true}, {"nymeria", true}}}};
        }
        const auto body = QJsonDocument(result).toJson(QJsonDocument::Compact);
        const auto response = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + QByteArray::number(body.size()) + "\r\nConnection: close\r\n\r\n" + body;
        QTimer::singleShot(delay, socket, [socket, response] { socket->write(response); socket->disconnectFromHost(); });
      });
      QObject::connect(socket, &QTcpSocket::disconnected, socket, &QObject::deleteLater);
    });
    auto* style = new oclero::qlementine::QlementineStyle;
    MainWindow window(style, nullptr, false);
    window._api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    {
      const QSignalBlocker datasetBlocker(window._dataset), minBlocker(window._minDuration), maxBlocker(window._maxDuration);
      window._dataset->setCurrentIndex(window._dataset->findData("ego4d"));
      window._minDuration->setValue(1);
      window._maxDuration->setValue(2);
    }
    auto* open = window.findChild<QPushButton*>("openProviderCatalogs");
    if (!open) return false;
    open->click();
    if (window._navigation->currentRow() != 9 || window._providerCatalogDataset->currentData() != "ego4d"
        || window._providerCatalogMinimum->value() != 1 || window._providerCatalogMaximum->value() != 2) return false;
    window._providerCatalogBusy = true;
    window._providerCatalogJobId = "in-flight";
    const auto revision = window._providerCatalogRevision;
    window.openProviderCatalogsForCampaign("nymeria", 10, 12);
    if (window._providerCatalogDataset->currentData() != "ego4d" || window._providerCatalogMaximum->value() != 2
        || window._providerCatalogJobId != "in-flight" || window._providerCatalogRevision != revision) return false;
    window._providerCatalogBusy = false;
    window._providerCatalogJobId.clear();
    window._backendReady = true;
    window._pages->setCurrentIndex(3);
    {
      const QSignalBlocker accountBlocker(window._campaignAccounts), modeBlocker(window._campaignAccountMode);
      window._campaignAccountMode->setCurrentIndex(window._campaignAccountMode->findData("manual"));
      window._campaignAccounts->clear();
      auto* account = new QListWidgetItem("fixture@example.invalid", window._campaignAccounts);
      account->setData(Qt::UserRole, "fixture@example.invalid");
      account->setCheckState(Qt::Checked);
    }
    window._campaignTaskSelectionTouched = true;
    window._campaignSelectedTaskIds = {QStringLiteral("garden")};
    auto await = [&](const std::function<bool()>& predicate) {
      QEventLoop loop;
      QTimer check;
      QObject::connect(&check, &QTimer::timeout, &loop, [&] { if (predicate()) loop.quit(); });
      check.start(10);
      QTimer::singleShot(2000, &loop, &QEventLoop::quit);
      loop.exec();
      return predicate();
    };
    window.loadTasks();
    if (!await([&] { return tasks == 1; })) return false;
    const QJsonObject ego{{"provider", "ego4d"}, {"state", "ready"}, {"candidates", 3}};
    const QJsonObject nymeria{{"provider", "nymeria"}, {"state", "error"}, {"error", "Fixture: fonte indisponível"}};
    const QJsonDocument partial(QJsonObject{{"ready", false}, {"state", "partial"},
        {"error", "Fixture: preparo parcial"}, {"providers", QJsonArray{ego, nymeria}}});
    window._providerCatalogBusy = true;
    window.renderProviderCatalogReply(false, partial, "Fixture (HTTP 409)", true);
    if (!await([&] { return tasks == 2 && window._taskRecords.size() == 2 && !window._taskCatalogWatch.isActive(); })) return false;
    window.renderProviderCatalogReply(false, partial, "Fixture (HTTP 409)", false);
    window.refreshCampaignTasksAfterProviderPreparation("nymeria");
    QEventLoop staleWait;
    QTimer::singleShot(900, &staleWait, &QEventLoop::quit);
    staleWait.exec();
    auto selectionPreserved = [&] {
      return window._campaignTasks->count() == 2
          && window._campaignTasks->item(0)->data(Qt::UserRole) == "garden"
          && window._campaignTasks->item(0)->checkState() == Qt::Checked
          && window._campaignTasks->item(1)->checkState() == Qt::Unchecked
          && window._campaignAccounts->item(0)->checkState() == Qt::Checked;
    };
    if (tasks != 2 || !selectionPreserved()) return false;
    window._pages->setCurrentIndex(9);
    window._providerCatalogBusy = true;
    window.renderProviderCatalogReply(true, QJsonDocument(QJsonObject{{"ready", true},
        {"providers", QJsonArray{ego}}}), {}, true);
    if (!window._taskCatalogForceRefresh || tasks != 2 || !selectionPreserved()) return false;
    window._pages->setCurrentIndex(3);
    window.loadCampaignData();
    if (!await([&] { return tasks == 3 && !window._taskCatalogWatch.isActive(); })) return false;
    const bool preserved = selectionPreserved();
    window.shutdownCampaignTimers();
    window.shutdownProviderCatalogTimers();
    return identity && preserved && writes == 0;
  }
