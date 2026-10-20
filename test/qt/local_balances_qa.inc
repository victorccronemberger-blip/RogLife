  static bool balancesReadingLifecycleSmoke() {
    const auto failure = [](int line) {
      std::cerr << "Balances reading/lifecycle native assertion failed at " << line << "\n";
      return false;
    };
    auto* style = new oclero::qlementine::QlementineStyle;
    qApp->setStyle(style);
    QTcpServer server;
    if (!server.listen(QHostAddress::LocalHost, 0)) return failure(__LINE__);
    QList<QPointer<QTcpSocket>> readsPending, writesPending;
    int reads = 0, writes = 0, unexpected = 0;
    const auto respond = [](QPointer<QTcpSocket> socket, const QJsonObject& snapshot) {
      if (!socket) return false;
      const auto body = QJsonDocument(snapshot).toJson(QJsonDocument::Compact);
      socket->write("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
          + QByteArray::number(body.size()) + "\r\nConnection: close\r\n\r\n" + body);
      socket->disconnectFromHost();
      return true;
    };
    QObject::connect(&server, &QTcpServer::newConnection, &server, [&] {
      while (server.hasPendingConnections()) {
        auto* socket = server.nextPendingConnection();
        QObject::connect(socket, &QTcpSocket::readyRead, socket, [&, socket] {
          const auto bytes = socket->property("balancesQaInput").toByteArray() + socket->readAll();
          socket->setProperty("balancesQaInput", bytes);
          const int end = bytes.indexOf("\r\n\r\n");
          if (end < 0 || socket->property("balancesQaAnswered").toBool()) return;
          int length = 0;
          for (const auto& header : bytes.left(end).split('\n'))
            if (header.trimmed().toLower().startsWith("content-length:"))
              length = header.mid(header.indexOf(':') + 1).trimmed().toInt();
          if (bytes.size() < end + 4 + length) return;
          socket->setProperty("balancesQaAnswered", true);
          const auto route = bytes.left(bytes.indexOf("\r\n"));
          if (route == "GET /api/balances HTTP/1.1") {
            ++reads;
            readsPending.append(socket);
          } else if (route == "POST /api/balances/refresh HTTP/1.1") {
            ++writes;
            writesPending.append(socket);
          } else {
            ++unexpected;
            respond(socket, {});
          }
        });
        QObject::connect(socket, &QTcpSocket::disconnected, socket, &QObject::deleteLater);
      }
    });
    MainWindow window(style, nullptr, false);
    window._api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    window._pages->setCurrentIndex(6);
    {
      const QSignalBlocker navigationBlocker(window._navigation);
      window._navigation->setCurrentRow(6);
    }
    const auto until = [](auto condition) {
      QElapsedTimer clock;
      clock.start();
      while (!condition() && clock.elapsed() < 3000)
        qApp->processEvents(QEventLoop::AllEvents, 20);
      return condition();
    };
    const auto settle = [] {
      QEventLoop loop;
      QTimer::singleShot(30, &loop, &QEventLoop::quit);
      loop.exec();
    };
    const QString currentEmail = QStringLiteral("current@example.invalid");
    const QString expiredEmail = QStringLiteral("expired@example.invalid");
    const QString claruEmail = QStringLiteral("old@supply.claru.ai");
    const QString expiredReason = QStringLiteral("Consulte novamente: a leitura está fora da validade de 24 horas.");
    const QString stamp = QDateTime::currentDateTimeUtc().toString(Qt::ISODate);
    const QString oldStamp = QDateTime::currentDateTimeUtc().addSecs(-36 * 3600).toString(Qt::ISODate);
    const auto record = [](int available, int pending, const QString& updated) {
      return QJsonObject{{"availableCents", available}, {"pendingCents", pending},
          {"updated_at", updated}, {"error", QJsonValue::Null}, {"stale", false}};
    };
    const auto walletRow = [](bool confirmed, const QString& reason) {
      return QJsonObject{{"reading", QJsonObject{{"confirmed", confirmed},
          {"code", confirmed ? QStringLiteral("confirmed") : QStringLiteral("expired")}, {"reason", reason}}},
          // A contradictory payout flag must not enable withdrawal of historical funds.
          {"payout", QJsonObject{{"eligible", true}, {"reason", reason}}}};
    };
    QJsonObject claruRecord{{"provider", "claru"}, {"availableCents", 1234}, {"submissionCents", 1034},
        {"referralCents", 100}, {"bonusCents", 100}, {"updated_at", oldStamp},
        {"error", QJsonValue::Null}, {"stale", false}};
    QJsonObject snapshot{{"accounts", QJsonArray{currentEmail, expiredEmail, claruEmail}},
        {"balances", QJsonObject{{currentEmail, record(10000, 2000, stamp)},
            {expiredEmail, record(50000, 3000, oldStamp)}, {claruEmail, claruRecord}}},
        {"account_kinds", QJsonObject{{claruEmail, "claru"}}},
        {"with_password", QJsonArray{currentEmail, expiredEmail}},
        {"refresh_needed", QJsonArray{expiredEmail}},
        {"runner", QJsonObject{{"state", "idle"}}},
        {"withdraw_bulk", QJsonObject{{"state", "idle"}}},
        {"exchange", QJsonObject{{"available", true}, {"rate", 5.0},
            {"quote_date", QDate::currentDate().toString(Qt::ISODate)}}},
        {"wallet", QJsonObject{
            {"accounts", QJsonObject{{currentEmail, walletRow(true, "Leitura completa confirmada nas últimas 24 horas.")},
                {expiredEmail, walletRow(false, expiredReason)}, {claruEmail, walletRow(false, expiredReason)}}},
            {"totals", QJsonObject{{"availableCents", 10000}, {"pendingCents", 2000}}},
            {"historical_totals", QJsonObject{{"availableCents", 51234}, {"pendingCents", 3000}}}}}};
    const auto rowFor = [&](const QString& email) {
      for (int row = 0; row < window._balancesTable->rowCount(); ++row)
        if (window._balancesTable->item(row, 0)->text() == email) return row;
      return -1;
    };
    const auto withdrawEnabled = [&](const QString& email) {
      const int row = rowFor(email);
      if (row < 0) return false;
      for (auto* button : window._balancesTable->cellWidget(row, 4)->findChildren<QPushButton*>())
        if (button->text() == QStringLiteral("Solicitar saque")) return button->isEnabled();
      return false;
    };
    // Explicit hidden reads are used by local previews; they must never start polling.
    window.loadBalances();
    if (!until([&] { return readsPending.size() == 1; })) return failure(__LINE__);
    if (!respond(readsPending.takeFirst(), snapshot)
        || !until([&] { return !window._balancesReadPending; })) return failure(__LINE__);
    const int currentRow = rowFor(currentEmail), expiredRow = rowFor(expiredEmail), claruRow = rowFor(claruEmail);
    if (currentRow < 0 || expiredRow < 0 || claruRow < 0 || window._balancePoll.isActive()
        || window._balancesApprovedUsd->text() != window.usdMoney(10000)
        || window._balancesPendingUsd->text() != window.usdMoney(2000)
        || window._balancesTable->item(currentRow, 1)->text() != window.usdMoney(10000)
        || window._balancesTable->item(expiredRow, 1)->text() != window.usdMoney(50000) + QStringLiteral(" *")
        || !window._balancesTable->item(expiredRow, 1)->toolTip().contains(expiredReason)
        || !window._balancesTotalsNote->text().contains(window.usdMoney(51234))
        || !window._balancesTotalsNote->text().contains(window.usdMoney(3000))
        || !withdrawEnabled(currentEmail) || withdrawEnabled(expiredEmail)
        || window._balancesWithdrawAll->property("eligibleCount").toInt() != 1) return failure(__LINE__);
    window._balancesOnlyAvailable->setChecked(true);
    if (window._balancesTable->isRowHidden(currentRow) || !window._balancesTable->isRowHidden(expiredRow)
        || !window._balancesTable->isRowHidden(claruRow)) return failure(__LINE__);
    window._balancesOnlyAvailable->setChecked(false);
    window._balancesSearch->setText(QStringLiteral("example.invalid"));
    if (!window._balancesTable->isRowHidden(claruRow)) return failure(__LINE__);
    window._balancesSearch->clear();

    window._backendReady = true;
    window.resize(980, 680);
    window._backendState->setText(QStringLiteral("● QA local · contas fictícias"));
    window.setWindowTitle(QStringLiteral("QFar — Saldos — QA local"));
    window.show();
    settle();
    const int outputArg = qApp->arguments().indexOf(QStringLiteral("--output-dir"));
    if (outputArg >= 0) {
      const QString output = qApp->arguments().value(outputArg + 1);
      if (output.isEmpty() || !QDir().mkpath(output)) return failure(__LINE__);
      for (bool dark : {false, true}) {
        window.setDarkTheme(dark);
        settle();
        if (!window.grab().save(QDir(output).filePath(dark
            ? QStringLiteral("balances-reading-980x680-dark.png")
            : QStringLiteral("balances-reading-980x680-light.png")))) return failure(__LINE__);
      }
    }
    bool claruHistorical = false;
    QTimer dismiss;
    dismiss.setInterval(10);
    QObject::connect(&dismiss, &QTimer::timeout, &window, [&] {
      for (auto* widget : qApp->topLevelWidgets()) {
        auto* dialog = qobject_cast<QMessageBox*>(widget);
        if (!dialog || !dialog->isVisible()) continue;
        claruHistorical = dialog->text().contains(QStringLiteral("Último saldo salvo"))
            && dialog->text().contains(expiredReason) && !dialog->text().contains(QStringLiteral("Saldo atual"));
        dialog->accept();
      }
    });
    dismiss.start();
    for (auto* button : window._balancesTable->cellWidget(claruRow, 4)->findChildren<QPushButton*>())
      if (button->text() == QStringLiteral("Ganhos Claru")) button->click();
    dismiss.stop();
    if (!claruHistorical || writes != 0) return failure(__LINE__);

    QTemporaryDir exported;
    if (!exported.isValid()) return failure(__LINE__);
    const QString csvPath = exported.filePath(QStringLiteral("balances-fixture.csv"));
    // Only the forced non-native Qt offscreen selector uses Fusion; this does not
    // validate the native Windows selector. The window keeps Qlementine and owns
    // this fixture style through widget teardown.
    struct CsvChooserStyle final : QObject {
      QStyle* fixtureStyle{};
      bool changing{};
      bool eventFilter(QObject* watched, QEvent* event) override {
        if (changing || event->type() != QEvent::Polish) return false;
        auto* widget = qobject_cast<QWidget*>(watched);
        if (!widget || widget->style() == fixtureStyle) return false;
        bool fileChooser = false;
        for (auto* ancestor = widget; ancestor; ancestor = ancestor->parentWidget())
          if (ancestor->inherits("QFileDialog")) { fileChooser = true; break; }
        if (!fileChooser) return false;
        changing = true;
        widget->setStyle(fixtureStyle);
        changing = false;
        return false;
      }
    } chooserStyle;
    chooserStyle.fixtureStyle = QStyleFactory::create(QStringLiteral("Fusion"));
    if (!chooserStyle.fixtureStyle) return failure(__LINE__);
    chooserStyle.fixtureStyle->setParent(&window);
    const bool offscreenChooser = qApp->platformName() == QStringLiteral("offscreen");
    if (offscreenChooser) qApp->installEventFilter(&chooserStyle);
    const bool nativeDialogsDisabled = qApp->testAttribute(Qt::AA_DontUseNativeDialogs);
    qApp->setAttribute(Qt::AA_DontUseNativeDialogs, true);
    bool csvSelected = false;
    QElapsedTimer chooserTime;
    chooserTime.start();
    QTimer chooser;
    chooser.setInterval(10);
    QTimer chooserDeadline;
    chooserDeadline.setSingleShot(true);
    QObject::connect(&chooserDeadline, &QTimer::timeout, &window, [&] {
      for (auto* widget : qApp->topLevelWidgets()) {
        auto* dialog = qobject_cast<QDialog*>(widget);
        if (dialog && dialog->isVisible() && dialog->inherits("QFileDialog")) dialog->reject();
      }
    });
    QObject::connect(&chooser, &QTimer::timeout, &window, [&] {
      for (auto* widget : qApp->topLevelWidgets()) {
        auto* dialog = qobject_cast<QDialog*>(widget);
        if (!dialog || !dialog->isVisible() || !dialog->inherits("QFileDialog")) continue;
        if (chooserTime.elapsed() > 2500) { dialog->reject(); continue; }
        auto* name = dialog->findChild<QLineEdit*>(QStringLiteral("fileNameEdit"));
        if (!name || csvSelected) continue;
        // accept() may enter another event loop; claim the dialog before invoking it.
        csvSelected = true;
        chooser.stop();
        name->setText(csvPath);
        if (!QMetaObject::invokeMethod(dialog, "accept", Qt::DirectConnection)) csvSelected = false;
      }
    });
    chooser.start();
    chooserDeadline.start(3000);
    window._balancesExport->click();
    chooser.stop();
    chooserDeadline.stop();
    if (offscreenChooser) qApp->removeEventFilter(&chooserStyle);
    qApp->setAttribute(Qt::AA_DontUseNativeDialogs, nativeDialogsDisabled);
    QFile csv(csvPath);
    if (!csvSelected || !csv.open(QIODevice::ReadOnly)) return failure(__LINE__);
    const auto csvLines = QString::fromUtf8(csv.readAll()).split(QLatin1Char('\n'));
    bool currentExport = false, historicalExport = false;
    for (const auto& line : csvLines) {
      if (line.contains(currentEmail)) currentExport = line.endsWith(QStringLiteral(";\"Confirmado\""));
      if (line.contains(expiredEmail)) historicalExport = line.contains(QStringLiteral("\"500,00\""))
          && line.contains(expiredReason) && line.contains(QStringLiteral("Leitura não confirmada"));
    }
    if (!currentExport || !historicalExport || writes != 0) return failure(__LINE__);

    // Refresh intents coalesce behind one in-flight read; the obsolete result cannot render.
    window._balancesSearch->setText(QStringLiteral("example.invalid"));
    window._balancesOnlyAvailable->setChecked(true);
    const QJsonObject beforeRefresh = window._balancesSnapshot;
    const int beforeReads = reads;
    window.loadBalances();
    if (!until([&] { return readsPending.size() == 1; })) return failure(__LINE__);
    for (int intent = 0; intent < 4; ++intent) window.loadBalances();
    if (reads != beforeReads + 1 || readsPending.size() != 1) return failure(__LINE__);
    auto obsolete = snapshot;
    obsolete.insert("qaMarker", "obsolete");
    if (!respond(readsPending.takeFirst(), obsolete)
        || !until([&] { return readsPending.size() == 1; })) return failure(__LINE__);
    if (reads != beforeReads + 2 || window._balancesSnapshot != beforeRefresh) return failure(__LINE__);
    auto fresh = snapshot;
    fresh.insert("qaMarker", "fresh");
    if (!respond(readsPending.takeFirst(), fresh)
        || !until([&] { return !window._balancesReadPending; })) return failure(__LINE__);
    if (window._balancesSnapshot != fresh || !window._balancesOnlyAvailable->isChecked()
        || window._balancesSearch->text() != QStringLiteral("example.invalid")) return failure(__LINE__);

    // Hiding the window or leaving the page rejects late data and never restarts a timer.
    window.loadBalances();
    if (!until([&] { return readsPending.size() == 1; })) return failure(__LINE__);
    window.hide();
    auto running = snapshot;
    running.insert("runner", QJsonObject{{"state", "running"}});
    if (!respond(readsPending.takeFirst(), running)
        || !until([&] { return !window._balancesReadPending; })) return failure(__LINE__);
    settle();
    if (window._balancesSnapshot != fresh || window._balancePoll.isActive() || !readsPending.isEmpty())
      return failure(__LINE__);
    window.show();
    window.loadBalances();
    if (!until([&] { return readsPending.size() == 1; })) return failure(__LINE__);
    window._pages->setCurrentIndex(7);
    window.onBalancesPageActivated(7);
    if (!respond(readsPending.takeFirst(), running)
        || !until([&] { return !window._balancesReadPending; })) return failure(__LINE__);
    if (window._balancesSnapshot != fresh || window._balancePoll.isActive() || !readsPending.isEmpty())
      return failure(__LINE__);
    window._pages->setCurrentIndex(6);

    // Backend loss rejects old callbacks; recovery during a pending read queues one current read.
    window.loadBalances();
    if (!until([&] { return readsPending.size() == 1; })) return failure(__LINE__);
    window._backendReady = false;
    ++window._operationBackendGeneration;
    if (!respond(readsPending.takeFirst(), obsolete)
        || !until([&] { return !window._balancesReadPending; })) return failure(__LINE__);
    if (window._balancesSnapshot != fresh || !readsPending.isEmpty() || window._balancePoll.isActive())
      return failure(__LINE__);
    window._backendReady = true;
    ++window._operationBackendGeneration;
    window.loadBalances();
    if (!until([&] { return readsPending.size() == 1; })) return failure(__LINE__);
    window._backendReady = false;
    ++window._operationBackendGeneration;
    window._backendReady = true;
    ++window._operationBackendGeneration;
    window.loadBalances();
    if (!respond(readsPending.takeFirst(), obsolete)
        || !until([&] { return readsPending.size() == 1; })) return failure(__LINE__);
    if (window._balancesSnapshot != fresh) return failure(__LINE__);
    auto recovered = snapshot;
    recovered.insert("qaMarker", "recovered");
    if (!respond(readsPending.takeFirst(), recovered)
        || !until([&] { return !window._balancesReadPending; })) return failure(__LINE__);
    if (window._balancesSnapshot != recovered || !window._balancesOnlyAvailable->isChecked())
      return failure(__LINE__);

    // A missing wallet contract keeps saved row data historical and makes current totals unknown.
    auto missingWallet = snapshot;
    missingWallet.remove("wallet");
    window.loadBalances();
    if (!until([&] { return readsPending.size() == 1; })) return failure(__LINE__);
    if (!respond(readsPending.takeFirst(), missingWallet)
        || !until([&] { return !window._balancesReadPending; })) return failure(__LINE__);
    if (window._balancesApprovedUsd->text() != QStringLiteral("US$ —")
        || window._balancesPendingUsd->text() != QStringLiteral("US$ —")
        || window._balancesApprovedBrl->text() != QStringLiteral("≈ R$ —")
        || window._balancesPendingBrl->text() != QStringLiteral("≈ R$ —")
        || !window._balancesTotalsNote->text().contains(QStringLiteral("indisponíveis"))
        || window._balancesWithdrawAll->isEnabled() || withdrawEnabled(currentEmail)) return failure(__LINE__);
    for (int row = 0; row < window._balancesTable->rowCount(); ++row)
      if (!window._balancesTable->isRowHidden(row)) return failure(__LINE__);
    window._balancesOnlyAvailable->setChecked(false);
    if (!window._balancesTable->item(rowFor(currentEmail), 1)->text().endsWith(QStringLiteral(" *")))
      return failure(__LINE__);

    // A local synthetic write completion after hiding cannot resume background polling.
    window._balancesRefresh->click();
    if (!until([&] { return writesPending.size() == 1; })) return failure(__LINE__);
    window.hide();
    if (!respond(writesPending.takeFirst(), {{"ok", true}})) return failure(__LINE__);
    settle();
    if (window._balancePoll.isActive() || !readsPending.isEmpty() || writes != 1) return failure(__LINE__);
    window._backendReady = true;
    window.loadBalances();
    if (!until([&] { return readsPending.size() == 1; })) return failure(__LINE__);
    window._closing = true;
    if (!respond(readsPending.takeFirst(), recovered)
        || !until([&] { return !window._balancesReadPending; })) return failure(__LINE__);
    if (window._balancesSnapshot != missingWallet || window._balancePoll.isActive()) return failure(__LINE__);
    return unexpected == 0 || failure(__LINE__);
  }
