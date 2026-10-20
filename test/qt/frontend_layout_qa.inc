  static void frontendLayoutSettle() {
    qApp->processEvents();
    QEventLoop loop;
    QTimer::singleShot(25, &loop, &QEventLoop::quit);
    loop.exec();
    qApp->processEvents();
  }

  static bool frontendLayoutFixture(QTcpServer& fixture, MainWindow& window) {
    for (auto* button : window._pages->widget(3)->findChildren<QAbstractButton*>()) {
      if (button->text().remove(QLatin1Char('&')).contains(QStringLiteral("Recuperar envios"), Qt::CaseInsensitive)) {
        std::cerr << "Layout QA: campaign still exposes manual delivery recovery" << std::endl;
        return false;
      }
    }
    if (!window._historyTable || !window._pages->widget(7)->isAncestorOf(window._historyTable)
        || window._navigation->item(7)->text() != QStringLiteral("Histórico")) {
      std::cerr << "Layout QA: campaign history is unavailable" << std::endl;
      return false;
    }
    if (!fixture.listen(QHostAddress::LocalHost, 0)) return false;
    QObject::connect(&fixture, &QTcpServer::newConnection, &fixture, [&fixture] {
      while (fixture.hasPendingConnections()) {
        auto* socket = fixture.nextPendingConnection();
        QObject::connect(socket, &QTcpSocket::readyRead, socket, [socket] {
          const auto input = socket->property("layoutQaInput").toByteArray() + socket->readAll();
          socket->setProperty("layoutQaInput", input);
          if (!input.contains("\r\n\r\n") || socket->property("layoutQaAnswered").toBool()) return;
          socket->setProperty("layoutQaAnswered", true);
          const QByteArray body = "{\"ok\":false,\"error\":\"Isolated layout fixture\"}";
          socket->write("HTTP/1.1 405 Method Not Allowed\r\nContent-Type: application/json\r\nContent-Length: "
              + QByteArray::number(body.size()) + "\r\nConnection: close\r\n\r\n" + body);
          socket->disconnectFromHost();
        });
        QObject::connect(socket, &QTcpSocket::disconnected, socket, &QObject::deleteLater);
      }
    });
    window._api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(fixture.serverPort()));
    window._backendReady = false;
    // Fixture controls remain inspectable, but cannot dispatch application actions.
    window._navigation->disconnect();
    for (auto* tabs : window.findChildren<QTabWidget*>()) tabs->blockSignals(true);
    for (auto* combo : window.findChildren<QComboBox*>()) combo->blockSignals(true);
    for (auto* spin : window.findChildren<QAbstractSpinBox*>()) spin->blockSignals(true);
    for (auto* field : window.findChildren<QLineEdit*>()) field->blockSignals(true);
    for (auto* button : window.findChildren<QAbstractButton*>()) button->disconnect();
    window._backendState->setText(QStringLiteral("● QA local · dados fictícios"));
    window._status->setText(QStringLiteral("Verificação de layout · serviço real desativado"));
    window.setWindowTitle(QStringLiteral("QFar — Layout — QA"));
    window._campaignVerificationStatus->setText(QStringLiteral("QA: verificação inicial · 37% desta etapa"));
    window._campaignVerificationPanel->show();
    for (auto* progress : window.findChildren<QProgressBar*>()) {
      const QSignalBlocker blocker(progress);
      progress->setRange(0, 100);
      progress->setValue(37);
      progress->setFormat(QStringLiteral("%p%"));
      progress->show();
    }
    window._providerCatalogPercent->setText(QStringLiteral("37%"));
    QObject::connect(window._navigation, &QListWidget::currentRowChanged, &window,
        [&window](int page) { window._pages->setCurrentIndex(page); });
    return true;
  }

  static bool frontendLayoutGeometry(MainWindow& window, int pageIndex, const QSize& requested) {
    bool passed = true;
    auto* page = window._pages->widget(pageIndex);
    const auto report = [&](QWidget* widget, const QString& reason, const QRect& bounds) {
      const QRect actual(widget->mapToGlobal(QPoint()), widget->size());
      std::cerr << "Layout QA: page=" << pageIndex << " object="
                << widget->objectName().toStdString() << " class=" << widget->metaObject()->className()
                << " reason=" << reason.toStdString()
                << " actual=" << actual.x() << ',' << actual.y() << ',' << actual.width() << ',' << actual.height()
                << " bounds=" << bounds.x() << ',' << bounds.y() << ',' << bounds.width() << ',' << bounds.height()
                << std::endl;
      passed = false;
    };
    if (window.width() > requested.width() || window.height() > requested.height())
      report(&window, QStringLiteral("window grew beyond requested size"), QRect(QPoint(), requested));
    const QRect pageBounds(page->mapToGlobal(QPoint()), page->size());
    for (auto* widget : page->findChildren<QWidget*>()) {
      if (!widget->isVisibleTo(page) || widget->isWindow()) continue;
      const bool control = qobject_cast<QAbstractButton*>(widget) || qobject_cast<QComboBox*>(widget)
          || qobject_cast<QLineEdit*>(widget) || qobject_cast<QAbstractSpinBox*>(widget)
          || qobject_cast<QProgressBar*>(widget) || qobject_cast<QAbstractItemView*>(widget)
          || qobject_cast<QScrollArea*>(widget) || qobject_cast<QTabWidget*>(widget);
      if (!control) continue;
      QRect bounds = pageBounds;
      // A scrolled child can legitimately lie above or below the viewport.
      // Its enclosing scroll area is checked against the page separately.
      for (QWidget* ancestor = widget->parentWidget(); ancestor && ancestor != page;
           ancestor = ancestor->parentWidget()) {
        auto* area = qobject_cast<QScrollArea*>(ancestor);
        if (area && area->widget() && area->widget()->isAncestorOf(widget)) {
          bounds = QRect(area->widget()->mapToGlobal(QPoint()), area->widget()->size());
          break;
        }
      }
      const QRect actual(widget->mapToGlobal(QPoint()), widget->size());
      if (widget->width() <= 0 || widget->height() <= 0 || !bounds.adjusted(-2, -2, 2, 2).contains(actual))
        report(widget, QStringLiteral("control exceeds page or scroll-content bounds"), bounds);
      if (auto* progress = qobject_cast<QProgressBar*>(widget)) {
        const int readableHeight = progress->fontMetrics().height() + 4;
        if (progress->height() < readableHeight)
          report(progress, QStringLiteral("progress height %1 below readable minimum %2")
              .arg(progress->height()).arg(readableHeight), bounds);
      }
    }
    return passed;
  }

  static void frontendLayoutScroll(QWidget* root, bool bottom) {
    for (auto* area : root->findChildren<QScrollArea*>()) {
      if (!area->isVisibleTo(root)) continue;
      area->horizontalScrollBar()->setValue(area->horizontalScrollBar()->minimum());
      area->verticalScrollBar()->setValue(bottom ? area->verticalScrollBar()->maximum()
                                                : area->verticalScrollBar()->minimum());
    }
    frontendLayoutSettle();
  }

  static bool frontendLayoutReview(MainWindow& window, bool dark, const QString& outputDirectory) {
    const QStringList emails{QStringLiteral("qa.first@example.invalid"), QStringLiteral("qa.second@example.invalid")};
    const QJsonObject request{{"target_hours", 0.5}, {"min_dur_s", 60}, {"max_dur_s", 600},
        {"active_hours", QJsonArray{7, 18}}, {"run_until_exhausted", false}};
    QJsonArray capacityAccounts;
    for (const QString& email : emails)
      capacityAccounts.append(QJsonObject{{"email", email}, {"available_seconds", 3600},
          {"unique_footage_seconds", 3600}, {"unique_footage_upper_bound_seconds", 3600},
          {"estimated_executable_seconds", 3600}, {"shortfall_seconds", 0}, {"eligible_clips", 24},
          {"estimated_sends", 12}, {"recorded_clips", 0}, {"pending_clips", 0}});
    const QJsonObject capacity{{"schema", 1}, {"known", true},
        {"basis", "estimated_admissible_delivery_duration_before_preparation"}, {"can_reach_goal", true},
        {"target_seconds_per_account", 1800}, {"available_seconds_min", 3600},
        {"available_seconds_max", 3600}, {"total_available_seconds", 7200},
        {"shortfall_account_count", 0}, {"estimated_sends", 24}, {"candidate_clips", 24},
        {"accounts", capacityAccounts}, {"requires_measured_validation", true}, {"pending_acquisition_clips", 8}};
    QJsonArray clips;
    for (int clip = 0; clip < 24; ++clip)
      clips.append(QJsonObject{{"clip_uid", QStringLiteral("qa-local-clip-%1").arg(clip + 1, 3, 10, QLatin1Char('0'))},
          {"task", QStringLiteral("Preparação de alimentos e atividades domésticas")}, {"duration_s", 300},
          {"eligible_accounts", QJsonArray{emails[0], emails[1]}}, {"excluded_accounts", QJsonArray{}},
          {"pending_accounts", QJsonArray{}}, {"requires_measured_validation", true}, {"readiness", "estimated"}});
    bool passed = true;
    for (bool blocked : {false, true}) {
      const QJsonObject result{{"ok", !blocked}, {"accounts", QJsonObject{{"validated", 2}}},
          {"tasks", QJsonObject{{"compatible", 3}}}, {"account_workers", 2}, {"clips", 24},
          {"estimated_sends", 24}, {"capacity", capacity}, {"clip_plan", clips},
          {"blockers", blocked ? QJsonArray{QStringLiteral("QA: ajuste a seleção antes de iniciar esta campanha fictícia.")}
                                : QJsonArray{}},
          {"warnings", QJsonArray{QStringLiteral("QA: vídeo e sensores ainda serão medidos antes do envio."),
              QStringLiteral("QA: confira os limites da campanha e a janela ativa antes de confirmar.")}}};
      if (!CampaignReviewDialog::validCapacity(result, request)) {
        std::cerr << "Layout QA: invalid synthetic review capacity" << std::endl;
        return false;
      }
      for (const QSize size : {QSize(580, 480), QSize(780, 620)}) {
        CampaignReviewDialog dialog(result, emails, &window, request);
        dialog.resize(size);
        dialog.show();
        frontendLayoutSettle();
        auto* footer = dialog.findChild<QDialogButtonBox*>();
        auto* confirm = dialog.findChild<QPushButton*>(QStringLiteral("campaignReviewConfirm"));
        auto* warning = dialog.findChild<QLabel*>(QStringLiteral("reviewWarning"));
        auto* scroll = dialog.findChild<QScrollArea*>();
        auto* tabs = dialog.findChild<QTabWidget*>();
        const auto geometry = [](QWidget* widget) { return QRect(widget->mapToGlobal(QPoint()), widget->size()); };
        const auto check = [&](const char* position) {
          const QRect bounds = geometry(&dialog);
          bool valid = footer && confirm && warning && scroll && tabs
              && dialog.width() <= size.width() && dialog.height() <= size.height();
          for (QWidget* widget : {static_cast<QWidget*>(footer), static_cast<QWidget*>(confirm), static_cast<QWidget*>(warning)}) {
            const QRect actual = widget ? geometry(widget) : QRect();
            if (!widget || !widget->isVisibleTo(&dialog) || !bounds.adjusted(-2, -2, 2, 2).contains(actual)) {
              std::cerr << "Layout QA: review=" << (blocked ? "blocked" : "ready")
                        << " position=" << position << " object=" << (widget ? widget->objectName().toStdString() : "missing")
                        << " actual=" << actual.x() << ',' << actual.y() << ',' << actual.width() << ',' << actual.height()
                        << " dialog=" << bounds.x() << ',' << bounds.y() << ',' << bounds.width() << ',' << bounds.height()
                        << std::endl;
              valid = false;
            }
          }
          valid &= confirm && confirm->isEnabled() == !blocked;
          valid &= scroll && scroll->widget() && footer && warning && !scroll->widget()->isAncestorOf(footer)
              && !scroll->widget()->isAncestorOf(warning);
          valid &= warning && warning->height() >= warning->heightForWidth(warning->width());
          if (!valid)
            std::cerr << "Layout QA: review footer or confirmation contract failed size=" << size.width() << 'x' << size.height()
                      << " actualSize=" << dialog.width() << 'x' << dialog.height()
                      << " blocked=" << blocked << " position=" << position << std::endl;
          return valid;
        };
        if (!footer || !confirm || !warning || !scroll || !tabs) {
          passed = check("initial") && passed;
          dialog.close();
          continue;
        }
        for (int tab = 0; tab < tabs->count(); ++tab) {
          tabs->setCurrentIndex(tab);
          frontendLayoutSettle();
          frontendLayoutScroll(&dialog, false);
          const QRect fixedFooter = geometry(footer), fixedWarning = geometry(warning);
          const QString name = QStringLiteral("review-%1-%2x%3-%4-tab-%5")
              .arg(dark ? QStringLiteral("dark") : QStringLiteral("light"))
              .arg(size.width()).arg(size.height()).arg(blocked ? QStringLiteral("blocked") : QStringLiteral("ready"))
              .arg(tab, 2, 10, QLatin1Char('0'));
          for (bool bottom : {false, true}) {
            frontendLayoutScroll(&dialog, bottom);
            passed = check(bottom ? "bottom" : "top") && passed;
            if (fixedFooter != geometry(footer) || fixedWarning != geometry(warning)) {
              std::cerr << "Layout QA: review warning/footer moved with content scroll " << name.toStdString() << std::endl;
              passed = false;
            }
            if (!outputDirectory.isEmpty()) {
              const QString file = name + (bottom ? QStringLiteral("-bottom.png") : QStringLiteral(".png"));
              if (!dialog.grab().save(QDir(outputDirectory).filePath(file))) {
                std::cerr << "Layout QA: review screenshot failed " << file.toStdString() << std::endl;
                passed = false;
              }
            }
          }
          frontendLayoutScroll(&dialog, false);
        }
        dialog.close();
      }
    }
    return passed;
  }

  static bool frontendLayoutPopup(MainWindow& window, int items, bool longText, const QSize& size) {
    window.resize(size);
    window._navigation->setCurrentRow(4);
    window._pages->setCurrentIndex(4);
    window._acceleratorTabs->setCurrentIndex(0);
    auto* combo = window._cacheTask;
    {
      const QSignalBlocker blocker(combo);
      combo->clear();
      for (int item = 0; item < items; ++item) {
        QString text = QStringLiteral("Categoria QA %1 — Preparação de alimentos e atividades domésticas").arg(item + 1);
        if (longText && item == items - 1)
          text += QStringLiteral(" · anotação pública detalhada").repeated(8);
        combo->addItem(text, QStringLiteral("fixture-%1").arg(item));
      }
      combo->setCurrentIndex(0);
    }
    frontendLayoutSettle();
    for (QWidget* ancestor = combo->parentWidget(); ancestor; ancestor = ancestor->parentWidget())
      if (auto* scroll = qobject_cast<QScrollArea*>(ancestor)) scroll->ensureWidgetVisible(combo);
    frontendLayoutSettle();
    combo->setFocus();
    frontendLayoutSettle();
    combo->showPopup();
    frontendLayoutSettle();
    auto* view = combo->view();
    auto* popup = view->window();
    const QRect available = combo->screen()->availableGeometry();
    const QRect actual = popup->geometry();
    bool passed = popup->isVisible() && available.adjusted(-1, -1, 1, 1).contains(actual);
    const QModelIndex last = combo->model()->index(items - 1, combo->modelColumn(), combo->rootModelIndex());
    view->scrollTo(last, QAbstractItemView::PositionAtBottom);
    view->verticalScrollBar()->setValue(view->verticalScrollBar()->maximum());
    frontendLayoutSettle();
    const QRect lastRow = view->visualRect(last);
    const QRect viewport = view->viewport()->rect();
    passed &= lastRow.isValid() && lastRow.top() >= viewport.top() - 1
        && lastRow.bottom() <= viewport.bottom() + 1 && viewport.intersects(lastRow);
    passed &= combo->itemData(items - 1, Qt::ToolTipRole).toString() == combo->itemText(items - 1);
    if (items > combo->maxVisibleItems()) passed &= view->verticalScrollBar()->maximum() > 0;
    if (items == 2) {
      const int twoRows = 2 * view->sizeHintForRow(0) + 2 * view->frameWidth()
          + view->horizontalScrollBar()->sizeHint().height() + 12;
      passed &= view->height() <= twoRows;
    }
    const int expectedTextWidth = view->fontMetrics().horizontalAdvance(combo->itemText(items - 1));
    if (longText && expectedTextWidth > view->viewport()->width())
      passed &= view->horizontalScrollBar()->maximum() > 0;
    if (!passed)
      std::cerr << "Layout QA: page=4 object=cache-task-popup items=" << items
                << " window=" << size.width() << 'x' << size.height()
                << " popup=" << actual.x() << ',' << actual.y() << ',' << actual.width() << ',' << actual.height()
                << " viewport=" << view->viewport()->width() << 'x' << view->viewport()->height()
                << " lastRow=" << lastRow.x() << ',' << lastRow.y() << ',' << lastRow.width() << ',' << lastRow.height()
                << " scroll=" << view->verticalScrollBar()->maximum() << ',' << view->horizontalScrollBar()->maximum()
                << " rowHint=" << view->sizeHintForRow(0) << " frame=" << view->frameWidth()
                << " viewHeight=" << view->height() << " horizontalHint=" << view->horizontalScrollBar()->sizeHint().height()
                << " tooltipMatch=" << (combo->itemData(items - 1, Qt::ToolTipRole).toString() == combo->itemText(items - 1))
                << " popupVisible=" << popup->isVisible() << " available=" << available.x() << ',' << available.y() << ',' << available.width() << ',' << available.height()
                << std::endl;
    combo->hidePopup();
    frontendLayoutSettle();
    return passed;
  }

  static bool frontendLayoutAudit(const QString& outputDirectory = {}) {
    if (!outputDirectory.isEmpty() && !QDir().mkpath(outputDirectory)) {
      std::cerr << "Layout QA: cannot create screenshot directory" << std::endl;
      return false;
    }
    QTcpServer fixture;
    auto* style = new oclero::qlementine::QlementineStyle;
    qApp->setStyle(style);
    MainWindow window(style, nullptr, false);
    if (!frontendLayoutFixture(fixture, window)) return false;
    if (window._pages->count() != 10) {
      std::cerr << "Layout QA: expected 10 pages, found " << window._pages->count() << std::endl;
      return false;
    }
    window.show();
    frontendLayoutSettle();
    bool passed = true;
    for (bool dark : {false, true}) {
      window.setDarkTheme(dark);
      for (const QSize size : {QSize(1280, 820), QSize(980, 680)}) {
        window.resize(size);
        for (int pageIndex = 0; pageIndex < window._pages->count(); ++pageIndex) {
          const QSignalBlocker navigationBlocker(window._navigation);
          window._navigation->setCurrentRow(pageIndex);
          window._pages->setCurrentIndex(pageIndex);
          auto* page = window._pages->widget(pageIndex);
          const auto tabs = page->findChildren<QTabWidget*>();
          QList<QList<int>> configurations;
          configurations.append(QList<int>{});
          for (auto* tab : tabs) {
            QList<QList<int>> next;
            for (const auto& configuration : configurations)
              for (int index = 0; index < tab->count(); ++index) {
                auto choice = configuration;
                choice.append(index);
                next.append(choice);
              }
            configurations = next;
          }
          int variant = 0;
          for (const auto& configuration : configurations) {
            for (int tab = 0; tab < tabs.size(); ++tab) {
              const QSignalBlocker blocker(tabs[tab]);
              tabs[tab]->setCurrentIndex(configuration[tab]);
            }
            frontendLayoutSettle();
            frontendLayoutScroll(page, false);
            passed = frontendLayoutGeometry(window, pageIndex, size) && passed;
            const QString name = QStringLiteral("%1-%2x%3-page-%4-tab-%5")
                .arg(dark ? QStringLiteral("dark") : QStringLiteral("light"))
                .arg(size.width()).arg(size.height()).arg(pageIndex, 2, 10, QLatin1Char('0'))
                .arg(variant, 2, 10, QLatin1Char('0'));
            if (!outputDirectory.isEmpty()) {
              if (!window.grab().save(QDir(outputDirectory).filePath(name + QStringLiteral(".png")))) {
                std::cerr << "Layout QA: screenshot failed " << name.toStdString() << std::endl;
                passed = false;
              }
            }
            frontendLayoutScroll(page, true);
            passed = frontendLayoutGeometry(window, pageIndex, size) && passed;
            if (!outputDirectory.isEmpty()
                && !window.grab().save(QDir(outputDirectory).filePath(name + QStringLiteral("-bottom.png")))) {
              std::cerr << "Layout QA: bottom screenshot failed " << name.toStdString() << std::endl;
              passed = false;
            }
            frontendLayoutScroll(page, false);
            ++variant;
          }
        }
      }
      // Reopening the same real ComboBox catches stale fixed popup dimensions.
      passed = frontendLayoutPopup(window, 32, true, QSize(1280, 820)) && passed;
      passed = frontendLayoutPopup(window, 2, false, QSize(1280, 820)) && passed;
      passed = frontendLayoutPopup(window, 18, true, QSize(980, 680)) && passed;
      passed = frontendLayoutReview(window, dark, outputDirectory) && passed;
    }
    passed &= window._backend.state() == QProcess::NotRunning && !window._backendReady;
    window.close();
    return passed;
  }

  static int frontendLayoutPreview() {
    QTcpServer fixture;
    auto* style = new oclero::qlementine::QlementineStyle;
    qApp->setStyle(style);
    MainWindow window(style, nullptr, false);
    if (!frontendLayoutFixture(fixture, window)) return 2;
    const auto arguments = qApp->arguments();
    const auto integerArgument = [&arguments](const QString& name, int fallback) {
      const int index = arguments.indexOf(name);
      bool valid = false;
      const int value = index >= 0 ? arguments.value(index + 1).toInt(&valid) : fallback;
      return valid ? value : fallback;
    };
    window.show();
    frontendLayoutSettle();
    window.setDarkTheme(arguments.contains(QStringLiteral("--qa-dark")));
    const int page = qBound(0, integerArgument(QStringLiteral("--qa-page"), 0), window._pages->count() - 1);
    window._navigation->setCurrentRow(page);
    window._pages->setCurrentIndex(page);
    const int index = integerArgument(QStringLiteral("--qa-tab"), 0);
    for (auto* tabs : window._pages->widget(page)->findChildren<QTabWidget*>())
      tabs->setCurrentIndex(qBound(0, index, tabs->count() - 1));
    QTimer::singleShot(qBound(1000, integerArgument(QStringLiteral("--qa-timeout-ms"), 120000), 120000),
        qApp, &QApplication::quit);
    return qApp->exec();
  }
