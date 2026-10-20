  static bool passiveAccountsAndIntegrationsSmoke() {
    const auto fail = [](int line) {
      std::cerr << "Passive data native assertion failed at " << line << "\n";
      return false;
    };
    auto* style = new oclero::qlementine::QlementineStyle;
    qApp->setStyle(style);
    QTcpServer server;
    if (!server.listen(QHostAddress::LocalHost, 0)) return fail(__LINE__);
    QList<QPointer<QTcpSocket>> accountReads, integrationReads, claruReads;
    bool holdClaru = false;
    int writes = 0;
    const auto reply = [](QPointer<QTcpSocket> socket, const QJsonObject& payload, int status = 200) {
      if (!socket) return;
      const auto body = QJsonDocument(payload).toJson(QJsonDocument::Compact);
      socket->write("HTTP/1.1 " + QByteArray::number(status) + (status == 200 ? " OK" : " Failure")
          + "\r\nContent-Type: application/json\r\nContent-Length: " + QByteArray::number(body.size())
          + "\r\nConnection: close\r\n\r\n" + body);
      socket->disconnectFromHost();
    };
    QObject::connect(&server, &QTcpServer::newConnection, &server, [&] {
      while (server.hasPendingConnections()) {
        auto* socket = server.nextPendingConnection();
        QObject::connect(socket, &QTcpSocket::readyRead, socket, [&, socket] {
          const auto input = socket->property("passiveInput").toByteArray() + socket->readAll();
          socket->setProperty("passiveInput", input);
          if (!input.contains("\r\n\r\n") || socket->property("passiveAnswered").toBool()) return;
          socket->setProperty("passiveAnswered", true);
          const auto request = input.left(input.indexOf("\r\n")).split(' ');
          if (request.value(0) != "GET") { ++writes; reply(socket, {}, 405); }
          else if (request.value(1) == "/api/accounts") accountReads.append(socket);
          else if (request.value(1) == "/api/integrations") integrationReads.append(socket);
          else if (request.value(1) == "/api/accounts/claru") {
            if (holdClaru) claruReads.append(socket);
            else reply(socket, {{"accounts", QJsonArray{}}, {"pending", QJsonArray{}}});
          }
          else reply(socket, {{"state", "idle"}, {"counts", QJsonObject{}}});
        });
        QObject::connect(socket, &QTcpSocket::disconnected, socket, &QObject::deleteLater);
      }
    });
    MainWindow window(style, nullptr, false);
    window._livePagePoll.stop(); // Exercise each owner's read lifecycle deterministically.
    window._api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    window._backendReady = true;
    window._pages->setCurrentIndex(5);
    window.show();
    const auto until = [](auto condition) {
      QElapsedTimer clock;
      clock.start();
      while (!condition() && clock.elapsed() < 3000) qApp->processEvents(QEventLoop::AllEvents, 20);
      return condition();
    };
    const auto settle = [] {
      QEventLoop loop;
      QTimer::singleShot(30, &loop, &QEventLoop::quit);
      loop.exec();
    };
    const QString email = QStringLiteral("live@example.invalid");
    QJsonObject account{{"email", email}, {"org_name", "Initial"}, {"expires_at", 4102444800.0}};
    QJsonObject accounts{{"accounts", QJsonArray{account}}};
    window._accountEmail->setText(QStringLiteral("unfinished@example.invalid"));
    window._accountPassword->setText(QStringLiteral("fixture-form-value"));
    window.refreshAccountsLive();
    window.refreshAccountsLive();
    if (!until([&] { return accountReads.size() == 1; })) return fail(__LINE__);
    reply(accountReads.takeFirst(), accounts);
    if (!until([&] { return !window._accountsReadPending && window._accountsTable->rowCount() == 1; })) return fail(__LINE__);
    auto* originalCell = window._accountsTable->item(0, 0);
    auto* originalActions = window._accountsTable->cellWidget(0, 3);
    window._accountsTable->selectRow(0);
    window.refreshAccountsLive();
    if (!until([&] { return accountReads.size() == 1; })) return fail(__LINE__);
    reply(accountReads.takeFirst(), accounts);
    if (!until([&] { return !window._accountsReadPending; })) return fail(__LINE__);
    if (window._accountsTable->item(0, 0) != originalCell
        || window._accountsTable->cellWidget(0, 3) != originalActions) return fail(__LINE__);
    if (!until([&] { return !window._claruReadPending; })) return fail(__LINE__);
    holdClaru = true;
    window.refreshAccountsLive();
    if (!until([&] { return accountReads.size() == 1 && claruReads.size() == 1; })) return fail(__LINE__);
    QDialog modal(&window);
    modal.setModal(true);
    modal.show();
    if (!until([&] { return QApplication::activeModalWidget() == &modal; })) return fail(__LINE__);
    reply(accountReads.takeFirst(), {{"accounts", QJsonArray{}}});
    reply(claruReads.takeFirst(), {{"accounts", QJsonArray{QJsonObject{
        {"login_email", "late@example.invalid"}, {"minute_email", "late@supply.claru.ai"}}}},
        {"pending", QJsonArray{}}});
    if (!until([&] { return !window._accountsReadPending && !window._claruReadPending; })
        || window._accountsTable->item(0, 0) != originalCell
        || window._accountsTable->cellWidget(0, 3) != originalActions
        || window._claruConnections->count() != 1) return fail(__LINE__);
    modal.close();
    if (!until([&] { return QApplication::activeModalWidget() == nullptr; })) return fail(__LINE__);
    holdClaru = false;
    account.insert("org_name", "Updated automatically");
    window._accountChecks.insert(email, {{"status_label", "Older local check"}, {"checked_at", "2025-01-01T00:00:00Z"}});
    account.insert("last_check", QJsonObject{{"status_label", "Newer service check"}, {"checked_at", "2025-01-02T00:00:00Z"}});
    accounts.insert("accounts", QJsonArray{QJsonObject{{"email", "new@example.invalid"}}, account});
    window.refreshAccountsLive();
    if (!until([&] { return accountReads.size() == 1; })) return fail(__LINE__);
    reply(accountReads.takeFirst(), accounts);
    if (!until([&] { return !window._accountsReadPending; })) return fail(__LINE__);
    if (window._accountsTable->rowCount() != 2 || !window._accountsTable->item(1, 0)->isSelected()
        || !window._accountsTable->item(1, 1)->text().contains("Updated automatically")
        || !window._accountsTable->item(1, 2)->text().contains("Newer service check")
        || window._accountEmail->text() != "unfinished@example.invalid"
        || window._accountPassword->text() != "fixture-form-value") return fail(__LINE__);
    window.refreshAccountsLive();
    if (!until([&] { return accountReads.size() == 1; })) return fail(__LINE__);
    reply(accountReads.takeFirst(), {{"error", "fixture transport failure"}}, 503);
    if (!until([&] { return !window._accountsReadPending; }) || QApplication::activeModalWidget()
        || window._accountsTable->rowCount() != 2) return fail(__LINE__);
    window.refreshAccountsLive();
    if (!until([&] { return accountReads.size() == 1; })) return fail(__LINE__);
    ++window._livePageRevision; // Navigated away and back before this reply.
    reply(accountReads.takeFirst(), {{"accounts", QJsonArray{}}});
    if (!until([&] { return !window._accountsReadPending; }) || window._accountsTable->rowCount() != 2)
      return fail(__LINE__);
    window.refreshAccountsLive();
    if (!until([&] { return accountReads.size() == 1; })) return fail(__LINE__);
    const auto oldAccountRead = accountReads.takeFirst();
    ++window._operationBackendGeneration;
    window.refreshAccountsLive();
    if (!until([&] { return accountReads.size() == 1; })) return fail(__LINE__);
    reply(oldAccountRead, {{"accounts", QJsonArray{}}});
    settle();
    if (!window._accountsReadPending || window._accountsTable->rowCount() != 2) return fail(__LINE__);
    reply(accountReads.takeFirst(), accounts);
    if (!until([&] { return !window._accountsReadPending; })) return fail(__LINE__);

    ++window._livePageRevision;
    window._pages->setCurrentIndex(2);
    QJsonObject host{{"configured", true}, {"connection_count", 1},
        {"profiles", QJsonArray{QJsonObject{{"id", "fixture-one"}, {"name", "First mailbox"}}}}};
    QJsonObject integrations{{"ego4d", QJsonObject{{"configured", true}, {"catalog_ready", false}}},
        {"hostinger", host}, {"holoassist", QJsonObject{}}, {"runtime", QJsonObject{}},
        {"appcheck", QJsonObject{}}, {"security", QJsonObject{}}};
    window._hostingerToken->setText(QStringLiteral("fixture-edit-in-progress"));
    window._hostingerSave->setEnabled(true);
    window._ego4dSecretKey->setText(QStringLiteral("fixture-credential-edit"));
    window.refreshIntegrationsLive();
    window.refreshIntegrationsLive();
    if (!until([&] { return integrationReads.size() == 1; })) return fail(__LINE__);
    reply(integrationReads.takeFirst(), integrations);
    if (!until([&] { return !window._integrationsReadPending; })) return fail(__LINE__);
    if (window._hostingerProfile->count() != 1 || window._hostingerProfile->currentData().toMap().value("id").toString() != "fixture-one"
        || window._hostingerToken->text() != "fixture-edit-in-progress" || !window._hostingerSave->isEnabled()
        || window._ego4dSecretKey->text() != "fixture-credential-edit") return fail(__LINE__);
    host.insert("profiles", QJsonArray{QJsonObject{{"id", "fixture-two"}, {"name", "Second mailbox"}},
        QJsonObject{{"id", "fixture-one"}, {"name", "First mailbox renamed"}}});
    host.insert("connection_count", 2);
    integrations.insert("hostinger", host);
    integrations.insert("ego4d", QJsonObject{{"configured", true}, {"catalog_ready", true}, {"annotations_ready", true}});
    window.refreshIntegrationsLive();
    if (!until([&] { return integrationReads.size() == 1; })) return fail(__LINE__);
    modal.show();
    if (!until([&] { return QApplication::activeModalWidget() == &modal; })) return fail(__LINE__);
    reply(integrationReads.takeFirst(), integrations);
    if (!until([&] { return !window._integrationsReadPending; })
        || window._hostingerProfile->count() != 1
        || window._hostingerToken->text() != "fixture-edit-in-progress") return fail(__LINE__);
    modal.close();
    if (!until([&] { return QApplication::activeModalWidget() == nullptr; })) return fail(__LINE__);
    window.refreshIntegrationsLive();
    if (!until([&] { return integrationReads.size() == 1; })) return fail(__LINE__);
    reply(integrationReads.takeFirst(), integrations);
    if (!until([&] { return !window._integrationsReadPending; })) return fail(__LINE__);
    if (window._hostingerProfile->currentData().toMap().value("id").toString() != "fixture-one"
        || !window._hostingerStatus->text().contains("2 caixa(s)")
        || !window._ego4dCatalog->text().contains("instalado")
        || window._hostingerToken->text() != "fixture-edit-in-progress"
        || window._ego4dSecretKey->text() != "fixture-credential-edit") return fail(__LINE__);
    window.refreshIntegrationsLive();
    if (!until([&] { return integrationReads.size() == 1; })) return fail(__LINE__);
    reply(integrationReads.takeFirst(), {{"error", "fixture unavailable"}}, 503);
    if (!until([&] { return !window._integrationsReadPending; }) || QApplication::activeModalWidget()
        || !window._integrationsSummary->text().contains("preservados")) return fail(__LINE__);
    window.refreshIntegrationsLive();
    if (!until([&] { return integrationReads.size() == 1; })) return fail(__LINE__);
    ++window._livePageRevision;
    reply(integrationReads.takeFirst(), {});
    if (!until([&] { return !window._integrationsReadPending; }) || window._hostingerProfile->count() != 2)
      return fail(__LINE__);
    window._closing = true;
    window.refreshAccountsLive();
    window.refreshIntegrationsLive();
    settle();
    return writes == 0 && accountReads.isEmpty() && integrationReads.isEmpty() && claruReads.isEmpty();
  }
