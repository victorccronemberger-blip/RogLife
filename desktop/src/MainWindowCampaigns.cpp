#include "MainWindowSupport.hpp"
#include "MainWindow.hpp"


void MainWindow::initializeCampaignTimers() {
  _campaignPoll.setInterval(1100);
  _previewPoll.setInterval(15000);
  _taskReload.setInterval(350);
  _taskReload.setSingleShot(true);
  _cachePoll.setInterval(3000);
  connect(&_campaignPoll, &QTimer::timeout, this, &MainWindow::pollCampaign);
  connect(&_previewPoll, &QTimer::timeout, this, &MainWindow::pollCampaignPreviews);
  connect(&_taskReload, &QTimer::timeout, this, &MainWindow::loadTasks);
  connect(&_cachePoll, &QTimer::timeout, this, &MainWindow::loadAccelerator);
  _taskCatalogWatch.setInterval(1000);
  connect(&_taskCatalogWatch, &QTimer::timeout, this, &MainWindow::updateTaskCatalogMonitor);
  _campaignVerificationWatch.setInterval(1000);
  connect(&_campaignVerificationWatch, &QTimer::timeout, this, [this] {
    updateCampaignVerificationMonitor();
  });
}

void MainWindow::updateCampaignVerificationMonitor(const QJsonObject& progress) {
  if (!_campaignVerificationStatus || !_campaignVerifying || _closing) return;
  if (!progress.isEmpty()) {
    _campaignVerificationReply.restart();
    _campaignVerificationMessage = progress.value("message").toString(QStringLiteral("Verificando campanha…"));
    const auto completed = progress.value("completed"), total = progress.value("total");
    if (completed.isDouble() && total.isDouble() && total.toDouble() > 0
        && completed.toDouble() >= 0 && completed.toDouble() <= total.toDouble()) {
      _campaignVerificationProgress->setRange(0, 100);
      _campaignVerificationProgress->setValue(int(100 * completed.toDouble() / total.toDouble()));
    } else {
      _campaignVerificationProgress->setRange(0, 0);
    }
  }
  const int elapsed = int(_campaignVerificationClock.elapsed() / 1000);
  const int silence = _campaignVerificationReply.isValid()
      ? int(_campaignVerificationReply.elapsed() / 1000) : elapsed;
  const QString percent = _campaignVerificationProgress->maximum() > 0
      ? QStringLiteral(" · %1% desta etapa").arg(_campaignVerificationProgress->value()) : QString();
  _campaignVerificationStatus->setText(QStringLiteral("Verificação inicial: %1%2\n%3 s · %4 · nenhum envio iniciado")
      .arg(_campaignVerificationMessage, percent).arg(elapsed)
      .arg(silence >= 30 ? QStringLiteral("sem resposta nova há %1 s").arg(silence)
                        : QStringLiteral("aguardando conclusão da etapa")));
}

void MainWindow::updateTaskCatalogMonitor() {
  if (!_taskCatalogStatus || !_taskCatalogClock.isValid()) return;
  const int sinceReply = _taskCatalogLastReply.isValid()
      ? int(_taskCatalogLastReply.elapsed() / 1000) : int(_taskCatalogClock.elapsed() / 1000);
  const int elapsed = qMax(int(_taskCatalogClock.elapsed() / 1000),
                          _taskCatalogServerElapsed + sinceReply);
  const QString contact = !_taskCatalogLastReply.isValid()
      ? QStringLiteral("Aguardando a primeira resposta do serviço")
      : QStringLiteral("Última resposta há %1 s").arg(sinceReply);
  const int idle = _taskCatalogIdleSeconds + sinceReply;
  const QString warning = sinceReply >= 30
      ? QStringLiteral("\nO serviço está demorando a responder; ainda não há confirmação de conclusão.")
      : idle >= 30 ? QStringLiteral("\nSem avanço confirmado há %1 s; o serviço ainda responde.").arg(idle)
                   : QString();
  _taskCatalogStatus->setText(QStringLiteral("%1\nTempo: %2 s · %3%4")
      .arg(_taskCatalogMessage).arg(elapsed).arg(contact, warning));
}

void MainWindow::finishTaskCatalogMonitor(const QString& message, bool success) {
  _taskCatalogWatch.stop();
  if (!_taskCatalogStatus) return;
  _taskCatalogProgress->setRange(0, 100);
  _taskCatalogProgress->setValue(success ? 100 : 0);
  _taskCatalogProgress->setFormat(QStringLiteral("%p%"));
  _taskCatalogStatus->setText(message);
}

void MainWindow::onCampaignPageActivated(int index) {
  if (index == 3 && _campaignStop && _campaignStop->isEnabled()) _campaignPoll.start();
  else if (index != 3) _campaignPoll.stop();
  if (index != 4) _cachePoll.stop();
}

void MainWindow::shutdownCampaignTimers() {
  if (_campaignDraftSave.isActive()) {
    _campaignDraftSave.stop();
    saveCampaignDraft();
  }
  _campaignPoll.stop();
  _previewPoll.stop();
  _cachePoll.stop();
  _taskReload.stop();
  _taskCatalogWatch.stop();
  _api.cancelTaskCatalogRequests();
  _campaignVerificationWatch.stop();
}

void MainWindow::onCampaignBackendAvailabilityChanged(bool ready, bool generationChanged) {
  if (generationChanged) {
    _campaignVerifying = false;
    _campaignVerificationWatch.stop();
    if (_campaignVerificationPanel) _campaignVerificationPanel->hide();
  }
  if (!ready) {
    if (_taskCatalogWatch.isActive()) {
      ++_taskLoadGeneration;
      _api.cancelTaskCatalogRequests();
      finishTaskCatalogMonitor(QStringLiteral("O serviço ficou indisponível. Recarregue as categorias após reconectar."));
    }
    return;
  }

  if (_campaignStartUncertain) {
    _campaignStart->setText(QStringLiteral("Início não confirmado"));
    _campaignStart->setEnabled(false);
    _campaignPoll.start();
    pollCampaign();
  }
  const QString savedPreview = QSettings().value(QStringLiteral("previewLogName")).toString();
  if (_previewLogName.isEmpty() && !savedPreview.isEmpty()) {
    _previewLogName = QFileInfo(savedPreview).fileName();
    _previewPoll.start();
    QTimer::singleShot(0, this, &MainWindow::pollCampaignPreviews);
  }
}

QString MainWindow::selectedCampaignDataset() const {
  return _dataset ? _dataset->currentData().toString() : QStringLiteral("all");
}

bool MainWindow::campaignIsBusy() const {
  return _campaignActive || _campaignVerifying;
}

QSet<QString> MainWindow::campaignLibraryTaskSelection(
    const QSet<QString>& availableNames) const {
  QSet<QString> selectedNames = _campaignTaskSelectionTouched
      ? QSet<QString>{} : availableNames;
  for (const auto& value : _taskRecords) {
    const auto task = value.toObject();
    if (_campaignSelectedTaskIds.contains(task.value(QStringLiteral("id")).toString()))
      selectedNames.insert(task.value(QStringLiteral("name")).toString());
  }
  return selectedNames;
}

void MainWindow::selectCombinedCampaignDataset() {
  if (!_dataset) return;
  const int index = _dataset->findData(QStringLiteral("ambos"));
  if (index >= 0) _dataset->setCurrentIndex(index);
  _campaignTaskSelectionTouched = false;
  loadTasks();
}

void MainWindow::disableHoloAssistCampaignSources() {
  for (QComboBox* combo : {_dataset, _cacheProvider}) {
    if (!combo) continue;
    for (int index = combo->count() - 1; index >= 0; --index) {
      const QString value = combo->itemData(index).toString();
      if (value == QStringLiteral("holoassist") || value == QStringLiteral("all"))
        combo->removeItem(index);
    }
  }
}

void MainWindow::restorePendingCampaignStart() {
    const QByteArray pendingStart = QSettings().value(QStringLiteral("campaign/pendingStart")).toByteArray();
    const auto savedStart = QJsonDocument::fromJson(pendingStart).object();
    _pendingCampaignStartId = savedStart.value(QStringLiteral("start_request_id")).toString();
    const bool validPendingStart = savedStart.value(QStringLiteral("schema_version")) == QJsonValue(1)
        && QRegularExpression(QStringLiteral("^[A-Za-z0-9_-]{1,160}$"))
               .match(_pendingCampaignStartId).hasMatch();
    _campaignStartUncertain = !pendingStart.isEmpty();
    if (!validPendingStart) _pendingCampaignStartId.clear();
    if (_campaignStartUncertain) {
      _campaignStart->setEnabled(false);
      _campaignStart->setText(QStringLiteral("Início anterior requer revisão"));
      setStatus(validPendingStart
          ? QStringLiteral("Consultando o identificador salvo antes de permitir outra campanha.")
          : QStringLiteral("O registro local de início não pôde ser lido. Revise Recuperação antes de iniciar outra campanha."));
    }
 }

void MainWindow::loadCampaignData() {
  // Até o motor responder, mantenha ações destrutivas e uma nova partida
  // bloqueadas. Isso evita uma janela curta em que a tela ainda não conhece
  // uma campanha em execução ou encerramento.
  _campaignActive = true;
  _campaignStart->setEnabled(false);
  _campaignReset->setEnabled(false);
  _api.get(QStringLiteral("/api/accounts"), [this](bool ok, const QJsonDocument& doc, const QString& error) {
    if (!ok) return showError(QStringLiteral("Falha ao carregar contas"), error);
    QSet<QString> selectedBefore;
    const bool hadAccounts = _campaignAccounts->count() > 0;
    for (int i = 0; i < _campaignAccounts->count(); ++i)
      if (_campaignAccounts->item(i)->checkState() == Qt::Checked)
        selectedBefore.insert(_campaignAccounts->item(i)->data(Qt::UserRole).toString());
    const QSignalBlocker blocker(_campaignAccounts);
    _campaignAccounts->clear();
    for (const auto value : doc.object().value(QStringLiteral("accounts")).toArray()) {
      const auto account = value.toObject();
      auto* item = new QListWidgetItem(account.value(QStringLiteral("email")).toString());
      item->setSizeHint(QSize(0, 38));
      item->setFlags(item->flags() | Qt::ItemIsUserCheckable);
      const QString email = account.value(QStringLiteral("email")).toString();
      const bool selected = hadAccounts ? selectedBefore.contains(email)
          : _campaignDraftLoaded ? _campaignDraftAccounts.contains(email) : true;
      item->setCheckState(selected ? Qt::Checked : Qt::Unchecked);
      item->setData(Qt::UserRole, account.value(QStringLiteral("email")).toString());
      item->setHidden(!email.contains(_campaignAccountSearch->text().trimmed(), Qt::CaseInsensitive));
      _campaignAccounts->addItem(item);
    }
    _campaignAccountCount->setMaximum(qMax(1, _campaignAccounts->count()));
    if (!hadAccounts && _campaignDraftLoaded) {
      const QSignalBlocker blocker(_campaignAccountCount);
      _campaignAccountCount->setValue(_campaignDraftQuantity);
    }
    _campaignAccountCount->setEnabled(_campaignAccounts->count() > 0);
    _campaignDrawAccounts->setEnabled(_campaignAccounts->count() > 0);
    int selectedCount = 0;
    for (int i = 0; i < _campaignAccounts->count(); ++i)
      if (_campaignAccounts->item(i)->checkState() == Qt::Checked) ++selectedCount;
    const bool automatic = _campaignAccountMode->currentData().toString() != QStringLiteral("manual");
    if (_campaignAccountMode->currentData().toString().startsWith(QStringLiteral("balance_"))) {
      _campaignBalancesLoaded = false;
      drawCampaignAccounts();
    } else if (automatic && _campaignAccounts->count() > 0
        && selectedCount != _campaignAccountCount->value()) drawCampaignAccounts();
    else { updateCampaignAccountCount(); loadTasks(); }
  });
  pollCampaign();
}

void MainWindow::loadTasks() {
  _taskReload.stop();
  _api.cancelTaskCatalogRequests();
  const int generation = ++_taskLoadGeneration;
  QStringList selectedAccounts;
  for (int i = 0; i < _campaignAccounts->count(); ++i) {
    if (_campaignAccounts->item(i)->checkState() == Qt::Checked) {
      const QString email = _campaignAccounts->item(i)->data(Qt::UserRole).toString().trimmed();
      if (!email.isEmpty()) selectedAccounts.append(email);
    }
  }
  if (selectedAccounts.isEmpty()) {
    finishTaskCatalogMonitor(QStringLiteral("Selecione uma conta para carregar as categorias."));
    const QSignalBlocker blocker(_campaignTasks);
    _campaignTasks->clear();
    _taskRecords = {};
    _campaignStart->setEnabled(false);
    return;
  }
  QString selectionKey = selectedAccounts.join(QLatin1Char('\n')) + QLatin1Char('\n')
      + _dataset->currentData().toString() + QLatin1Char('\n')
      + _contentMode->currentData().toString() + QLatin1Char('\n')
      + QString::number(_minDuration->value()) + QLatin1Char('\n')
      + QString::number(_maxDuration->value());
  if (_taskCatalogFallbackSelection != selectionKey) {
    _taskCatalogFallbackSelection = selectionKey;
    _taskCatalogFallbackAttempted.clear();
    _taskCatalogFallbackExhausted = false;
  }
  QString account;
  for (const QString& candidate : selectedAccounts) {
    if (!_taskCatalogFallbackAttempted.contains(candidate.toCaseFolded())) {
      account = candidate;
      break;
    }
  }
  if (account.isEmpty()) {
    finishTaskCatalogMonitor(QStringLiteral("Nenhuma conta selecionada disponibilizou o catálogo. Confira o erro abaixo."));
    const QSignalBlocker blocker(_campaignTasks);
    _campaignTasks->clear();
    _taskRecords = {};
    auto* failure = new QListWidgetItem(QStringLiteral(
        "Não foi possível carregar categorias em nenhuma das contas que você selecionou. Atualize as contas ou marque outra conta para tentar novamente."),
        _campaignTasks);
    failure->setFlags(Qt::NoItemFlags);
    _taskCatalogFallbackExhausted = true;
    _campaignStart->setEnabled(false);
    setStatus(QStringLiteral("O catálogo falhou nas contas selecionadas; nenhuma outra conta foi usada."));
    return;
  }
  {
    const QSignalBlocker blocker(_campaignTasks);
    _campaignTasks->clear();
    _campaignTasks->addItem(QStringLiteral("Carregando categorias…"));
  }
  _campaignStart->setEnabled(false);
  _taskRecords = {};
  _taskCatalogMessage = QStringLiteral("Conectando ao serviço para consultar categorias…");
  _taskCatalogClock.start();
  _taskCatalogLastReply.invalidate();
  _taskCatalogServerElapsed = 0;
  _taskCatalogIdleSeconds = 0;
  _taskCatalogProgress->setRange(0, 0);
  _taskCatalogProgress->setFormat(QStringLiteral("%p%"));
  _taskCatalogWatch.start();
  updateTaskCatalogMonitor();
  const QString path = QStringLiteral(
      "/api/tasks?async=1&prepared_only=1&email=%1&min_dur_s=%2&max_dur_s=%3&dataset=%4&content_mode=%5")
      .arg(encoded(account)).arg(_minDuration->value() * 60)
      .arg(_maxDuration->value() * 60)
      .arg(encoded(_dataset->currentData().toString()))
      .arg(encoded(_contentMode->currentData().toString()));
  const QString requestPath = path + (_taskCatalogForceRefresh ? QStringLiteral("&refresh=1") : QString());
  _taskCatalogForceRefresh = false;
  _api.get(requestPath, [this, generation, account, selectedAccounts, selectionKey](bool ok, const QJsonDocument& doc,
                                   const QString& error) {
    if (generation != _taskLoadGeneration || _closing) return;
    QStringList currentAccounts;
    for (int i = 0; i < _campaignAccounts->count(); ++i)
      if (_campaignAccounts->item(i)->checkState() == Qt::Checked)
        currentAccounts.append(_campaignAccounts->item(i)->data(Qt::UserRole).toString().trimmed());
    const QString currentKey = currentAccounts.join(QLatin1Char('\n')) + QLatin1Char('\n')
        + _dataset->currentData().toString() + QLatin1Char('\n')
        + _contentMode->currentData().toString() + QLatin1Char('\n')
        + QString::number(_minDuration->value()) + QLatin1Char('\n')
        + QString::number(_maxDuration->value());
    if (currentKey != selectionKey) {
      _taskReload.start(0);
      return;
    }
    const QSignalBlocker blocker(_campaignTasks);
    _campaignTasks->clear();
    const auto response = doc.object();
    if (!ok) {
      if (response.value(QStringLiteral("code")).toString() == QStringLiteral("provider_catalog_preparation_required")) {
        finishTaskCatalogMonitor(QStringLiteral("Prepare ou atualize os índices na tela Catálogos antes de selecionar as categorias."));
        auto* item = new QListWidgetItem(response.value(QStringLiteral("error")).toString(), _campaignTasks);
        item->setFlags(Qt::NoItemFlags);
        _campaignStart->setEnabled(false);
        return;
      }
      finishTaskCatalogMonitor(QStringLiteral("Falha na consulta: %1\nRecarregar categorias recupera o cálculo existente, se ainda estiver em andamento.").arg(error));
      const auto issue = response.value(QStringLiteral("issue")).toObject();
      const QString issueEmail = issue.value(QStringLiteral("email")).toString().trimmed();
      const QString issueCode = issue.value(QStringLiteral("code")).toString().trimmed();
      const bool confirmedCatalogFailure = response.value(QStringLiteral("code")).toString()
              == QStringLiteral("catalog_account_unavailable")
          && issueEmail.compare(account, Qt::CaseInsensitive) == 0 && !issueCode.isEmpty()
          && issue.value(QStringLiteral("stage")).toString()
              == QStringLiteral("Carregamento remoto de categorias");
      if (confirmedCatalogFailure && selectionKey == _taskCatalogFallbackSelection) {
        _taskCatalogFallbackAttempted.insert(account.toCaseFolded());
        if (response.value(QStringLiteral("permanently_removed")).toBool()) {
          for (int i = _campaignAccounts->count() - 1; i >= 0; --i) {
            auto* item = _campaignAccounts->item(i);
            if (item->data(Qt::UserRole).toString().compare(account, Qt::CaseInsensitive) == 0)
              delete _campaignAccounts->takeItem(i);
          }
          _campaignDraftAccounts.remove(account);
          updateCampaignAccountCount();
          loadAccounts();
        }
        const QString archiveWarning = response.value(QStringLiteral("removal_failed")).toBool()
            ? QStringLiteral("A restrição foi confirmada, mas o arquivamento local falhou; a conta permanece para revisão. ")
            : QString();
        for (const QString& next : selectedAccounts) {
          if (!_taskCatalogFallbackAttempted.contains(next.toCaseFolded())) {
            setStatus(archiveWarning + QStringLiteral("Tentando o catálogo da próxima conta que você já selecionou."));
            _taskReload.start(0);
            return;
          }
        }
        _taskCatalogFallbackExhausted = true;
        auto* failure = new QListWidgetItem(archiveWarning + QStringLiteral(
            "Falha ao carregar categorias nas contas selecionadas. Marque outra conta ou atualize as contas para tentar novamente."),
            _campaignTasks);
        failure->setFlags(Qt::NoItemFlags);
        _campaignStart->setEnabled(false);
        setStatus(archiveWarning + QStringLiteral("O catálogo falhou em todas as contas selecionadas."));
        return;
      }
      _campaignTasks->addItem(QStringLiteral("Falha: ") + error);
      return;
    }
    _taskCatalogFallbackExhausted = false;
    _taskRecords = response.value(QStringLiteral("tasks")).toArray();
    finishTaskCatalogMonitor(QStringLiteral("Catálogo concluído: %1 categorias consultadas em %2 s.")
        .arg(_taskRecords.size()).arg(_taskCatalogClock.elapsed() / 1000), true);
    int compatible = 0;
    int outsideDuration = 0;
    for (const auto value : _taskRecords) {
      const auto task = value.toObject();
      const bool available = task.value(QStringLiteral("available_for_duration")).toBool(false);
      const bool outside = !available && task.value(QStringLiteral("overall_clip_count")).toInt() > 0;
      if (outside) ++outsideDuration;
      QString label = task.value(QStringLiteral("name_pt")).toString();
      if (label.isEmpty()) label = task.value(QStringLiteral("name")).toString();
      if (available) label += QStringLiteral("  ·  %1 trecho(s)")
                                  .arg(task.value(QStringLiteral("clip_count")).toInt());
      if (task.value(QStringLiteral("boosted")).toBool()) label += QStringLiteral("  ·  turbinada");
      if (task.value(QStringLiteral("requires_audio")).toBool()) label += QStringLiteral("  ·  exige áudio");
      if (!available) label += outside ? QStringLiteral("  ·  fora desta duração")
                                      : QStringLiteral("  ·  sem clipe compatível");
      auto* item = new QListWidgetItem(label);
      item->setSizeHint(QSize(0, 38));
      item->setFlags(item->flags() | Qt::ItemIsUserCheckable);
      item->setData(Qt::UserRole, jsonId(task.value(QStringLiteral("id"))));
      const QString taskId = item->data(Qt::UserRole).toString();
      item->setCheckState(available && (!_campaignTaskSelectionTouched
          || _campaignSelectedTaskIds.contains(taskId)) ? Qt::Checked : Qt::Unchecked);
      if (available) ++compatible;
      if (available && task.contains(QStringLiteral("parent_video_count"))) {
        const bool cacheOnly = _contentMode->currentData().toString() == QStringLiteral("cache");
        item->setToolTip(QStringLiteral("%1 trechos de %2 vídeos de origem.\n%3\n"
                                       "Os totais consideram a categoria, os sensores e a duração escolhida.")
                             .arg(task.value(QStringLiteral("clip_count")).toInt())
                             .arg(task.value(QStringLiteral("parent_video_count")).toInt())
                             .arg(cacheOnly
                                  ? QStringLiteral("Todos já estão preparados neste computador.")
                                  : QStringLiteral("A seleção pode incluir conteúdo ainda não baixado.")));
      }
      if (!available) {
        item->setFlags(item->flags() & ~Qt::ItemIsEnabled);
        item->setToolTip(task.value(QStringLiteral("unavailable_reason")).toString(
            QStringLiteral("Categoria sem clipe compatível no conjunto escolhido.")));
      }
      _campaignTasks->addItem(item);
    }
    int selectedTasks = 0;
    for (int i = 0; i < _campaignTasks->count(); ++i)
      if (_campaignTasks->item(i)->checkState() == Qt::Checked) ++selectedTasks;
    _campaignStart->setEnabled(selectedTasks > 0 && !_campaignActive && !_campaignVerifying
                               && !_shutdownPolling && !_campaignStartUncertain);
    if (compatible == 0) {
      setStatus(outsideDuration > 0
          ? QStringLiteral("Há conteúdo fora desta faixa. Ajuste a duração mínima ou máxima e aguarde as categorias atualizarem.")
          : QStringLiteral("Nenhuma categoria tem clipe compatível nesta origem e duração."));
    } else {
      setStatus(QStringLiteral("%1 categoria(s) compatível(is) de %2 carregadas.")
                    .arg(compatible).arg(_campaignTasks->count()));
    }
  }, [this, generation](const QJsonObject& progress) {
    if (generation != _taskLoadGeneration || _closing) return;
    const QSignalBlocker blocker(_campaignTasks);
    QString message = progress.value(QStringLiteral("message")).toString(QStringLiteral("Preparando categorias…"));
    _taskCatalogMessage = message;
    _taskCatalogServerElapsed = progress.value(QStringLiteral("elapsed_s")).toInt();
    _taskCatalogIdleSeconds = progress.value(QStringLiteral("idle_s")).toInt();
    _taskCatalogLastReply.restart();
    int completed = progress.value(QStringLiteral("completed")).toInt(-1);
    int total = progress.value(QStringLiteral("total")).toInt();
    QString unit = QStringLiteral("Categorias conferidas");
    // Some provider stages already report real x/y counters in their message.
    // These are stage progress, never an estimate of total processing time.
    if (progress.value(QStringLiteral("phase")).toString().endsWith(QStringLiteral("annotations"))) {
      const auto count = QRegularExpression(QStringLiteral("(\\d+)/(\\d+)")).match(message);
      if (count.hasMatch()) {
        completed = count.captured(1).toInt(); total = count.captured(2).toInt();
        unit = QStringLiteral("Etapa atual");
      }
    }
    if (total > 0 && completed >= 0 && completed <= total) {
      const int percent = int(100.0 * completed / total);
      _taskCatalogProgress->setRange(0, 100);
      _taskCatalogProgress->setValue(percent);
      _taskCatalogProgress->setFormat(QStringLiteral("%p%"));
      _taskCatalogMessage += QStringLiteral("\n%1: %2/%3 · %4%")
          .arg(unit).arg(completed).arg(total).arg(percent);
    } else {
      _taskCatalogProgress->setRange(0, 0);
    }
    updateTaskCatalogMonitor();
    const QString summary = QStringLiteral("%1 · %2 s").arg(message)
        .arg(qMax(_taskCatalogServerElapsed, int(_taskCatalogClock.elapsed() / 1000)));
    if (_campaignTasks->count() == 1) _campaignTasks->item(0)->setText(summary);
    setStatus(summary);
  });
}

void MainWindow::startCampaign() {
  if (!_backendReady || _closing || _shutdownPolling || _campaignActive || _campaignVerifying
      || _campaignStartUncertain) return;
  if (_campaignAccountMode->currentData().toString().startsWith(QStringLiteral("balance_"))
      && !_campaignBalancesLoaded)
    return showError(QStringLiteral("Saldos ainda não carregados"),
                     QStringLiteral("Aguarde a leitura dos saldos antes de iniciar."));
  if (_taskReload.isActive())
    return showError(QStringLiteral("Categorias ainda não atualizadas"),
                     QStringLiteral("Aguarde a atualização das categorias após mudar as contas."));
  QJsonArray accounts;
  QStringList selectedAccountNames;
  const QString selectionMode = _campaignAccountMode->currentData().toString();
  const QString balanceField = selectionMode.contains(QStringLiteral("available"))
      ? QStringLiteral("availableCents") : QStringLiteral("pendingCents");
  const auto balanceRecords = _campaignBalances.value(QStringLiteral("balances")).toObject();
  for (int i = 0; i < _campaignAccounts->count(); ++i) {
    auto* item = _campaignAccounts->item(i);
    if (item->checkState() == Qt::Checked) {
      const QString email = item->data(Qt::UserRole).toString();
      accounts.append(email);
      const auto amount = balanceRecords.value(email).toObject().value(balanceField);
      selectedAccountNames << (selectionMode.startsWith(QStringLiteral("balance_")) && amount.isDouble()
          ? QStringLiteral("%1 · %2").arg(email, usdMoney(static_cast<qint64>(amount.toDouble())))
          : email);
    }
  }
  if (selectionMode.startsWith(QStringLiteral("balance_"))
      && accounts.size() != _campaignAccountCount->value())
    return showError(QStringLiteral("Seleção por saldo incompleta"),
                     QStringLiteral("A quantidade pedida supera as contas com saldo confirmado. "
                                    "Reduza a quantidade ou atualize os saldos na aba Saldos."));
  QJsonArray tasks;
  for (int i = 0; i < _campaignTasks->count(); ++i) {
    auto* item = _campaignTasks->item(i);
    if (item->checkState() == Qt::Checked && !item->data(Qt::UserRole).toString().isEmpty()) {
      tasks.append(QJsonObject{{QStringLiteral("task_id"), item->data(Qt::UserRole).toString()}});
    }
  }
  if (accounts.isEmpty() || tasks.isEmpty()) {
    return showError(QStringLiteral("Seleção incompleta"),
                     QStringLiteral("Marque ao menos uma conta e uma categoria."));
  }
  QJsonObject body{
      {QStringLiteral("accounts"), accounts},
      {QStringLiteral("dataset"), _dataset->currentData().toString()},
      {QStringLiteral("content_mode"), _contentMode->currentData().toString()},
      {QStringLiteral("tasks"), tasks},
      {QStringLiteral("count"), 1},
      {QStringLiteral("include_clip_plan"), true},
      {QStringLiteral("target_hours"), _runUntilExhausted->isChecked() ? 0.0 : _targetHours->value()},
      {QStringLiteral("run_until_exhausted"), _runUntilExhausted->isChecked()},
      {QStringLiteral("min_dur_s"), _minDuration->value() * 60},
      {QStringLiteral("max_dur_s"), _maxDuration->value() * 60},
      {QStringLiteral("delay_mode"), _delayMode->currentData().toString()},
      {QStringLiteral("delay_s"), _delaySeconds->value()},
      {QStringLiteral("cleanup_after_upload"), _cleanupAfter->isChecked()}};
  if (_activeHours->isChecked()) {
    body.insert(QStringLiteral("active_hours"), QJsonArray{_hourStart->value(), _hourEnd->value()});
  } else {
    body.insert(QStringLiteral("active_hours"), QJsonValue::Null);
  }
  _campaignStart->setEnabled(false);
  _campaignVerifying = true;
  _campaignVerificationClock.restart();
  _campaignVerificationReply.invalidate();
  _campaignVerificationMessage = QStringLiteral("Aguardando resposta do serviço…");
  _campaignVerificationProgress->setRange(0, 0);
  _campaignVerificationPanel->show();
  _campaignVerificationWatch.start();
  updateCampaignVerificationMonitor();
  _campaignStart->setText(QStringLiteral("Executando preflight…"));
  const QString preflightRequestId = QUuid::createUuid().toString(QUuid::WithoutBraces)
      .remove(QLatin1Char('-')).toLower();
  _api.post(QStringLiteral("/api/campaigns/preflight?async=1&request_id=%1").arg(preflightRequestId), body,
            [this, body, selectedAccountNames](bool ok, const QJsonDocument& doc, const QString& error) {
    _campaignVerifying = false;
    _campaignVerificationWatch.stop();
    _campaignVerificationPanel->hide();
    if (!ok) {
      _campaignStart->setText(QStringLiteral("Iniciar campanha"));
      _campaignStart->setEnabled(!_campaignStartUncertain);
      return showError(QStringLiteral("Preflight não concluído"), error);
    }
    const auto result = doc.object();
    const auto blockers = result.value(QStringLiteral("blockers")).toArray();
    const auto warnings = result.value(QStringLiteral("warnings")).toArray();
    QStringList blockerLines;
    for (const auto& value : blockers) blockerLines << QStringLiteral("• ") + value.toString();
    QStringList warningLines;
    for (const auto& value : warnings) warningLines << QStringLiteral("• ") + value.toString();
    if (!result.value(QStringLiteral("ok")).toBool() || !blockerLines.isEmpty()) {
      _campaignStart->setText(QStringLiteral("Iniciar campanha"));
      _campaignStart->setEnabled(!_campaignStartUncertain);
      const auto issues = result.value(QStringLiteral("account_issues")).toArray();
      const auto errors = result.value(QStringLiteral("account_errors")).toArray();
      if (!issues.isEmpty()) {
        for (const auto& value : errors)
          blockerLines.removeAll(QStringLiteral("• ") + value.toString());
      } else {
        // Compatível com motores anteriores que já retornavam o erro da conta,
        // mas cuja interface mostrava somente a contagem de falhas.
        for (const auto& value : errors) {
          const QString line = QStringLiteral("• ") + value.toString();
          if (!blockerLines.contains(line)) blockerLines << line;
        }
      }
      const bool canRemoveRestricted = result.value(QStringLiteral("can_remove_and_continue")).toBool();
      const bool canSkipUnverified = result.value(QStringLiteral("can_skip_and_continue")).toBool();
      if (issues.isEmpty() && !canRemoveRestricted && !canSkipUnverified)
        return showError(QStringLiteral("Campanha não iniciada"),
                         blockerLines.join(QLatin1Char('\n')));
      std::function<void()> continueAction;
      if (canRemoveRestricted || canSkipUnverified) {
        QJsonObject continuation = body;
        continuation.insert(QStringLiteral("preflight_id"), result.value(QStringLiteral("preflight_id")));
        if (canRemoveRestricted) continuation.insert(QStringLiteral("remove_restricted"), true);
        if (canSkipUnverified) continuation.insert(QStringLiteral("skip_unverified"), true);
        continueAction = [this, continuation, result, selectedAccountNames,
                          canRemoveRestricted, canSkipUnverified] {
          QSet<QString> removed;
          for (const auto value : result.value("removable_accounts").toArray()) removed.insert(value.toString());
          for (const auto value : result.value("skippable_accounts").toArray()) removed.insert(value.toString());
          for (const auto value : result.value("removed_accounts").toArray()) removed.insert(value.toString());
          QStringList included;
          const auto requested = continuation.value("accounts").toArray();
          for (int i = 0; i < requested.size(); ++i)
            if (!removed.contains(requested[i].toString())) included.append(selectedAccountNames.value(i));
          if (included.isEmpty()) {
            showError(QStringLiteral("Campanha não iniciada"),
                QStringLiteral("Nenhuma conta aprovada permaneceu na seleção. Ajuste as contas antes de iniciar."));
            return;
          }
          auto reviewed = result;
          reviewed.insert("ok", true);
          reviewed.insert("blockers", QJsonArray{});
          reviewed.insert("account_errors", QJsonArray{});
          reviewed.insert("account_issues", QJsonArray{});
          auto metrics = reviewed.value("accounts").toObject();
          const int validated = metrics.value("validated").toInt();
          metrics.insert("validated", included.size());
          reviewed.insert("accounts", metrics);
          reviewed.insert("account_workers", qMin(reviewed.value("account_workers").toInt(), int(included.size())));
          if (CampaignReviewDialog::validCapacity(reviewed, continuation))
            reviewed.insert("estimated_sends", reviewed.value("capacity").toObject().value("estimated_sends"));
          else if (validated > 0)
            reviewed.insert("estimated_sends", reviewed.value("estimated_sends").toInt() * int(included.size()) / validated);
          auto warnings = reviewed.value("warnings").toArray();
          if (canSkipUnverified)
            warnings.append(QStringLiteral("%1 conta(s) com verificação inconclusiva ficarão fora somente desta campanha; os acessos continuam cadastrados.")
                                .arg(result.value("skippable_accounts").toArray().size()));
          if (canRemoveRestricted)
            warnings.append(QStringLiteral("Ao confirmar, %1 conta(s) com restrição confirmada serão movidas para Banidas.")
                                .arg(result.value("removable_accounts").toArray().size()));
          reviewed.insert("warnings", warnings);
          CampaignReviewDialog review(reviewed, included, this, continuation);
          setStatus(QStringLiteral("Confira a prévia antes de iniciar os envios."));
          if (review.exec() == QDialog::Accepted) submitCampaign(continuation);
          else setStatus(QStringLiteral("A revisão foi cancelada. Nenhum envio iniciado."));
        };
      }
      return showAccountIssues(QStringLiteral("Campanha não iniciada — verificação pendente"),
                               blockerLines, issues, continueAction);
    }

    const auto accountInfo = result.value(QStringLiteral("accounts")).toObject();
    const auto taskInfo = result.value(QStringLiteral("tasks")).toObject();
    QString preview = QStringLiteral(
        "%1 conta(s) validada(s)\n%2 categoria(s) compatível(is)\n%3 clipes disponíveis\n%4 envio(s) estimado(s)")
        .arg(accountInfo.value(QStringLiteral("validated")).toInt())
        .arg(taskInfo.value(QStringLiteral("compatible")).toInt())
        .arg(result.value(QStringLiteral("clips")).toInt())
        .arg(result.value(QStringLiteral("estimated_sends")).toInt());
    preview += QStringLiteral("\n\nContas escolhidas:\n")
        + selectedAccountNames.mid(0, 12).join(QLatin1Char('\n'));
    if (selectedAccountNames.size() > 12)
      preview += QStringLiteral("\n… e mais %1 conta(s)").arg(selectedAccountNames.size() - 12);
    if (!warningLines.isEmpty())
      preview += QStringLiteral("\n\nAtenções:\n") + warningLines.join(QLatin1Char('\n'));
    preview += QStringLiteral("\n\nIniciar esta campanha agora?");
    CampaignReviewDialog review(result, selectedAccountNames, this, body);
    if (review.exec() != QDialog::Accepted) {
      _campaignStart->setText(QStringLiteral("Iniciar campanha"));
      _campaignStart->setEnabled(!_campaignStartUncertain);
      return;
    }

    QJsonObject approved = body;
    approved.insert(QStringLiteral("preflight_id"), result.value(QStringLiteral("preflight_id")));
    submitCampaign(approved);
  }, [this](const QJsonObject& progress) {
    if (_closing || !_campaignVerifying) return;
    updateCampaignVerificationMonitor(progress);
    setStatus(progress.value(QStringLiteral("message")).toString(QStringLiteral("Verificando campanha…")));
  });
}

void MainWindow::submitCampaign(QJsonObject body) {
  const QString receipt = body.value(QStringLiteral("preflight_id")).toString().trimmed();
  if (receipt.isEmpty() || receipt != body.value(QStringLiteral("preflight_id")).toString()) {
    _campaignVerifying = false;
    _campaignStart->setText(QStringLiteral("Iniciar campanha"));
    _campaignStart->setEnabled(!_campaignActive && !_campaignStartUncertain);
    return showError(QStringLiteral("Campanha não iniciada"),
        QStringLiteral("A prévia não tem um identificador válido. Revise a campanha antes de iniciar."));
  }
  if (!rememberCampaignStart(receipt)) return;
  _campaignVerifying = true;
  _campaignStart->setEnabled(false);
    _campaignStart->setText(QStringLiteral("Iniciando…"));
    const int generation = _operationBackendGeneration;
    _api.post(QStringLiteral("/api/campaigns"), body,
              [this, generation, receipt](bool started, const QJsonDocument& startDoc, const QString& startError) {
      if (generation != _operationBackendGeneration || _pendingCampaignStartId != receipt) return;
      _campaignVerifying = false;
      _campaignStart->setText(QStringLiteral("Iniciar campanha"));
      if (!started) {
        const QString errorCode = startDoc.object().value(QStringLiteral("error_code")).toString();
        if (errorCode.contains(QStringLiteral("unknown"))
            || QStringList{QStringLiteral("start_state_unreadable"),
                           QStringLiteral("start_request_conflict")}.contains(errorCode)) {
          _campaignStart->setEnabled(false);
          _campaignStartUncertain = true;
          _campaignStart->setText(QStringLiteral("Início não confirmado"));
          setStatus(QStringLiteral("O início pode ter sido aceito. A interface consultará essa execução sem repetir o pedido."));
          pollCampaign();
          return showError(QStringLiteral("Início inconclusivo"),
              QStringLiteral("A resposta de início não foi confirmada. Consulte a operação e a recuperação antes de iniciar novamente."));
        }
        clearCampaignStart();
        _campaignStart->setEnabled(!_campaignActive && !_campaignStartUncertain);
        return showError(QStringLiteral("Campanha não iniciada"), startError);
      }
      const auto startReply = startDoc.object();
      const bool alreadyRunning = startReply.value(QStringLiteral("already_running")).toBool();
      const QString acknowledgedId = startReply.value(QStringLiteral("start_request_id")).toString();
      const bool confirmedOwnStart = acknowledgedId == receipt;
      if (!confirmedOwnStart && !(alreadyRunning && acknowledgedId.isEmpty())) {
        _campaignStartUncertain = true;
        _campaignStart->setEnabled(false);
        _campaignStart->setText(QStringLiteral("Início não confirmado"));
        setStatus(QStringLiteral("A resposta não confirmou o identificador desta campanha. Vou consultar o registro salvo sem repetir o início."));
        lookupCampaignStart();
        return;
      }
      clearCampaignStart();
      if (alreadyRunning) {
        _campaignActive = true;
        _campaignStart->setEnabled(false);
        _campaignReset->setEnabled(false);
        _campaignPoll.start();
        pollCampaign();
        return showError(QStringLiteral("Campanha já em andamento"),
                         QStringLiteral("A campanha anterior ainda está executando ou encerrando. "
                                        "Aguarde a conclusão antes de iniciar outra."));
      }
      auto used = QJsonDocument::fromJson(QSettings().value(
          QStringLiteral("campaign/accountLastUsed")).toByteArray()).object();
      const double startedAt = static_cast<double>(QDateTime::currentMSecsSinceEpoch());
      for (const auto value : startDoc.object().value(QStringLiteral("accounts")).toArray())
        used.insert(value.toString(), startedAt);
      QSettings().setValue(QStringLiteral("campaign/accountLastUsed"),
                           QJsonDocument(used).toJson(QJsonDocument::Compact));
      {
        const QSignalBlocker blocker(_campaignAccounts);
        for (const auto& removed : startDoc.object().value(QStringLiteral("removed_accounts")).toArray()) {
          for (int i = _campaignAccounts->count() - 1; i >= 0; --i)
            if (_campaignAccounts->item(i)->data(Qt::UserRole).toString() == removed.toString())
              delete _campaignAccounts->takeItem(i);
        }
      }
      updateCampaignAccountCount();
      _campaignActive = true;
      _campaignReset->setEnabled(false);
      _lastCampaignSeq = 0;
      _previewPoll.stop();
      _previewLogName.clear();
      _previewCheckActive = false;
      QSettings().remove(QStringLiteral("previewLogName"));
      _campaignFeed->clear();
      _campaignPoll.start();
      pollCampaign();
      setStatus(QStringLiteral("Campanha iniciada após preflight aprovado."));
    });
  }

bool MainWindow::rememberCampaignStart(const QString& identity) {
  if (!QRegularExpression(QStringLiteral("^[A-Za-z0-9_-]{1,160}$")).match(identity).hasMatch()) {
    showError(QStringLiteral("Campanha não iniciada"),
        QStringLiteral("O serviço não forneceu um identificador válido para acompanhar o início. Revise a prévia antes de tentar novamente."));
    return false;
  }
  if (_campaignStartUncertain && !_pendingCampaignStartId.isEmpty()
      && _pendingCampaignStartId != identity) {
    setStatus(QStringLiteral("Um início anterior ainda precisa ser confirmado. Ele não será substituído."));
    lookupCampaignStart();
    return false;
  }
  QSettings settings;
  settings.setValue(QStringLiteral("campaign/pendingStart"), QJsonDocument(QJsonObject{
      {QStringLiteral("schema_version"), 1},
      {QStringLiteral("start_request_id"), identity}}).toJson(QJsonDocument::Compact));
  settings.sync();
  if (settings.status() != QSettings::NoError) {
    _campaignStartUncertain = true;
    _campaignStart->setEnabled(false);
    showError(QStringLiteral("Campanha não iniciada"),
        QStringLiteral("Não foi possível salvar o identificador do início para recuperação. Revise o armazenamento local antes de tentar novamente."));
    return false;
  }
  _pendingCampaignStartId = identity;
  _campaignStartUncertain = true;
  return true;
}

void MainWindow::clearCampaignStart() {
  QSettings settings;
  settings.remove(QStringLiteral("campaign/pendingStart"));
  settings.sync();
  if (settings.status() == QSettings::NoError) {
    _pendingCampaignStartId.clear();
    _campaignStartUncertain = false;
  } else {
    _campaignStartUncertain = true;
    setStatus(QStringLiteral("O resultado foi identificado, mas o registro local não pôde ser atualizado. Novo início permanece bloqueado."));
  }
}

void MainWindow::lookupCampaignStart() {
  if (_campaignStartLookupInFlight || _pendingCampaignStartId.isEmpty()) return;
  _campaignStartLookupInFlight = true;
  const QString identity = _pendingCampaignStartId;
  _api.get(QStringLiteral("/api/campaigns/starts/%1").arg(identity),
      [this, identity](bool ok, const QJsonDocument& doc, const QString&) {
    _campaignStartLookupInFlight = false;
    if (identity != _pendingCampaignStartId) return;
    const auto result = doc.object();
    const QString status = result.value(QStringLiteral("status")).toString();
    const bool valid = ok && doc.isObject()
        && result.value(QStringLiteral("ok")) == QJsonValue(true)
        && result.value(QStringLiteral("start_request_id")) == QJsonValue(identity)
        && result.value(QStringLiteral("may_start")) == QJsonValue(false)
        && result.value(QStringLiteral("found")).isBool()
        && QStringList{QStringLiteral("admitted"), QStringLiteral("review"), QStringLiteral("not_found")}.contains(status);
    const QString execution = result.value(QStringLiteral("execution_state")).toString();
    const QString logName = result.value(QStringLiteral("log_name")).toString();
    const bool terminalRecord = valid
        && result.value(QStringLiteral("found")) == QJsonValue(true)
        && result.value(QStringLiteral("terminal")) == QJsonValue(true)
        && result.value(QStringLiteral("delivery_confirmed")).isBool()
        && QStringList{QStringLiteral("done"), QStringLiteral("partial"),
                       QStringLiteral("stopped"), QStringLiteral("error")}.contains(execution)
        && QRegularExpression(QStringLiteral("^campaign_[A-Za-z0-9_-]+\\.json$")).match(logName).hasMatch();
    if (terminalRecord) {
      clearCampaignStart();
      setStatus(QStringLiteral("O início anterior foi associado a uma execução salva. Consulte o Histórico e a Recuperação."));
      bool taskSelected = false;
      for (int i = 0; i < _campaignTasks->count(); ++i)
        taskSelected |= _campaignTasks->item(i)->checkState() == Qt::Checked
            && !_campaignTasks->item(i)->data(Qt::UserRole).toString().isEmpty();
      _campaignStart->setText(QStringLiteral("Iniciar campanha"));
      _campaignStart->setEnabled(!_campaignActive && !_campaignVerifying
                                 && taskSelected && !_campaignStartUncertain);
      _campaignReset->setEnabled(!_campaignActive && !_campaignVerifying && !_campaignStartUncertain);
      if (!_campaignActive) _campaignPoll.stop();
      return;
    }
    const QString detail = !valid
        ? QStringLiteral("O registro do início não pôde ser confirmado. Consulte a Recuperação antes de iniciar outra campanha.")
        : status == QStringLiteral("admitted")
            ? QStringLiteral("O serviço admitiu o início; o resultado ainda não está terminal. A solicitação não será repetida.")
        : status == QStringLiteral("not_found")
            ? QStringLiteral("O identificador não consta no registro consultado. Isso não confirma que nada foi enviado; revise Histórico e Recuperação.")
        : QStringLiteral("O resultado do início exige revisão na Recuperação. A solicitação não será repetida automaticamente.");
    _campaignCurrent->setText(detail);
    setStatus(detail);
  });
}

void MainWindow::pollCampaign() {
  _api.get(QStringLiteral("/api/campaigns/current?since=%1").arg(_lastCampaignSeq),
           [this](bool ok, const QJsonDocument& doc, const QString&) {
    if (!ok) return;
    const auto snap = doc.object();
    const QString state = snap.value(QStringLiteral("state")).toString();
    const bool running = state == QStringLiteral("running") || state == QStringLiteral("stopping");
    const QString confirmedStartId = snap.value(QStringLiteral("start_request_id")).toString();
    if (_campaignStartUncertain && !_pendingCampaignStartId.isEmpty()
        && confirmedStartId == _pendingCampaignStartId) {
      clearCampaignStart();
      _campaignStart->setText(QStringLiteral("Iniciar campanha"));
      setStatus(QStringLiteral("O início salvo foi confirmado pelo serviço."));
    } else if (_campaignStartUncertain && !_pendingCampaignStartId.isEmpty()) {
      lookupCampaignStart();
    }
    _campaignActive = running;
    // A recovered start must keep updating after its first status reply.
    if (running && _backendReady && !_closing && !_campaignPoll.isActive()) _campaignPoll.start();
    _campaignStop->setEnabled(running && state != QStringLiteral("stopping"));
    _campaignPaused = snap.value(QStringLiteral("pause_requested")).toBool();
    _campaignPause->setEnabled(running && state != QStringLiteral("stopping"));
    _campaignPause->setText(_campaignPaused ? QStringLiteral("Retomar") : QStringLiteral("Pausar"));
    bool taskSelected = false;
    for (int i = 0; i < _campaignTasks->count(); ++i)
      taskSelected |= _campaignTasks->item(i)->checkState() == Qt::Checked
          && !_campaignTasks->item(i)->data(Qt::UserRole).toString().isEmpty();
    _campaignStart->setEnabled(!running && !_campaignVerifying && taskSelected && !_campaignStartUncertain);
    _campaignReset->setEnabled(!running && !_campaignVerifying && !_campaignStartUncertain);
    onCampaignAvailabilityChanged();
    const QHash<QString, QString> stateLabels{
        {QStringLiteral("idle"), QStringLiteral("Aguardando")},
        {QStringLiteral("running"), QStringLiteral("Em andamento")},
        {QStringLiteral("stopping"), QStringLiteral("Encerrando")},
        {QStringLiteral("done"), QStringLiteral("Concluída")},
        {QStringLiteral("stopped"), QStringLiteral("Encerrada")},
        {QStringLiteral("error"), QStringLiteral("Atenção necessária")},
    };
    _campaignStage->setText(snap.value(QStringLiteral("stage")).toString(
        stateLabels.value(state, QStringLiteral("Aguardando"))));
    QString current = snap.value(QStringLiteral("current")).toString();
    if (current.isEmpty()) {
      if (state == QStringLiteral("done")) current = QStringLiteral("Resultados salvos no Histórico.");
      else if (state == QStringLiteral("stopped")) current = QStringLiteral("Parada concluída com segurança.");
      else if (state == QStringLiteral("error")) current = snap.value(QStringLiteral("error")).toString(
          QStringLiteral("A operação não foi concluída."));
      else if (running) current = QStringLiteral("Campanha em andamento…");
      else current = QStringLiteral("Nenhuma campanha em andamento.");
    }
    _campaignCurrent->setText(current);
    const auto totals = snap.value(QStringLiteral("totals")).toObject();
    const bool untilExhausted = snap.value(QStringLiteral("run_until_exhausted")).toBool();
    const bool hoursGoal = totals.value(QStringLiteral("progress_unit")).toString() == QStringLiteral("seconds");
    const double total = totals.value(QStringLiteral("progress_target")).toDouble(
        totals.value(QStringLiteral("total_sends")).toDouble());
    const double done = totals.value(QStringLiteral("progress_completed")).toDouble(
        totals.value(QStringLiteral("done_sends")).toDouble());
    const int successful = totals.value(QStringLiteral("ok_sends")).toInt();
    const int failed = totals.value(QStringLiteral("failed_sends")).toInt();
    const int skipped = totals.value(QStringLiteral("skipped_sends")).toInt();
    const int percent = total > 0 ? qBound(0, int(done * 100 / total), 100) : 0;
    _campaignProgress->setValue(percent);
    _campaignProgress->setFormat(untilExhausted
        ? QStringLiteral("%1 envios confirmados · até esgotar o conteúdo").arg(successful)
        : total > 0
        ? hoursGoal ? QStringLiteral("Meta: %1 de %2 h · %p%")
              .arg(done / 3600., 0, 'f', 2).arg(total / 3600., 0, 'f', 2)
            : QStringLiteral("%1 de %2 envios · %p%").arg(done).arg(total)
        : QStringLiteral("Calculando os envios…"));
    _campaignStats->setText(QStringLiteral("%1 sucesso · %2 ignorados · %3 falhas")
        .arg(successful).arg(skipped).arg(failed));
    for (const auto eventValue : snap.value(QStringLiteral("events")).toArray()) {
      const auto event = eventValue.toObject();
      if (event.value(QStringLiteral("permanently_removed")).toBool()) {
        const QString removedEmail = event.value(QStringLiteral("email")).toString();
        {
          const QSignalBlocker blocker(_campaignAccounts);
          for (int i = _campaignAccounts->count() - 1; i >= 0; --i)
            if (_campaignAccounts->item(i)->data(Qt::UserRole).toString() == removedEmail)
              delete _campaignAccounts->takeItem(i);
        }
        updateCampaignAccountCount();
        notifyPermanentCampaignAccountRemoval(removedEmail);
      }
      _lastCampaignSeq = qMax(_lastCampaignSeq, event.value(QStringLiteral("seq")).toInt());
      const QString level = event.value(QStringLiteral("level")).toString();
      const QString marker = level == QStringLiteral("success") ? QStringLiteral("✓")
                           : level == QStringLiteral("warning") ? QStringLiteral("!")
                           : level == QStringLiteral("error") ? QStringLiteral("×")
                           : QStringLiteral("•");
      const qint64 seconds = static_cast<qint64>(event.value(QStringLiteral("ts")).toDouble());
      const QString time = QDateTime::fromSecsSinceEpoch(seconds).toLocalTime()
                               .toString(QStringLiteral("HH:mm:ss"));
      const QString title = event.value(QStringLiteral("title")).toString();
      const QString detail = event.value(QStringLiteral("detail")).toString();
      if (title.isEmpty()) continue;
      QString line = QStringLiteral("%1   %2  %3").arg(time, marker, title);
      if (!detail.isEmpty()) line += QStringLiteral("\n             %1").arg(detail);
      _campaignFeed->appendPlainText(line);
    }
    if (!running) {
      _campaignPoll.stop();
      const QString logName = QFileInfo(
          snap.value(QStringLiteral("log_path")).toString()).fileName();
      if (state == QStringLiteral("done") && successful > 0 && !logName.isEmpty()
          && _previewLogName.isEmpty()) {
        _previewLogName = logName;
        QSettings().setValue(QStringLiteral("previewLogName"), logName);
        _previewPoll.start();
        pollCampaignPreviews();
      }
    }
  });
}

void MainWindow::pollCampaignPreviews() {
  if (_previewLogName.isEmpty() || _previewCheckActive) return;
  _previewCheckActive = true;
  _api.post(QStringLiteral("/api/logs/") + encoded(_previewLogName)
                + QStringLiteral("/status"), {},
            [this](bool ok, const QJsonDocument& doc, const QString& error) {
    _previewCheckActive = false;
    if (!ok) {
      if (error.contains(QStringLiteral("log não encontrado"), Qt::CaseInsensitive)) {
        _previewPoll.stop();
        _previewLogName.clear();
        QSettings().remove(QStringLiteral("previewLogName"));
        _campaignStage->setText(QStringLiteral("Atenção no histórico"));
        _campaignCurrent->setText(QStringLiteral(
            "O registro salvo para acompanhar as prévias não foi encontrado."));
        setStatus(error);
        return;
      }
      _campaignStage->setText(QStringLiteral("Aguardando o Minute"));
      _campaignCurrent->setText(QStringLiteral(
          "Os vídeos foram enviados. A consulta das prévias será repetida automaticamente."));
      setStatus(QStringLiteral("Minute ainda não respondeu sobre as prévias: %1").arg(error));
      return;
    }
    const auto summary = doc.object().value(QStringLiteral("summary")).toObject();
    const int total = summary.value(QStringLiteral("total")).toInt();
    const int ready = summary.value(QStringLiteral("ready")).toInt();
    const int pending = summary.value(QStringLiteral("pending")).toInt();
    const int unavailable = summary.value(QStringLiteral("unavailable")).toInt();
    const int errors = summary.value(QStringLiteral("errors")).toInt();
    const int transientErrors = summary.value(QStringLiteral("transient_errors")).toInt();
    const int terminalErrors = qMax(0, errors - transientErrors);
    const int finished = ready + unavailable + terminalErrors;
    const int percent = total > 0 ? qBound(0, finished * 100 / total, 100) : 100;
    _campaignProgress->setValue(percent);
    _campaignProgress->setFormat(total > 0
        ? QStringLiteral("%1 de %2 prévias prontas · %p%").arg(ready).arg(total)
        : QStringLiteral("Nenhuma sessão enviada"));
    _campaignStats->setText(QStringLiteral(
        "%1 prontas · %2 processando · %3 falhas")
        .arg(ready).arg(pending).arg(unavailable + errors));

    if (total <= 0) {
      _previewPoll.stop();
      _previewLogName.clear();
      QSettings().remove(QStringLiteral("previewLogName"));
      _campaignStage->setText(QStringLiteral("Nenhum envio confirmado"));
      _campaignCurrent->setText(QStringLiteral(
          "A campanha terminou sem uma sessão remota para acompanhar. Veja o Histórico para identificar o motivo."));
      setStatus(QStringLiteral("A campanha não possui arquivos enviados ao Minute."));
      return;
    }

    if (pending > 0 || transientErrors > 0) {
      _campaignStage->setText(transientErrors > 0
          ? QStringLiteral("Confirmando no Minute")
          : QStringLiteral("Processamento no Minute"));
      _campaignCurrent->setText(transientErrors > 0
          ? QStringLiteral(
                "%1 de %2 arquivos publicados. %3 consulta(s) falharam temporariamente e serão repetidas automaticamente.")
                .arg(ready).arg(total).arg(transientErrors)
          : QStringLiteral(
                "%1 de %2 arquivos publicados. Os vídeos já foram recebidos; o Minute está processando o restante.")
                .arg(ready).arg(total));
      setStatus(QStringLiteral("Prévias no Minute: %1 prontas, %2 processando, %3 consultas pendentes.")
                    .arg(ready).arg(pending).arg(transientErrors));
      return;
    }

    _previewPoll.stop();
    _previewLogName.clear();
    QSettings().remove(QStringLiteral("previewLogName"));
    if (unavailable > 0 || errors > 0) {
      _campaignStage->setText(QStringLiteral("Atenção nas prévias"));
      _campaignCurrent->setText(QStringLiteral(
          "%1 prévia(s) pronta(s); %2 precisam de atenção. Veja os detalhes no Histórico.")
          .arg(ready).arg(unavailable + errors));
      _campaignFeed->appendPlainText(QStringLiteral(
          "!   Minute concluiu a fila com %1 prévia(s) que precisam de atenção.")
          .arg(unavailable + errors));
    } else {
      _campaignStage->setText(QStringLiteral("Concluída"));
      _campaignCurrent->setText(QStringLiteral(
          "Todas as %1 prévias foram publicadas no Minute.").arg(ready));
      _campaignFeed->appendPlainText(QStringLiteral(
          "✓   Minute publicou todas as %1 prévias.").arg(ready));
      setStatus(QStringLiteral("Campanha concluída: todas as prévias estão prontas no Minute."));
    }
  });
}

void MainWindow::loadAccelerator() {
  const QString provider = _cacheProvider && !_cacheProvider->currentData().toString().isEmpty()
      ? _cacheProvider->currentData().toString() : QStringLiteral("holoassist");
  const QString requestedTask = _cacheTask->count() ? _cacheTask->currentText() : QString();
  QString path = QStringLiteral("/api/holo-cache?provider=%1").arg(encoded(provider));
  if (!requestedTask.isEmpty()) {
    path += QStringLiteral("&task=%1&limit=%2").arg(encoded(requestedTask)).arg(_cacheLimit->value());
  }
  if (provider == QStringLiteral("ego4d") && _cacheBudget) {
    if (_cacheBudgetLoaded)
      path += QStringLiteral("&budget_gb=%1").arg(_cacheBudget->value());
    path += QStringLiteral("&min_free_gb=%1").arg(_cacheReserve->value());
  }
  const bool live = _cachePoll.isActive()
      && _cacheCatalogSnapshot.value(QStringLiteral("provider")).toString() == provider
      && (requestedTask.isEmpty()
          || _cacheCatalogSnapshot.value(QStringLiteral("task")).toString() == requestedTask);
  if (live) path += QStringLiteral("&live=1");
  if (_cacheInFlightKey == path) return;
  if (_cacheRequestKey != path) {
    _cacheRequestKey = path;
    ++_cacheRequestId;
  }
  const auto requestId = _cacheRequestId;
  _cacheInFlightKey = path;
  _api.get(path, [this, provider, requestedTask, requestId, path, live](bool ok, const QJsonDocument& doc, const QString& error) {
    if (_cacheInFlightKey == path) _cacheInFlightKey.clear();
    if (requestId != _cacheRequestId) return;
    if (!ok) {
      _cacheState->setText(QStringLiteral("Não foi possível consultar o acelerador"));
      _cacheLastRun->setText(error);
      return setStatus(error);
    }
    if (_cacheProvider && _cacheProvider->currentData().toString() != provider) return;
    if (!requestedTask.isEmpty() && _cacheTask->currentText() != requestedTask) return;
    const auto root = doc.object();
    if (_cacheTask->count() == 0) {
      const QString preferred = root.value(QStringLiteral("default_task")).toString();
      _cacheTask->blockSignals(true);
      int select = 0;
      int index = 0;
      for (const auto task : root.value(QStringLiteral("tasks")).toArray()) {
        const QString name = task.toString();
        _cacheTask->addItem(name);
        if (name == preferred) select = index;
        ++index;
      }
      if (_cacheTask->count()) _cacheTask->setCurrentIndex(select);
      _cacheTask->blockSignals(false);
      const QString served = root.value(QStringLiteral("cache")).toObject()
          .value(QStringLiteral("task")).toString();
      if (_cacheTask->count() && !served.isEmpty() && _cacheTask->currentText() != served) {
        loadAccelerator();
        return;
      }
    }
    QJsonObject cache;
    if (live) {
      cache = _cacheCatalogSnapshot;
      cache.insert(QStringLiteral("last_run"), root.value(QStringLiteral("last_run")));
    } else {
      cache = root.value(QStringLiteral("cache")).toObject();
      cache.insert(QStringLiteral("provider"), provider);
      _cacheCatalogSnapshot = cache;
    }
    if (!live && provider == QStringLiteral("ego4d") && cache.contains(QStringLiteral("max_budget_gb"))) {
      _cacheBudget->blockSignals(true);
      _cacheBudget->setMaximum(cache.value(QStringLiteral("max_budget_gb")).toInt());
      if (!_cacheBudgetLoaded) {
        _cacheBudget->setValue(cache.value(QStringLiteral("requested_budget_gb")).toInt());
        _cacheBudgetLoaded = true;
      }
      _cacheBudget->setSuffix(_cacheBudget->value() == 0 ? QString() : QStringLiteral(" GB"));
      _cacheBudget->setToolTip(QStringLiteral("Máximo neste disco: %1 GB · livres: %2 GiB · reserva: %3 GiB")
          .arg(_cacheBudget->maximum()).arg(cache.value(QStringLiteral("free_gb")).toDouble())
          .arg(_cacheReserve->value()));
      _cacheBudget->blockSignals(false);
      _cacheStart->setText(_cacheBudget->value() == 0 ? QStringLiteral("Desativar pré-cache")
                                                    : QStringLiteral("Preparar cache"));
      _cacheBudgetHelp->setText(_cacheBudget->maximum() > 0
          ? QStringLiteral("Até %1 GB neste disco, mantendo %2 GiB livres. 0 GB desativa o pré-cache; a campanha ainda pode buscar vídeos sob demanda.")
                .arg(_cacheBudget->maximum()).arg(_cacheReserve->value())
          : QStringLiteral("Sem espaço para novo cache acima da reserva de %1 GiB. Reduza a reserva ou libere espaço no disco.")
                .arg(_cacheReserve->value()));
    }
    const auto runner = root.value(QStringLiteral("runner")).toObject();
    const int total = cache.value(QStringLiteral("total")).toInt();
    const int ready = cache.value(QStringLiteral("ready")).toInt();
    const int partial = cache.value(QStringLiteral("partial")).toInt();
    const int pending = cache.value(QStringLiteral("pending")).toInt();
    const QString state = runner.value(QStringLiteral("state")).toString();
    const bool running = state == QStringLiteral("running") || state == QStringLiteral("stopping");
    const bool runningHere = running && runner.value(QStringLiteral("provider")).toString() == provider;
    const auto lastRun = cache.value(QStringLiteral("last_run")).toObject();
    const QString lastStatus = lastRun.value(QStringLiteral("status")).toString();
    const QString catalogError = cache.value(QStringLiteral("catalog_error")).toString();
    const int runTotal = runner.value(QStringLiteral("total")).toInt();
    const int runReady = runner.value(QStringLiteral("ready")).toInt();
    const int runFailed = runner.value(QStringLiteral("failed")).toInt();
    const int processed = qMin(runTotal, runReady + runFailed);
    if (runningHere)
      _cacheState->setText(state == QStringLiteral("stopping")
          ? QStringLiteral("Parando após o clipe atual…")
          : runner.value(QStringLiteral("current")).toString(QStringLiteral("Preparando catálogo…")));
    else if (running)
      _cacheState->setText(QStringLiteral("Outro acelerador está em execução"));
    else if (state == QStringLiteral("error"))
      _cacheState->setText(QStringLiteral("Acelerador falhou: %1").arg(runner.value(QStringLiteral("error")).toString()));
    else if (lastStatus == QStringLiteral("running"))
      _cacheState->setText(QStringLiteral("Execução anterior interrompida; confira antes de retomar"));
    else if (!catalogError.isEmpty())
      _cacheState->setText(QStringLiteral("Catálogo indisponível: %1").arg(catalogError));
    else if (lastStatus == QStringLiteral("complete"))
      _cacheState->setText(lastRun.value(QStringLiteral("failed")).toInt() > 0
          ? QStringLiteral("Última preparação concluída com falhas")
          : QStringLiteral("Última preparação concluída"));
    else if (lastStatus == QStringLiteral("budget"))
      _cacheState->setText(QStringLiteral("Parou no limite de cache escolhido"));
    else if (lastStatus == QStringLiteral("disk_limit"))
      _cacheState->setText(QStringLiteral("Parou para preservar espaço livre"));
    else if (lastStatus == QStringLiteral("stopped"))
      _cacheState->setText(QStringLiteral("Preparação parada; pode retomar"));
    else
      _cacheState->setText(QStringLiteral("Nenhuma preparação em andamento"));
    const int budgetGb = cache.value(QStringLiteral("budget_gb")).toInt();
    const double usedGb = cache.value(QStringLiteral("used_gb")).toDouble();
    if (runningHere && runTotal <= 0) {
      _cacheProgress->setRange(0, 0);
      _cacheProgress->setFormat(QStringLiteral("Montando fila de clipes…"));
      _cacheNumbers->setText(QStringLiteral("Preparando catálogo e conferindo arquivos locais"));
    } else if (runningHere) {
      _cacheProgress->setRange(0, 100);
      _cacheProgress->setValue(processed * 100 / runTotal);
      _cacheProgress->setFormat(QStringLiteral("%1 de %2 clipes processados · %p%")
          .arg(processed).arg(runTotal));
      _cacheNumbers->setText(QStringLiteral("%1 pronto(s) · %2 falha(s) · clipe %3 de %4")
          .arg(runReady).arg(runFailed).arg(runner.value(QStringLiteral("index")).toInt()).arg(runTotal));
    } else {
      _cacheProgress->setRange(0, 100);
      _cacheProgress->setValue(total > 0 ? ready * 100 / total : 0);
      _cacheProgress->setFormat(total > 0
          ? QStringLiteral("%1 de %2 clipes prontos · %p%").arg(ready).arg(total)
          : QStringLiteral("Nenhum clipe planejado"));
      _cacheNumbers->setText(provider == QStringLiteral("ego4d") && budgetGb > 0
          ? QStringLiteral("%1 de %2 GB ocupados · %3 prontos · %4 parciais · %5 pendentes")
                .arg(QLocale().toString(usedGb, 'f', 1)).arg(budgetGb).arg(ready).arg(partial).arg(pending)
          : QStringLiteral("%1 prontos · %2 parciais · %3 pendentes")
                .arg(ready).arg(partial).arg(pending));
    }
    if (!running && catalogError.isEmpty() && lastStatus.isEmpty()
        && provider == QStringLiteral("ego4d") && _cacheBudget->value() == 0)
      _cacheState->setText(root.value(QStringLiteral("configured_budget_gb")).toInt() == 0
          ? QStringLiteral("Pré-cache Ego4D desativado")
          : QStringLiteral("0 GB selecionado · clique para desativar o pré-cache"));
    const double updatedAt = lastRun.value(QStringLiteral("updated_at")).toDouble();
    const QString when = updatedAt > 0
        ? QDateTime::fromSecsSinceEpoch(static_cast<qint64>(updatedAt)).toLocalTime()
              .toString(QStringLiteral("dd/MM/yyyy HH:mm"))
        : QStringLiteral("horário indisponível");
    if (lastStatus.isEmpty())
      _cacheLastRun->setText(QStringLiteral("Nenhuma preparação anterior registrada neste computador."));
    else {
      const QHash<QString, QString> statusLabels{
          {QStringLiteral("running"), QStringLiteral("em andamento")},
          {QStringLiteral("complete"), QStringLiteral("concluída")},
          {QStringLiteral("stopped"), QStringLiteral("parada pelo usuário")},
          {QStringLiteral("budget"), QStringLiteral("limite de cache atingido")},
          {QStringLiteral("disk_limit"), QStringLiteral("limite de espaço livre")},
          {QStringLiteral("provider"), QStringLiteral("pré-cache desativado")},
      };
      _cacheLastRun->setText(QStringLiteral("Última execução (%1): %2 · %3 de %4 prontos · %5 falhas · registro em %6")
          .arg(lastRun.value(QStringLiteral("task")).toString(QStringLiteral("tarefa não registrada")))
          .arg(lastStatus == QStringLiteral("running") && !runningHere
                   ? QStringLiteral("interrompida") : statusLabels.value(lastStatus, lastStatus),
               QString::number(lastRun.value(QStringLiteral("ready")).toInt()),
               QString::number(lastRun.value(QStringLiteral("total")).toInt()),
               QString::number(lastRun.value(QStringLiteral("failed")).toInt()), when));
      const int reclaimedFiles = lastRun.value(QStringLiteral("reclaimed_files")).toInt();
      if (reclaimedFiles > 0)
        _cacheLastRun->setText(_cacheLastRun->text()
            + QStringLiteral("\nCache obsoleto recuperado: %1 arquivo(s), %2 GB.")
                  .arg(reclaimedFiles)
                  .arg(QLocale().toString(
                      lastRun.value(QStringLiteral("reclaimed_bytes")).toDouble() / (1024.0 * 1024 * 1024), 'f', 1)));
      const auto errors = lastRun.value(QStringLiteral("errors")).toArray();
      if (!errors.isEmpty())
        _cacheLastRun->setText(_cacheLastRun->text() + QStringLiteral("\nÚltima falha: ")
            + errors.last().toObject().value(QStringLiteral("error")).toString());
    }
    const bool disablingCache = provider == QStringLiteral("ego4d")
        && _cacheBudget && _cacheBudget->value() == 0;
    _cacheStart->setEnabled(!running && (catalogError.isEmpty() || disablingCache));
    if (!disablingCache && provider == QStringLiteral("ego4d")
        && (lastStatus == QStringLiteral("running") || lastStatus == QStringLiteral("stopped")
            || lastStatus == QStringLiteral("disk_limit") || lastStatus == QStringLiteral("budget")))
      _cacheStart->setText(QStringLiteral("Retomar preparação"));
    _cacheStop->setEnabled(running && state != QStringLiteral("stopping"));
    if (running) _cachePoll.start(); else _cachePoll.stop();
    if (live && !running) QTimer::singleShot(0, this, &MainWindow::loadAccelerator);
  });
}

void MainWindow::startAccelerator() {
  if (_cacheTask->currentText().isEmpty()) return;
  const QString provider = _cacheProvider && !_cacheProvider->currentData().toString().isEmpty()
      ? _cacheProvider->currentData().toString() : QStringLiteral("holoassist");
  QJsonObject body{{QStringLiteral("provider"), provider},
                   {QStringLiteral("task"), _cacheTask->currentText()},
                   {QStringLiteral("min_free_gb"), _cacheReserve->value()}};
  if (_cacheLimit->value() > 0) body.insert(QStringLiteral("limit"), _cacheLimit->value());
  else body.insert(QStringLiteral("limit"), QJsonValue::Null);
  if (provider == QStringLiteral("ego4d") && _cacheBudget)
    body.insert(QStringLiteral("budget_gb"), _cacheBudget->value());
  _cacheStart->setEnabled(false);
  _api.post(QStringLiteral("/api/holo-cache/start"), body,
            [this](bool ok, const QJsonDocument&, const QString& error) {
    if (!ok) {
      _cacheStart->setEnabled(true);
      return showError(QStringLiteral("Acelerador não iniciado"), error);
    }
    _cachePoll.start();
    loadAccelerator();
  });
}

void MainWindow::updateCampaignAccountCount() {
  if (!_campaignAccounts || !_campaignAccountSelection) return;
  int selected = 0;
  for (int i = 0; i < _campaignAccounts->count(); ++i)
    if (_campaignAccounts->item(i)->checkState() == Qt::Checked) ++selected;
  _campaignAccountSelection->setText(QStringLiteral("%1 de %2 contas selecionadas")
      .arg(selected).arg(_campaignAccounts->count()));
}

void MainWindow::saveCampaignDraft() {
  if (!_campaignAccounts) return;
  QJsonArray accounts;
  for (int i = 0; i < _campaignAccounts->count(); ++i)
    if (_campaignAccounts->item(i)->checkState() == Qt::Checked)
      accounts.append(_campaignAccounts->item(i)->data(Qt::UserRole).toString());
  QJsonArray tasks;
  for (const QString& id : _campaignSelectedTaskIds) tasks.append(id);
  const QJsonObject draft{
      {QStringLiteral("accounts"), accounts},
      {QStringLiteral("tasks"), tasks},
      {QStringLiteral("tasks_touched"), _campaignTaskSelectionTouched},
      {QStringLiteral("mode"), _campaignAccountMode->currentData().toString()},
      {QStringLiteral("quantity"), _campaignAccountCount->value()},
      {QStringLiteral("dataset"), _dataset->currentData().toString()},
      {QStringLiteral("content_mode"), _contentMode->currentData().toString()},
      {QStringLiteral("target_hours"), _targetHours->value()},
      {QStringLiteral("run_until_exhausted"), _runUntilExhausted->isChecked()},
      {QStringLiteral("min_duration"), _minDuration->value()},
      {QStringLiteral("max_duration"), _maxDuration->value()},
      {QStringLiteral("delay_mode"), _delayMode->currentData().toString()},
      {QStringLiteral("delay_seconds"), _delaySeconds->value()},
      {QStringLiteral("cleanup"), _cleanupAfter->isChecked()},
      {QStringLiteral("active_hours"), _activeHours->isChecked()},
      {QStringLiteral("hour_start"), _hourStart->value()},
      {QStringLiteral("hour_end"), _hourEnd->value()},
  };
  QSettings().setValue(QStringLiteral("campaign/draft"),
                       QJsonDocument(draft).toJson(QJsonDocument::Compact));
}

void MainWindow::drawCampaignAccounts() {
  if (!_campaignAccounts || _campaignAccounts->count() == 0) {
    updateCampaignAccountCount();
    return;
  }
  const QString mode = _campaignAccountMode->currentData().toString();
  if (mode.startsWith(QStringLiteral("balance_")) && !_campaignBalancesLoaded) {
    loadCampaignBalances();
    return;
  }
  const QSignalBlocker accountChanges(_campaignAccounts);
  QVector<int> indexes;
  indexes.reserve(_campaignAccounts->count());
  for (int i = 0; i < _campaignAccounts->count(); ++i) indexes.append(i);
  if (mode.startsWith(QStringLiteral("balance_"))) {
    const auto balances = _campaignBalances.value(QStringLiteral("balances")).toObject();
    const auto kinds = _campaignBalances.value(QStringLiteral("account_kinds")).toObject();
    QSet<QString> connected;
    for (const auto value : _campaignBalances.value(QStringLiteral("with_password")).toArray())
      connected.insert(value.toString());
    QSet<QString> needsRefresh;
    for (const auto value : _campaignBalances.value(QStringLiteral("refresh_needed")).toArray())
      needsRefresh.insert(value.toString());
    const QString field = mode.contains(QStringLiteral("available"))
        ? QStringLiteral("availableCents") : QStringLiteral("pendingCents");
    const bool descending = mode.endsWith(QStringLiteral("desc"));
    QVector<int> eligible;
    for (int i : indexes) {
      auto* item = _campaignAccounts->item(i);
      const QString email = item->data(Qt::UserRole).toString();
      const auto record = balances.value(email).toObject();
      const auto value = record.value(field);
      item->setText(email);
      if (!connected.contains(email) || kinds.value(email).toString() != QStringLiteral("crowtado")
          || needsRefresh.contains(email) || !record.value(QStringLiteral("error")).toString().isEmpty()
          || record.value(QStringLiteral("stale")).toBool() || !value.isDouble()
          || !std::isfinite(value.toDouble()) || value.toDouble() < 0) {
        item->setToolTip(QStringLiteral("Sem saldo Crowtado confirmado nas últimas 24 horas. Atualize na aba Saldos."));
        continue;
      }
      item->setToolTip(QStringLiteral("%1: %2 · atualizado em %3")
          .arg(field == QStringLiteral("availableCents") ? QStringLiteral("Aprovado")
               : QStringLiteral("Pendente"), usdMoney(static_cast<qint64>(value.toDouble())),
               friendlyDate(record.value(QStringLiteral("updated_at")).toString())));
      item->setText(QStringLiteral("%1  ·  %2").arg(email,
          usdMoney(static_cast<qint64>(value.toDouble()))));
      eligible.append(i);
    }
    std::sort(eligible.begin(), eligible.end(), [this, &balances, &field, descending](int left, int right) {
      const QString leftEmail = _campaignAccounts->item(left)->data(Qt::UserRole).toString();
      const QString rightEmail = _campaignAccounts->item(right)->data(Qt::UserRole).toString();
      const double leftValue = balances.value(leftEmail).toObject().value(field).toDouble();
      const double rightValue = balances.value(rightEmail).toObject().value(field).toDouble();
      if (leftValue != rightValue) return descending ? leftValue > rightValue : leftValue < rightValue;
      return leftEmail.compare(rightEmail, Qt::CaseInsensitive) < 0;
    });
    indexes = eligible;
    _campaignBalanceHint->setText(QStringLiteral(
        "%1 conta(s) com %2 confirmado nas últimas 24 horas. %3 Atualize os valores na aba Saldos antes de decidir.")
        .arg(eligible.size())
        .arg(field == QStringLiteral("availableCents") ? QStringLiteral("saldo aprovado")
             : QStringLiteral("saldo pendente"))
        .arg(eligible.size() < _campaignAccountCount->value()
             ? QStringLiteral("A quantidade pedida supera as contas elegíveis.") : QString()));
  } else {
    for (int i : indexes) {
      auto* item = _campaignAccounts->item(i);
      item->setText(item->data(Qt::UserRole).toString());
      item->setToolTip(item->data(Qt::UserRole).toString());
    }
    for (int i = 0; i < indexes.size(); ++i) {
      const int pick = i + QRandomGenerator::global()->bounded(indexes.size() - i);
      std::swap(indexes[i], indexes[pick]);
    }
  }
  if (mode == QStringLiteral("rotation")) {
    const auto used = QJsonDocument::fromJson(QSettings().value(
        QStringLiteral("campaign/accountLastUsed")).toByteArray()).object();
    std::stable_sort(indexes.begin(), indexes.end(), [this, &used](int left, int right) {
      const auto emailLeft = _campaignAccounts->item(left)->data(Qt::UserRole).toString();
      const auto emailRight = _campaignAccounts->item(right)->data(Qt::UserRole).toString();
      return used.value(emailLeft).toDouble() < used.value(emailRight).toDouble();
    });
  }
  const int wanted = qMin(_campaignAccountCount->value(), indexes.size());
  QSet<int> selected;
  for (int i = 0; i < wanted; ++i) selected.insert(indexes[i]);
  {
    const QSignalBlocker blocker(_campaignAccounts);
    for (int i = 0; i < _campaignAccounts->count(); ++i)
      _campaignAccounts->item(i)->setCheckState(selected.contains(i) ? Qt::Checked : Qt::Unchecked);
  }
  updateCampaignAccountCount();
  _campaignStart->setEnabled(false);
  _taskReload.start();
  _campaignDraftSave.start();
}

void MainWindow::loadCampaignBalances() {
  const int requestId = ++_campaignBalanceRequestId;
  _campaignBalanceHint->setText(QStringLiteral("Lendo saldos salvos…"));
  _campaignDrawAccounts->setEnabled(false);
  _campaignStart->setEnabled(false);
  {
    const QSignalBlocker blocker(_campaignAccounts);
    for (int i = 0; i < _campaignAccounts->count(); ++i)
      _campaignAccounts->item(i)->setCheckState(Qt::Unchecked);
  }
  updateCampaignAccountCount();
  _api.get(QStringLiteral("/api/balances"), [this, requestId](bool ok, const QJsonDocument& doc,
                                                             const QString& error) {
    if (requestId != _campaignBalanceRequestId
        || !_campaignAccountMode->currentData().toString().startsWith(QStringLiteral("balance_"))) return;
    _campaignDrawAccounts->setEnabled(true);
    if (!ok) {
      _campaignBalanceHint->setText(QStringLiteral("Não foi possível ler os saldos: %1").arg(error));
      const QSignalBlocker blocker(_campaignAccounts);
      for (int i = 0; i < _campaignAccounts->count(); ++i)
        _campaignAccounts->item(i)->setCheckState(Qt::Unchecked);
      updateCampaignAccountCount();
      _campaignStart->setEnabled(false);
      return;
    }
    _campaignBalances = doc.object();
    _campaignBalancesLoaded = true;
    drawCampaignAccounts();
  });
}

QWidget* MainWindow::buildCampaignPage() {
  auto* body = new QWidget;
  auto* bodyLayout = new QVBoxLayout(body);
  bodyLayout->setContentsMargins(0, 0, 0, 0);
  bodyLayout->setSpacing(14);

  auto* scroll = new QScrollArea;
  scroll->setWidgetResizable(true);
  scroll->setFrameShape(QFrame::NoFrame);
  auto* content = new QWidget;
  auto* layout = new QVBoxLayout(content);
  layout->setContentsMargins(0, 0, 8, 0);
  layout->setSpacing(14);

  auto* sourceBody = new QWidget;
  auto* sourceColumn = new QVBoxLayout(sourceBody);
  sourceColumn->setContentsMargins(0, 0, 0, 0);
  auto* sourceLayout = new QHBoxLayout;
  sourceColumn->addLayout(sourceLayout);
  _dataset = new ComboBox;
  configureCombo(_dataset, 180);
  _dataset->addItem(QStringLiteral("Conteúdo combinado"), QStringLiteral("all"));
  _dataset->addItem(QStringLiteral("Somente Ego4D"), QStringLiteral("ego4d"));
  _dataset->addItem(QStringLiteral("Somente HoloAssist"), QStringLiteral("holoassist"));
  _dataset->addItem(QStringLiteral("Somente NymeriaPlus"), QStringLiteral("nymeria"));
  _dataset->addItem(QStringLiteral("Ego4D + NymeriaPlus"), QStringLiteral("ambos"));
  _dataset->setCurrentIndex(1);
  connect(_dataset, &QComboBox::currentIndexChanged, this,
          [this] { _taskReload.start(); });
  sourceLayout->addWidget(new QLabel(QStringLiteral("Origem")));
  sourceLayout->addWidget(_dataset, 1);
  _contentMode = new ComboBox;
  configureCombo(_contentMode, 190);
  _contentMode->addItem(QStringLiteral("Sob demanda + mídia já preparada"), QStringLiteral("both"));
  _contentMode->addItem(QStringLiteral("Somente cache pronto"), QStringLiteral("cache"));
  _contentMode->addItem(QStringLiteral("Sob demanda (sem pré-cache)"), QStringLiteral("dataset"));
  _contentMode->setCurrentIndex(2);
  _contentMode->setToolTip(QStringLiteral(
      "Sob demanda busca as fontes necessárias durante a campanha, sem preparar cache antecipadamente. "
      "A opção com mídia já preparada reaproveita arquivos locais; somente cache limita a seleção aos prontos."));
  connect(_contentMode, &QComboBox::currentIndexChanged, this,
          [this] { _taskReload.start(); });
  auto* reloadTasks = new QPushButton(QStringLiteral("Recarregar categorias"));
  connect(reloadTasks, &QPushButton::clicked, this, [this] {
    _taskCatalogFallbackAttempted.clear();
    _taskCatalogFallbackExhausted = false;
    _taskCatalogForceRefresh = true;
    loadTasks();
  });
  sourceLayout->addWidget(reloadTasks);
  _campaignReset = new QPushButton(QStringLiteral("Resetar lista"));
  _campaignReset->setToolTip(QStringLiteral(
      "Limpa a lista de vídeos já utilizados e permite que eles sejam selecionados novamente."));
  connect(_campaignReset, &QPushButton::clicked, this, [this] {
    const auto choice = QMessageBox::warning(
        this, QStringLiteral("Resetar lista de vídeos usados"),
        QStringLiteral("Os vídeos registrados como já utilizados poderão ser selecionados "
                       "novamente nas próximas campanhas.\n\n"
                       "O histórico das campanhas e os dados das contas serão preservados. "
                       "Deseja continuar?"),
        QMessageBox::Reset | QMessageBox::Cancel, QMessageBox::Cancel);
    if (choice != QMessageBox::Reset) return;
    _campaignReset->setEnabled(false);
    _campaignReset->setText(QStringLiteral("Resetando…"));
    _api.post(QStringLiteral("/api/sent/reset"), {},
              [this](bool ok, const QJsonDocument&, const QString& error) {
      _campaignReset->setText(QStringLiteral("Resetar lista"));
      _campaignReset->setEnabled(!_campaignActive);
      if (!ok)
        return showError(QStringLiteral("Lista não resetada"), error);
      setStatus(QStringLiteral(
          "Lista de vídeos usados resetada. O histórico foi preservado."));
      loadTasks();
    });
  });
  sourceLayout->addWidget(_campaignReset);
  auto* modeLayout = new QHBoxLayout;
  modeLayout->addWidget(new QLabel(QStringLiteral("Uso da mídia")));
  modeLayout->addWidget(_contentMode);
  modeLayout->addStretch();
  sourceColumn->addLayout(modeLayout);
  layout->addWidget(card(QStringLiteral("Conteúdo da campanha"), sourceBody));

  auto* selection = new QWidget;
  auto* selectionLayout = new QHBoxLayout(selection);
  selectionLayout->setContentsMargins(0, 0, 0, 0);
  auto* accountCol = new QVBoxLayout;
  auto* accountHeading = quietLabel(QStringLiteral("CONTAS DE DESTINO"));
  accountHeading->setWordWrap(false);
  accountHeading->setMinimumHeight(36);
  accountCol->addWidget(accountHeading);
  auto* accountControls = new QHBoxLayout;
  _campaignAccountMode = new ComboBox;
  _campaignAccountMode->addItem(QStringLiteral("Escolher manualmente"), QStringLiteral("manual"));
  _campaignAccountMode->addItem(QStringLiteral("Sortear contas"), QStringLiteral("random"));
  _campaignAccountMode->addItem(QStringLiteral("Rodízio: menos usadas"), QStringLiteral("rotation"));
  _campaignAccountMode->addItem(QStringLiteral("Maior saldo aprovado"), QStringLiteral("balance_available_desc"));
  _campaignAccountMode->addItem(QStringLiteral("Menor saldo aprovado"), QStringLiteral("balance_available_asc"));
  _campaignAccountMode->addItem(QStringLiteral("Maior saldo pendente"), QStringLiteral("balance_pending_desc"));
  _campaignAccountMode->addItem(QStringLiteral("Menor saldo pendente"), QStringLiteral("balance_pending_asc"));
  _campaignAccountMode->setToolTip(QStringLiteral(
      "Escolha manual, sorteio, rodízio ou ordenação pelo último saldo Crowtado confirmado."));
  accountControls->addWidget(_campaignAccountMode, 1);
  auto* allAccounts = new QPushButton(QStringLiteral("Todas"));
  allAccounts->setToolTip(QStringLiteral("Seleciona todas as contas cadastradas."));
  accountControls->addWidget(allAccounts);
  auto* clearAccounts = new QPushButton(QStringLiteral("Limpar"));
  clearAccounts->setToolTip(QStringLiteral("Desmarca todas as contas para escolher uma a uma."));
  accountControls->addWidget(clearAccounts);
  _campaignAccountCount = new QSpinBox;
  _campaignAccountCount->setRange(1, 1);
  _campaignAccountCount->setPrefix(QStringLiteral("Quantidade: "));
  _campaignAccountCount->setToolTip(QStringLiteral("Número de contas a incluir nesta campanha."));
  _campaignAccountCount->hide();
  accountControls->addWidget(_campaignAccountCount);
  _campaignDrawAccounts = new QPushButton(QStringLiteral("Sortear"));
  _campaignDrawAccounts->setToolTip(QStringLiteral("Faz um novo sorteio das contas disponíveis."));
  _campaignDrawAccounts->hide();
  accountControls->addWidget(_campaignDrawAccounts);
  accountCol->addLayout(accountControls);
  _campaignAccountSearch = new QLineEdit;
  _campaignAccountSearch->setPlaceholderText(QStringLiteral("Buscar conta por e-mail"));
  _campaignAccountSearch->setClearButtonEnabled(true);
  accountCol->addWidget(_campaignAccountSearch);
  _campaignAccounts = new QListWidget;
  _campaignAccounts->setMinimumHeight(190);
  _campaignAccounts->setSpacing(2);
  _campaignAccounts->setUniformItemSizes(true);
  connect(_campaignAccounts, &QListWidget::itemChanged, this, [this] {
    updateCampaignAccountCount();
    _taskReload.start();
  });
  accountCol->addWidget(_campaignAccounts);
  connect(_campaignAccountSearch, &QLineEdit::textChanged, this, [this](const QString& search) {
    for (int i = 0; i < _campaignAccounts->count(); ++i)
      _campaignAccounts->item(i)->setHidden(
          !_campaignAccounts->item(i)->data(Qt::UserRole).toString().contains(
              search.trimmed(), Qt::CaseInsensitive));
  });
  _campaignAccountSelection = quietLabel(QStringLiteral("0 de 0 contas selecionadas"));
  accountCol->addWidget(_campaignAccountSelection);
  _campaignBalanceHint = quietLabel(QString());
  _campaignBalanceHint->setWordWrap(true);
  _campaignBalanceHint->hide();
  accountCol->addWidget(_campaignBalanceHint);
  const auto setAllAccounts = [this](Qt::CheckState state) {
    {
      const QSignalBlocker blocker(_campaignAccounts);
      for (int i = 0; i < _campaignAccounts->count(); ++i)
        _campaignAccounts->item(i)->setCheckState(state);
    }
    updateCampaignAccountCount();
    _taskReload.start();
    _campaignDraftSave.start();
  };
  connect(allAccounts, &QPushButton::clicked, this,
          [setAllAccounts] { setAllAccounts(Qt::Checked); });
  connect(clearAccounts, &QPushButton::clicked, this,
          [setAllAccounts] { setAllAccounts(Qt::Unchecked); });
  connect(_campaignAccountMode, &QComboBox::currentIndexChanged, this, [this, allAccounts, clearAccounts] {
    const QString mode = _campaignAccountMode->currentData().toString();
    const bool automatic = mode != QStringLiteral("manual");
    allAccounts->setVisible(!automatic);
    clearAccounts->setVisible(!automatic);
    _campaignAccountCount->setVisible(automatic);
    _campaignDrawAccounts->setVisible(automatic);
    _campaignDrawAccounts->setText(mode.startsWith(QStringLiteral("balance_"))
        ? QStringLiteral("Recalcular lista") : mode == QStringLiteral("rotation")
        ? QStringLiteral("Atualizar rodízio") : QStringLiteral("Sortear"));
    _campaignAccounts->setEnabled(!automatic);
    _campaignBalanceHint->setVisible(mode.startsWith(QStringLiteral("balance_")));
    _campaignBalancesLoaded = false;
    ++_campaignBalanceRequestId;
    if (automatic) drawCampaignAccounts();
    else { updateCampaignAccountCount(); _taskReload.start(); }
  });
  connect(_campaignAccountCount, &QSpinBox::valueChanged, this,
          [this] { drawCampaignAccounts(); });
  connect(_campaignDrawAccounts, &QPushButton::clicked, this, [this] {
    if (_campaignAccountMode->currentData().toString().startsWith(QStringLiteral("balance_")))
      _campaignBalancesLoaded = false;
    drawCampaignAccounts();
  });
  auto* taskCol = new QVBoxLayout;
  auto* taskHead = new QHBoxLayout;
  auto* taskHeading = quietLabel(QStringLiteral("CATEGORIAS DO MINUTE"));
  taskHeading->setWordWrap(false);
  taskHeading->setMinimumHeight(36);
  taskHead->addWidget(taskHeading);
  taskHead->addStretch();
  auto* allTasks = new QPushButton(QStringLiteral("Marcar todas"));
  allTasks->setFlat(true);
  allTasks->setFixedHeight(36);
  connect(allTasks, &QPushButton::clicked, this, [this] {
    for (int i = 0; i < _campaignTasks->count(); ++i) {
      auto* item = _campaignTasks->item(i);
      if (item->flags() & Qt::ItemIsEnabled) item->setCheckState(Qt::Checked);
    }
  });
  taskHead->addWidget(allTasks);
  taskCol->addLayout(taskHead);
  _taskCatalogStatus = quietLabel(QStringLiteral("Aguardando a seleção de uma conta."));
  _taskCatalogStatus->setObjectName(QStringLiteral("campaignCatalogStatus"));
  _taskCatalogStatus->setWordWrap(true);
  taskCol->addWidget(_taskCatalogStatus);
  auto* openCatalogs = new QPushButton(QStringLiteral("Preparar catálogos…"));
  openCatalogs->setObjectName(QStringLiteral("openProviderCatalogs"));
  connect(openCatalogs, &QPushButton::clicked, this, [this] { _navigation->setCurrentRow(9); });
  taskCol->addWidget(openCatalogs);
  _taskCatalogProgress = new QProgressBar;
  _taskCatalogProgress->setObjectName(QStringLiteral("campaignCatalogProgress"));
  _taskCatalogProgress->setRange(0, 100);
  _taskCatalogProgress->setValue(0);
  _taskCatalogProgress->setFormat(QStringLiteral("%p%"));
  _taskCatalogProgress->setTextVisible(false);
  taskCol->addWidget(_taskCatalogProgress);
  _campaignTasks = new QListWidget;
  _campaignTasks->setMinimumHeight(190);
  _campaignTasks->setSpacing(2);
  _campaignTasks->setUniformItemSizes(true);
  connect(_campaignTasks, &QListWidget::itemChanged, this, [this] {
    _campaignTaskSelectionTouched = true;
    _campaignSelectedTaskIds.clear();
    for (int i = 0; i < _campaignTasks->count(); ++i) {
      auto* item = _campaignTasks->item(i);
      if (item->checkState() == Qt::Checked && !item->data(Qt::UserRole).toString().isEmpty())
        _campaignSelectedTaskIds.insert(item->data(Qt::UserRole).toString());
    }
  });
  taskCol->addWidget(_campaignTasks);
  selectionLayout->addLayout(accountCol, 1);
  selectionLayout->addLayout(taskCol, 1);
  auto* selectionBody = new QWidget;
  auto* selectionBodyLayout = new QVBoxLayout(selectionBody);
  selectionBodyLayout->setContentsMargins(0, 0, 0, 0);
  selectionBodyLayout->addWidget(selection);
  auto* draftHelp = quietLabel(QStringLiteral(
      "Rascunho salvo automaticamente neste computador. A campanha só começa após a prévia e sua confirmação."));
  draftHelp->setWordWrap(true);
  selectionBodyLayout->addWidget(draftHelp);
  layout->addWidget(card(QStringLiteral("Seleção"), selectionBody));

  auto* parameters = new QWidget;
  auto* form = new QFormLayout(parameters);
  configureForm(form);
  form->setContentsMargins(0, 0, 0, 0);
  _targetHours = new QDoubleSpinBox;
  _targetHours->setRange(0.5, 12.0);
  _targetHours->setSingleStep(0.5);
  _targetHours->setValue(8.0);
  _targetHours->setSuffix(QStringLiteral(" h / conta"));
  form->addRow(QStringLiteral("Meta de gravação"), _targetHours);
  _runUntilExhausted = new QCheckBox(QStringLiteral("Continuar até esgotar o conteúdo disponível"));
  _runUntilExhausted->setToolTip(QStringLiteral(
      "Executa até acabar o conteúdo elegível ou você solicitar uma parada segura."));
  form->addRow(QStringLiteral("Duração da campanha"), _runUntilExhausted);
  connect(_runUntilExhausted, &QCheckBox::toggled, _targetHours, &QWidget::setDisabled);
  _minDuration = new QSpinBox;
  _minDuration->setRange(1, 30);
  _minDuration->setValue(1);
  _minDuration->setSuffix(QStringLiteral(" min"));
  _minDuration->setToolTip(
      QStringLiteral("O QMoney selecionará somente vídeos com pelo menos esta duração."));
  _maxDuration = new QSpinBox;
  _maxDuration->setRange(1, 30);
  _maxDuration->setValue(30);
  _maxDuration->setSuffix(QStringLiteral(" min"));
  _maxDuration->setToolTip(
      QStringLiteral("O QMoney selecionará somente vídeos até esta duração."));
  connect(_minDuration, &QSpinBox::valueChanged, this, [this](int minimum) {
    const QSignalBlocker blocker(_maxDuration);
    _maxDuration->setMinimum(minimum);
    _taskReload.start();
  });
  connect(_maxDuration, &QSpinBox::valueChanged, this, [this](int maximum) {
    const QSignalBlocker blocker(_minDuration);
    _minDuration->setMaximum(maximum);
    _taskReload.start();
  });
  auto* duration = new QWidget;
  auto* durationLayout = new QHBoxLayout(duration);
  durationLayout->setContentsMargins(0, 0, 0, 0);
  durationLayout->addWidget(_minDuration);
  durationLayout->addWidget(new QLabel(QStringLiteral("até")));
  durationLayout->addWidget(_maxDuration);
  durationLayout->addStretch();
  _minDuration->setAccessibleName(QStringLiteral("Duração mínima"));
  _maxDuration->setAccessibleName(QStringLiteral("Duração máxima"));
  _targetHours->setMaximumWidth(200);
  _minDuration->setFixedWidth(130);
  _maxDuration->setFixedWidth(130);
  form->addRow(QStringLiteral("Duração dos vídeos"), duration);
  _delayMode = new ComboBox;
  configureCombo(_delayMode, 240);
  _delayMode->addItem(QStringLiteral("Sem intervalo"), QStringLiteral("off"));
  _delayMode->addItem(QStringLiteral("Duração do clipe"), QStringLiteral("clip"));
  _delayMode->addItem(QStringLiteral("Intervalo fixo"), QStringLiteral("fixed"));
  _delayMode->setCurrentIndex(0);
  form->addRow(QStringLiteral("Intervalo"), _delayMode);
  _delaySeconds = new QSpinBox;
  _delaySeconds->setRange(0, 3600);
  _delaySeconds->setSuffix(QStringLiteral(" s"));
  form->addRow(QStringLiteral("Intervalo fixo"), _delaySeconds);
  _delaySeconds->setMaximumWidth(200);
  form->setRowVisible(_delaySeconds, false);
  connect(_delayMode, &QComboBox::currentIndexChanged, parameters, [this, form] {
    form->setRowVisible(_delaySeconds, _delayMode->currentData().toString() == QStringLiteral("fixed"));
  });
  _cleanupAfter = new QCheckBox(QStringLiteral("Liberar mídia local após cada envio"));
  _cleanupAfter->setChecked(true);
  form->addRow(QString(), _cleanupAfter);
  auto* hours = new QWidget;
  auto* hoursLayout = new QHBoxLayout(hours);
  hoursLayout->setContentsMargins(0, 0, 0, 0);
  _activeHours = new QCheckBox(QStringLiteral("Enviar somente entre"));
  _activeHours->setChecked(true);
  _hourStart = new QSpinBox;
  _hourStart->setRange(0, 23);
  _hourStart->setValue(7);
  _hourStart->setSuffix(QStringLiteral("h"));
  _hourEnd = new QSpinBox;
  _hourEnd->setRange(1, 24);
  _hourEnd->setValue(18);
  _hourEnd->setSuffix(QStringLiteral("h"));
  _hourStart->setFixedWidth(100);
  _hourEnd->setFixedWidth(100);
  connect(_activeHours, &QCheckBox::toggled, _hourStart, &QWidget::setEnabled);
  connect(_activeHours, &QCheckBox::toggled, _hourEnd, &QWidget::setEnabled);
  hoursLayout->addWidget(_activeHours);
  hoursLayout->addWidget(_hourStart);
  hoursLayout->addWidget(new QLabel(QStringLiteral("e")));
  hoursLayout->addWidget(_hourEnd);
  hoursLayout->addStretch();
  form->addRow(QStringLiteral("Janela ativa"), hours);
  layout->addWidget(card(QStringLiteral("Parâmetros"), parameters));

  auto* execution = new QWidget;
  auto* executionLayout = new QVBoxLayout(execution);
  executionLayout->setContentsMargins(0, 0, 0, 0);
  executionLayout->setSpacing(10);
  auto* executionHead = new QHBoxLayout;
  auto* executionCopy = new QVBoxLayout;
  executionCopy->setSpacing(3);
  _campaignStage = new QLabel(QStringLiteral("Aguardando"));
  _campaignStage->setObjectName(QStringLiteral("campaignStage"));
  executionCopy->addWidget(_campaignStage);
  _campaignCurrent = quietLabel(QStringLiteral(
      "Configure a campanha; os acontecimentos importantes aparecerão aqui."));
  _campaignCurrent->setWordWrap(true);
  executionCopy->addWidget(_campaignCurrent);
  executionHead->addLayout(executionCopy, 1);
  _campaignStats = quietLabel(QStringLiteral("0 concluídos · 0 falhas"));
  _campaignStats->setObjectName(QStringLiteral("campaignStats"));
  _campaignStats->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
  executionHead->addWidget(_campaignStats);
  executionLayout->addLayout(executionHead);
  _campaignProgress = new QProgressBar;
  _campaignProgress->setObjectName(QStringLiteral("campaignProgress"));
  _campaignProgress->setRange(0, 100);
  _campaignProgress->setValue(0);
  _campaignProgress->setFormat(QStringLiteral("Nenhum envio iniciado"));
  executionLayout->addWidget(_campaignProgress);
  _campaignFeed = new QPlainTextEdit;
  _campaignFeed->setObjectName(QStringLiteral("campaignTimeline"));
  _campaignFeed->setReadOnly(true);
  _campaignFeed->setMaximumBlockCount(500);
  _campaignFeed->setMinimumHeight(190);
  _campaignFeed->setPlaceholderText(QStringLiteral(
      "A linha do tempo mostrará preparação, envios, tentativas e resultados — sem logs técnicos."));
  executionLayout->addWidget(_campaignFeed);
  auto* actions = new QHBoxLayout;
  actions->addStretch();
  _campaignStop = new QPushButton(QStringLiteral("Parar com segurança"));
  _campaignStop->setEnabled(false);
  connect(_campaignStop, &QPushButton::clicked, this, [this] {
    _api.post(QStringLiteral("/api/campaigns/stop"), {}, [this](bool ok, const QJsonDocument&, const QString& error) {
      if (!ok) showError(QStringLiteral("Não foi possível parar"), error);
      else setStatus(QStringLiteral("Parada solicitada; o envio atual será concluído."));
    });
  });
  actions->addWidget(_campaignStop);
  _campaignPause = new QPushButton(QStringLiteral("Pausar"));
  _campaignPause->setEnabled(false);
  connect(_campaignPause, &QPushButton::clicked, this, [this] {
    _campaignPause->setEnabled(false);
    _api.post(_campaignPaused ? QStringLiteral("/api/campaigns/resume")
                             : QStringLiteral("/api/campaigns/pause"), {},
        [this](bool ok, const QJsonDocument&, const QString& error) {
          if (!ok) showError(QStringLiteral("Pausa da campanha"), error);
          pollCampaign();
        });
  });
  actions->addWidget(_campaignPause);
  auto* recovery = new QPushButton(QStringLiteral("Recuperar envios"));
  connect(recovery, &QPushButton::clicked, this, &MainWindow::showRecovery);
  actions->addWidget(recovery);

  _campaignStart = primaryButton(QStringLiteral("Iniciar campanha"));
  connect(_campaignStart, &QPushButton::clicked, this, &MainWindow::startCampaign);
  actions->addWidget(_campaignStart);
  layout->addWidget(card(QStringLiteral("Execução"), execution));
  layout->addStretch();
  scroll->setWidget(content);
  bodyLayout->addWidget(scroll);
  _campaignVerificationPanel = new QWidget;
  auto* verificationLayout = new QVBoxLayout(_campaignVerificationPanel);
  verificationLayout->setContentsMargins(12, 6, 12, 6);
  _campaignVerificationStatus = new QLabel;
  _campaignVerificationStatus->setObjectName(QStringLiteral("campaignVerificationStatus"));
  _campaignVerificationStatus->setWordWrap(true);
  _campaignVerificationProgress = new QProgressBar;
  _campaignVerificationProgress->setObjectName(QStringLiteral("campaignVerificationProgress"));
  _campaignVerificationProgress->setTextVisible(false);
  verificationLayout->addWidget(_campaignVerificationStatus);
  verificationLayout->addWidget(_campaignVerificationProgress);
  _campaignVerificationPanel->hide();
  bodyLayout->addWidget(_campaignVerificationPanel);
  bodyLayout->addLayout(actions);

  _campaignDraftSave.setSingleShot(true);
  _campaignDraftSave.setInterval(400);
  connect(&_campaignDraftSave, &QTimer::timeout, this, &MainWindow::saveCampaignDraft);
  const auto draft = QJsonDocument::fromJson(QSettings().value(
      QStringLiteral("campaign/draft")).toByteArray()).object();
  if (!draft.isEmpty()) {
    _campaignDraftLoaded = true;
    for (const auto value : draft.value(QStringLiteral("accounts")).toArray())
      _campaignDraftAccounts.insert(value.toString());
    _campaignTaskSelectionTouched = draft.value(QStringLiteral("tasks_touched")).toBool();
    for (const auto value : draft.value(QStringLiteral("tasks")).toArray())
      _campaignSelectedTaskIds.insert(value.toString());
    const auto restoreCombo = [](QComboBox* combo, const QString& value) {
      const int index = combo->findData(value);
      if (index >= 0) { const QSignalBlocker blocker(combo); combo->setCurrentIndex(index); }
    };
    restoreCombo(_dataset, draft.value(QStringLiteral("dataset")).toString());
    restoreCombo(_contentMode, draft.value(QStringLiteral("content_mode")).toString());
    restoreCombo(_campaignAccountMode, draft.value(QStringLiteral("mode")).toString());
    restoreCombo(_delayMode, draft.value(QStringLiteral("delay_mode")).toString());
    _campaignDraftQuantity = qMax(1, draft.value(QStringLiteral("quantity")).toInt(1));
    _targetHours->setValue(draft.value(QStringLiteral("target_hours")).toDouble(8.0));
    _runUntilExhausted->setChecked(draft.value(QStringLiteral("run_until_exhausted")).toBool(false));
    _minDuration->setValue(draft.value(QStringLiteral("min_duration")).toInt(1));
    _maxDuration->setValue(draft.value(QStringLiteral("max_duration")).toInt(30));
    _delaySeconds->setValue(draft.value(QStringLiteral("delay_seconds")).toInt());
    _cleanupAfter->setChecked(draft.value(QStringLiteral("cleanup")).toBool(true));
    _activeHours->setChecked(draft.value(QStringLiteral("active_hours")).toBool(true));
    _hourStart->setValue(draft.value(QStringLiteral("hour_start")).toInt(7));
    _hourEnd->setValue(draft.value(QStringLiteral("hour_end")).toInt(18));
  }
  const QString restoredMode = _campaignAccountMode->currentData().toString();
  const bool automatic = restoredMode != QStringLiteral("manual");
  allAccounts->setVisible(!automatic);
  clearAccounts->setVisible(!automatic);
  _campaignAccountCount->setVisible(automatic);
  _campaignDrawAccounts->setVisible(automatic);
  _campaignDrawAccounts->setText(restoredMode == QStringLiteral("rotation")
      ? QStringLiteral("Atualizar rodízio") : restoredMode.startsWith(QStringLiteral("balance_"))
      ? QStringLiteral("Recalcular lista") : QStringLiteral("Sortear"));
  _campaignAccounts->setEnabled(!automatic);
  _campaignBalanceHint->setVisible(restoredMode.startsWith(QStringLiteral("balance_")));
  const auto scheduleDraft = [this] { _campaignDraftSave.start(); };
  connect(_campaignAccounts, &QListWidget::itemChanged, this, scheduleDraft);
  connect(_campaignTasks, &QListWidget::itemChanged, this, scheduleDraft);
  connect(_campaignAccountMode, &QComboBox::currentIndexChanged, this, scheduleDraft);
  connect(_campaignAccountCount, &QSpinBox::valueChanged, this, scheduleDraft);
  connect(_dataset, &QComboBox::currentIndexChanged, this, scheduleDraft);
  connect(_contentMode, &QComboBox::currentIndexChanged, this, scheduleDraft);
  connect(_targetHours, &QDoubleSpinBox::valueChanged, this, scheduleDraft);
  connect(_runUntilExhausted, &QCheckBox::toggled, this, scheduleDraft);
  for (auto* spin : {_minDuration, _maxDuration, _delaySeconds, _hourStart, _hourEnd})
    connect(spin, &QSpinBox::valueChanged, this, scheduleDraft);
  connect(_delayMode, &QComboBox::currentIndexChanged, this, scheduleDraft);
  connect(_cleanupAfter, &QCheckBox::toggled, this, scheduleDraft);
  connect(_activeHours, &QCheckBox::toggled, this, scheduleDraft);

  return pageShell(QStringLiteral("Nova campanha"),
                   QStringLiteral("Escolha o conteúdo, calibre a operação e acompanhe cada envio."), body);
}

QWidget* MainWindow::buildAcceleratorPage() {
  auto* body = new QWidget;
  auto* layout = new QVBoxLayout(body);
  layout->setContentsMargins(0, 0, 0, 0);
  layout->setSpacing(14);

  auto* hero = new QWidget;
  auto* heroLayout = new QVBoxLayout(hero);
  heroLayout->setContentsMargins(0, 0, 0, 0);
  auto* line = new QHBoxLayout;
  _cacheState = new QLabel(QStringLiteral("Aguardando leitura"));
  _cacheState->setObjectName(QStringLiteral("pulseTitle"));
  line->addWidget(_cacheState);
  line->addStretch();
  _cacheNumbers = quietLabel(QStringLiteral("—"));
  line->addWidget(_cacheNumbers);
  heroLayout->addLayout(line);
  _cacheProgress = new QProgressBar;
  _cacheProgress->setRange(0, 100);
  heroLayout->addWidget(_cacheProgress);
  _cacheLastRun = quietLabel(QStringLiteral("Nenhuma preparação registrada neste computador."));
  _cacheLastRun->setWordWrap(true);
  heroLayout->addWidget(_cacheLastRun);
  layout->addWidget(card(QStringLiteral("Reservatório de campanha"), hero));

  auto* config = new QWidget;
  auto* form = new QFormLayout(config);
  configureForm(form);
  form->setContentsMargins(0, 0, 0, 0);
  _cacheProvider = new ComboBox;
  configureCombo(_cacheProvider, 620);
  _cacheProvider->addItem(QStringLiteral("Ego4D"), QStringLiteral("ego4d"));
  _cacheProvider->addItem(QStringLiteral("HoloAssist"), QStringLiteral("holoassist"));
  connect(_cacheProvider, qOverload<int>(&QComboBox::currentIndexChanged), this, [this, form] {
    _cacheTask->blockSignals(true);
    _cacheTask->clear();
    _cacheTask->blockSignals(false);
    const bool ego = _cacheProvider->currentData().toString() == QStringLiteral("ego4d");
    if (_cacheTaskLabel) {
      _cacheTaskLabel->setText(ego ? QStringLiteral("Priorizar categoria")
                                   : QStringLiteral("Tarefa a preparar"));
    }
    if (_cacheBudget) form->setRowVisible(_cacheBudget, ego);
    if (_cacheBudgetHelp) form->setRowVisible(_cacheBudgetHelp, ego);
    if (_cacheProviderHelp) _cacheProviderHelp->setText(ego
        ? QStringLiteral("Ego4D: prepara vídeos e sensores antecipadamente. A campanha usa primeiro os arquivos prontos.")
        : QStringLiteral("HoloAssist: prepara os clipes da tarefa escolhida para uso posterior na campanha."));
    if (_cacheTaskHelp) _cacheTaskHelp->setText(ego
        ? QStringLiteral("O cache alterna clipes entre categorias. A escolhida entra primeiro em cada rodada; o limite em GB é compartilhado.")
        : QStringLiteral("Somente a tarefa escolhida entra nesta preparação."));
    if (_cacheDiskHelp) _cacheDiskHelp->setText(ego
        ? QStringLiteral("O QMoney preserva o espaço livre indicado e ajusta o limite ao disco disponível.")
        : QStringLiteral("A preparação para quando o espaço livre cair abaixo da reserva."));
    if (_cacheLimitHelp) _cacheLimitHelp->setText(ego
        ? QStringLiteral("Todos usa o espaço escolhido; um número menor limita somente esta execução.")
        : QStringLiteral("Todos prepara todos os clipes disponíveis da tarefa escolhida."));
    if (_cacheStart) {
      const bool off = ego && _cacheBudget && _cacheBudget->value() == 0;
      _cacheStart->setText(off ? QStringLiteral("Desativar pré-cache")
                               : QStringLiteral("Preparar cache"));
    }
    loadAccelerator();
  });
  form->addRow(QStringLiteral("Provedor"), _cacheProvider);
  _cacheProviderHelp = quietLabel(QStringLiteral(
      "HoloAssist: prepara os clipes da tarefa escolhida para uso posterior na campanha."));
  _cacheProviderHelp->setWordWrap(true);
  form->addRow(QString(), _cacheProviderHelp);
  _cacheTask = new ComboBox;
  configureCombo(_cacheTask, 620);
  connect(_cacheTask, qOverload<int>(&QComboBox::currentIndexChanged), this, [this] {
    loadAccelerator();
  });
  _cacheTaskLabel = new QLabel(QStringLiteral("Tarefa a preparar"));
  form->addRow(_cacheTaskLabel, _cacheTask);
  _cacheTaskHelp = quietLabel(QStringLiteral("Somente a tarefa escolhida entra nesta preparação."));
  _cacheTaskHelp->setWordWrap(true);
  form->addRow(QString(), _cacheTaskHelp);
  _cacheBudgetLabel = new QLabel(QStringLiteral("Espaço para o cache"));
  _cacheBudget = new QSpinBox;
  _cacheBudget->setRange(0, 2147483647);
  _cacheBudget->setKeyboardTracking(false);
  _cacheBudget->setSpecialValueText(QStringLiteral("0 GB · desativado"));
  _cacheBudget->setValue(0);
  _cacheBudget->setSuffix(QStringLiteral(" GB"));
  connect(_cacheBudget, qOverload<int>(&QSpinBox::valueChanged), this, [this] {
    const bool off = _cacheBudget->value() == 0;
    _cacheBudget->setSuffix(off ? QString() : QStringLiteral(" GB"));
    if (_cacheStart && _cacheProvider
        && _cacheProvider->currentData().toString() == QStringLiteral("ego4d")) {
      _cacheStart->setText(off ? QStringLiteral("Desativar pré-cache")
                               : QStringLiteral("Preparar cache"));
    }
    loadAccelerator();
  });
  form->addRow(_cacheBudgetLabel, _cacheBudget);
  _cacheBudgetHelp = quietLabel(QStringLiteral(
      "Limite total para arquivos Ego4D neste computador. 0 GB desativa a preparação antecipada; a campanha ainda pode buscar vídeos quando precisar."));
  _cacheBudgetHelp->setWordWrap(true);
  form->addRow(QString(), _cacheBudgetHelp);
  form->setRowVisible(_cacheBudget, false);
  form->setRowVisible(_cacheBudgetHelp, false);
  _cacheLimit = new QSpinBox;
  _cacheLimit->setRange(0, 1000);
  _cacheLimit->setSpecialValueText(QStringLiteral("Todos"));
  _cacheLimit->setToolTip(QStringLiteral("0 prepara todos os clipes que couberem no espaço escolhido. Outro valor limita apenas esta execução."));
  connect(_cacheLimit, qOverload<int>(&QSpinBox::valueChanged), this, [this] {
    loadAccelerator();
  });
  form->addRow(QStringLiteral("Clipes nesta execução"), _cacheLimit);
  _cacheLimitHelp = quietLabel(QStringLiteral(
      "Todos prepara todos os clipes disponíveis da tarefa escolhida."));
  _cacheLimitHelp->setWordWrap(true);
  form->addRow(QString(), _cacheLimitHelp);
  _cacheReserve = new QSpinBox;
  _cacheReserve->setRange(5, 1000);
  _cacheReserve->setValue(50);
  _cacheReserve->setSuffix(QStringLiteral(" GiB livres"));
  _cacheReserve->setKeyboardTracking(false);
  connect(_cacheReserve, qOverload<int>(&QSpinBox::valueChanged), this, [this] {
    loadAccelerator();
  });
  form->addRow(QStringLiteral("Manter livre no disco"), _cacheReserve);
  _cacheDiskHelp = quietLabel(QStringLiteral("A preparação para quando o espaço livre cair abaixo da reserva."));
  _cacheDiskHelp->setWordWrap(true);
  form->addRow(QString(), _cacheDiskHelp);
  auto* actions = new QWidget;
  auto* actionLayout = new QHBoxLayout(actions);
  actionLayout->setContentsMargins(0, 0, 0, 0);
  auto* cleanupProvider = new ComboBox;
  cleanupProvider->addItem(QStringLiteral("Ego4D"), QStringLiteral("ego4d"));
  cleanupProvider->addItem(QStringLiteral("HoloAssist"), QStringLiteral("holoassist"));
  cleanupProvider->addItem(QStringLiteral("Ambos"), QStringLiteral("all"));
  cleanupProvider->setToolTip(QStringLiteral("Escolha o cache a apagar"));
  actionLayout->addWidget(cleanupProvider);
  auto* cleanup = new QPushButton(QStringLiteral("Apagar cache"));
  connect(cleanup, &QPushButton::clicked, this, [this, cleanupProvider] {
    const QString provider = cleanupProvider->currentData().toString();
    const QString name = cleanupProvider->currentText();
    if (QMessageBox::question(this, QStringLiteral("Limpar mídia"),
          QStringLiteral("Apagar o cache de %1? Catálogos, contas e histórico serão preservados.").arg(name))
        != QMessageBox::Yes) return;
    _api.post(QStringLiteral("/api/storage/cleanup"), {{QStringLiteral("provider"), provider}}, [this](bool ok, const QJsonDocument& doc, const QString& error) {
      if (!ok) return showError(QStringLiteral("Falha na limpeza"), error);
      const auto result = doc.object();
      setStatus(QStringLiteral("%1 arquivo(s) removido(s).").arg(result.value(QStringLiteral("files")).toInt()));
      loadAccelerator();
    });
  });
  actionLayout->addWidget(cleanup);
  actionLayout->addStretch();
  _cacheStop = new QPushButton(QStringLiteral("Parar com segurança"));
  _cacheStop->setEnabled(false);
  connect(_cacheStop, &QPushButton::clicked, this, [this] {
    _api.post(QStringLiteral("/api/holo-cache/stop"), {}, [this](bool ok, const QJsonDocument&, const QString& error) {
      if (!ok) showError(QStringLiteral("Falha ao parar"), error);
      else setStatus(QStringLiteral("O acelerador parará depois do clipe atual."));
    });
  });
  actionLayout->addWidget(_cacheStop);
  _cacheStart = primaryButton(QStringLiteral("Preparar cache"));
  connect(_cacheStart, &QPushButton::clicked, this, &MainWindow::startAccelerator);
  actionLayout->addWidget(_cacheStart);
  form->addRow(QString(), actions);
  layout->addWidget(card(QStringLiteral("O que preparar"), config));
  layout->addStretch();

  _acceleratorTabs = new QTabWidget;
  _acceleratorTabs->setDocumentMode(true);
  _acceleratorTabs->addTab(body, QStringLiteral("Cache Ego4D / HoloAssist"));
  auto* nymeriaScroll = new QScrollArea;
  nymeriaScroll->setWidgetResizable(true);
  nymeriaScroll->setFrameShape(QFrame::NoFrame);
  auto* nymeriaContent = buildNymeriaLibraryTab();
  nymeriaContent->layout()->setSizeConstraint(QLayout::SetMinimumSize);
  nymeriaScroll->setWidget(nymeriaContent);
  _acceleratorTabs->addTab(nymeriaScroll, QStringLiteral("NymeriaPlus"));
  connect(_acceleratorTabs, &QTabWidget::currentChanged, this, [this](int index) {
    onLibraryTabActivated(index);
  });
  return pageShell(QStringLiteral("Acelerador"),
                   QStringLiteral("Prepare mídia antes da campanha e acompanhe quanto já está pronto no disco."), _acceleratorTabs);
}

