#include "MainWindowSupport.hpp"

QWidget* MainWindow::buildProviderCatalogPage() {
  auto* body = new QWidget;
  auto* layout = new QVBoxLayout(body);
  layout->setContentsMargins(0, 0, 0, 0);
  auto* options = new QWidget;
  auto* form = new QFormLayout(options);
  configureForm(form);
  form->setContentsMargins(0, 0, 0, 0);
  _providerCatalogDataset = new ComboBox;
  configureCombo(_providerCatalogDataset, 240);
  _providerCatalogDataset->addItem(QStringLiteral("Ego4D + NymeriaPlus"), QStringLiteral("ambos"));
  _providerCatalogDataset->addItem(QStringLiteral("Somente Ego4D"), QStringLiteral("ego4d"));
  _providerCatalogDataset->addItem(QStringLiteral("Somente NymeriaPlus"), QStringLiteral("nymeria"));
  form->addRow(QStringLiteral("Provedores"), _providerCatalogDataset);
  auto* durations = new QWidget;
  auto* row = new QHBoxLayout(durations);
  row->setContentsMargins(0, 0, 0, 0);
  _providerCatalogMinimum = new QSpinBox;
  _providerCatalogMinimum->setRange(1, 30);
  _providerCatalogMinimum->setValue(1);
  _providerCatalogMinimum->setSuffix(QStringLiteral(" min"));
  _providerCatalogMaximum = new QSpinBox;
  _providerCatalogMaximum->setRange(1, 30);
  _providerCatalogMaximum->setValue(30);
  _providerCatalogMaximum->setSuffix(QStringLiteral(" min"));
  row->addWidget(_providerCatalogMinimum);
  row->addWidget(new QLabel(QStringLiteral("até")));
  row->addWidget(_providerCatalogMaximum);
  form->addRow(QStringLiteral("Faixa dos vídeos"), durations);
  const QString preparationHelp = QStringLiteral("Baixa ou reutiliza as anotações necessárias e prepara índices reutilizáveis. Vídeos e sensores são preparados durante a campanha, que também verifica as categorias permitidas, o histórico e a capacidade sem repetições.");
  auto* note = quietLabel(QStringLiteral("Anotações e índices necessários à campanha. Acompanhamento automático abaixo."));
  note->setWordWrap(true);
  note->setToolTip(preparationHelp);
  note->setAccessibleDescription(preparationHelp);
  form->addRow(note);
  _providerCatalogPrepare = primaryButton(QStringLiteral("Preparar / atualizar catálogos"));
  _providerCatalogPrepare->setObjectName(QStringLiteral("providerCatalogPrepare"));
  _providerCatalogPrepare->setToolTip(preparationHelp);
  _providerCatalogPrepare->setAccessibleDescription(preparationHelp);
  _providerCatalogPrepare->setEnabled(_backendReady);
  form->addRow(_providerCatalogPrepare);
  connect(_providerCatalogPrepare, &QPushButton::clicked, this, &MainWindow::prepareProviderCatalog);
  layout->addWidget(card(QStringLiteral("Preparação de conteúdo"), options));
  auto* monitor = new QWidget;
  auto* monitorLayout = new QVBoxLayout(monitor);
  _providerCatalogStatus = new QLabel(QStringLiteral("Nenhuma preparação iniciada."));
  _providerCatalogStatus->setWordWrap(true);
  _providerCatalogStatus->setObjectName(QStringLiteral("providerCatalogStatus"));
  monitorLayout->addWidget(_providerCatalogStatus);
  auto* progressRow = new QHBoxLayout;
  _providerCatalogProgress = new QProgressBar;
  _providerCatalogProgress->setMinimumHeight(24);
  _providerCatalogProgress->setTextVisible(false);
  _providerCatalogProgress->setObjectName(QStringLiteral("providerCatalogProgress"));
  _providerCatalogProgress->setRange(0, 100);
  _providerCatalogProgress->setValue(0);
  _providerCatalogPercent = new QLabel(QStringLiteral("0%"));
  _providerCatalogPercent->setObjectName(QStringLiteral("providerCatalogPercent"));
  _providerCatalogPercent->setMinimumWidth(70);
  QFont font = _providerCatalogPercent->font(); font.setPointSize(20); font.setBold(true);
  _providerCatalogPercent->setFont(font);
  progressRow->addWidget(_providerCatalogProgress, 1);
  progressRow->addWidget(_providerCatalogPercent);
  monitorLayout->addLayout(progressRow);
  _providerCatalogSummary = new QPlainTextEdit;
  _providerCatalogSummary->setReadOnly(true);
  _providerCatalogSummary->setObjectName(QStringLiteral("providerCatalogSummary"));
  _providerCatalogSummary->setMinimumHeight(160);
  _providerCatalogSummary->setToolTip(QStringLiteral("A preparação confere se os índices correspondem às fontes atuais."));
  monitorLayout->addWidget(_providerCatalogSummary, 1);
  layout->addWidget(card(QStringLiteral("Acompanhamento"), monitor), 1);
  auto* campaign = new QPushButton(QStringLiteral("Abrir campanha"));
  connect(campaign, &QPushButton::clicked, this, [this] { _navigation->setCurrentRow(3); });
  layout->addWidget(campaign);
  _providerCatalogWatch.setInterval(1000);
  connect(&_providerCatalogWatch, &QTimer::timeout, this, [this] {
    updateProviderCatalogMonitor();
    if (_providerCatalogBusy || isPageActive(9)) loadProviderCatalogStatus();
  });
  const auto selectionChanged = [this] {
    ++_providerCatalogRevision;
    _providerCatalogRequestPending = false;
    _providerCatalogJobId.clear();
    _providerCatalogSnapshot = {};
    _providerCatalogSummary->clear();
    _providerCatalogProgress->setRange(0, 100);
    _providerCatalogProgress->setValue(0);
    _providerCatalogPercent->setText(QStringLiteral("—"));
    _providerCatalogStatus->setText(QStringLiteral("Consultando o catálogo selecionado…"));
    loadProviderCatalogStatus();
  };
  connect(_providerCatalogDataset, qOverload<int>(&QComboBox::currentIndexChanged), this, selectionChanged);
  connect(_providerCatalogMinimum, qOverload<int>(&QSpinBox::valueChanged), this, selectionChanged);
  connect(_providerCatalogMaximum, qOverload<int>(&QSpinBox::valueChanged), this, selectionChanged);
  return pageShell(QStringLiteral("Catálogos"),
      QStringLiteral("Índices de conteúdo para as campanhas."), body);
}

void MainWindow::onProviderCatalogBackendAvailabilityChanged(bool ready, bool generationChanged) {
  if (!_providerCatalogPrepare) return;
  if (generationChanged) {
    ++_providerCatalogRevision;
    _providerCatalogRequestPending = false;
    _providerCatalogJobId.clear();
  }
  if (!ready) {
    _providerCatalogWatch.stop();
    if (_providerCatalogBusy)
      _providerCatalogStatus->setText(QStringLiteral("O serviço foi desconectado. O acompanhamento será retomado ao reconectar; a conclusão ainda não foi confirmada."));
    _providerCatalogBusy = false;
  } else if (!_closing && isPageActive(9)) {
    _providerCatalogWatch.start();
    loadProviderCatalogStatus();
  }
  _providerCatalogPrepare->setEnabled(ready && !_providerCatalogBusy);
  _providerCatalogDataset->setEnabled(!_providerCatalogBusy);
  _providerCatalogMinimum->setEnabled(!_providerCatalogBusy);
  _providerCatalogMaximum->setEnabled(!_providerCatalogBusy);
}

void MainWindow::onProviderCatalogPageActivated(int index) {
  if (index == 9 && _backendReady && !_closing) _providerCatalogWatch.start();
  else if (!_providerCatalogBusy) _providerCatalogWatch.stop();
}

void MainWindow::openProviderCatalogsForCampaign(const QString& dataset, int minimumMinutes,
                                                int maximumMinutes) {
  if (_closing || !_providerCatalogDataset) return;
  if (!_providerCatalogBusy) {
    const QString selected = dataset == QStringLiteral("all") ? QStringLiteral("ambos") : dataset;
    const int index = _providerCatalogDataset->findData(selected);
    if (index < 0) return;
    const int minimum = qBound(1, minimumMinutes, 30);
    const int maximum = qBound(minimum, maximumMinutes, 30);
    const bool changed = _providerCatalogDataset->currentIndex() != index
        || _providerCatalogMinimum->value() != minimum || _providerCatalogMaximum->value() != maximum;
    if (changed) {
      const QSignalBlocker datasetBlocker(_providerCatalogDataset);
      const QSignalBlocker minimumBlocker(_providerCatalogMinimum);
      const QSignalBlocker maximumBlocker(_providerCatalogMaximum);
      _providerCatalogDataset->setCurrentIndex(index);
      _providerCatalogMinimum->setValue(minimum);
      _providerCatalogMaximum->setValue(maximum);
      ++_providerCatalogRevision;
      _providerCatalogRequestPending = false;
      _providerCatalogJobId.clear();
      _providerCatalogSnapshot = {};
      _providerCatalogSummary->clear();
      _providerCatalogProgress->setRange(0, 100);
      _providerCatalogProgress->setValue(0);
      _providerCatalogPercent->setText(QStringLiteral("—"));
    }
    _providerCatalogStatus->setText(QStringLiteral("Seleção da campanha: %1, de %2 a %3 min.")
        .arg(_providerCatalogDataset->currentText()).arg(minimum).arg(maximum));
  }
  // An accepted preparation keeps its own selectors and job identity. Opening
  // this page from another campaign selection must never retarget that work.
  _navigation->setCurrentRow(9);
}

void MainWindow::shutdownProviderCatalogTimers() {
  ++_providerCatalogRevision;
  _providerCatalogWatch.stop();
  _providerCatalogRequestPending = false;
}

QString MainWindow::providerCatalogPath(bool prepare) const {
  QString path = QStringLiteral("/api/catalogs/providers%1?dataset=%2&min_dur_s=%3&max_dur_s=%4")
      .arg(prepare ? QStringLiteral("/prepare") : QString(),
           encoded(_providerCatalogDataset->currentData().toString()))
      .arg(_providerCatalogMinimum->value() * 60).arg(_providerCatalogMaximum->value() * 60);
  if (!prepare && !_providerCatalogJobId.isEmpty())
    path += QStringLiteral("&job_id=") + encoded(_providerCatalogJobId);
  return path;
}

void MainWindow::loadProviderCatalogStatus() {
  if (_providerCatalogRequestPending || !_backendReady || _closing) return;
  if (_providerCatalogMinimum->value() > _providerCatalogMaximum->value()) {
    _providerCatalogStatus->setText(QStringLiteral("A duração mínima deve ser menor ou igual à máxima."));
    return;
  }
  const int generation = _operationBackendGeneration;
  const quint64 revision = _providerCatalogRevision;
  _providerCatalogRequestPending = true;
  _api.get(providerCatalogPath(), [this, generation, revision](bool ok, const QJsonDocument& doc, const QString& error) {
    if (_closing || generation != _operationBackendGeneration || revision != _providerCatalogRevision) return;
    renderProviderCatalogReply(ok, doc, error, false);
  }, [this, generation, revision](const QJsonObject& progress) {
    if (_closing || generation != _operationBackendGeneration || revision != _providerCatalogRevision) return;
    renderProviderCatalogProgress(progress);
  });
}

void MainWindow::prepareProviderCatalog() {
  if (_providerCatalogBusy || !_backendReady || _closing) return;
  if (_providerCatalogMinimum->value() > _providerCatalogMaximum->value()) {
    _providerCatalogStatus->setText(QStringLiteral("A duração mínima deve ser menor ou igual à máxima.")); return;
  }
  ++_providerCatalogRevision;
  _providerCatalogRequestPending = true;
  _providerCatalogJobId.clear();
  _providerCatalogBusy = true;
  _providerCatalogPrepare->setEnabled(false);
  _providerCatalogDataset->setEnabled(false);
  _providerCatalogMinimum->setEnabled(false);
  _providerCatalogMaximum->setEnabled(false);
  _providerCatalogProgress->setRange(0, 0);
  _providerCatalogPercent->setText(QStringLiteral("…"));
  _providerCatalogStatus->setText(QStringLiteral("Solicitando a preparação dos índices…"));
  _providerCatalogSummary->clear();
  _providerCatalogSnapshot = QJsonObject{{"message", QStringLiteral("Solicitando anotações e índices…")},
      {"elapsed_s", 0}, {"idle_s", 0}};
  _providerCatalogObservedAt.restart();
  _providerCatalogWatch.start();
  const int generation = _operationBackendGeneration;
  const quint64 revision = _providerCatalogRevision;
  _api.post(providerCatalogPath(true), {}, [this, generation, revision](bool ok, const QJsonDocument& doc, const QString& error) {
    if (_closing || generation != _operationBackendGeneration || revision != _providerCatalogRevision) return;
    renderProviderCatalogReply(ok, doc, error, true);
  }, [this, generation, revision](const QJsonObject& progress) {
    if (_closing || generation != _operationBackendGeneration || revision != _providerCatalogRevision) return;
    renderProviderCatalogProgress(progress);
  });
}

void MainWindow::renderProviderCatalogProgress(const QJsonObject& progress) {
  recordLiveDataResult(9, true);
  _providerCatalogBusy = true;
  _providerCatalogSnapshot = progress;
  _providerCatalogObservedAt.restart();
  const QString jobId = progress.value(QStringLiteral("job_id")).toString();
  if (!jobId.isEmpty()) _providerCatalogJobId = jobId;
  _providerCatalogPrepare->setEnabled(false);
  _providerCatalogDataset->setEnabled(false);
  _providerCatalogMinimum->setEnabled(false);
  _providerCatalogMaximum->setEnabled(false);
  _providerCatalogWatch.start();
  updateProviderCatalogMonitor();
}

void MainWindow::updateProviderCatalogMonitor() {
  if (!_providerCatalogBusy || _closing || !_providerCatalogObservedAt.isValid()) return;
  const auto completed = _providerCatalogSnapshot.value(QStringLiteral("completed"));
  const auto total = _providerCatalogSnapshot.value(QStringLiteral("total"));
  QString message = _providerCatalogSnapshot.value(QStringLiteral("message")).toString(
      QStringLiteral("Aguardando resposta da preparação…"));
  if (completed.isDouble() && total.isDouble() && total.toDouble() > 0
      && completed.toDouble() >= 0 && completed.toDouble() <= total.toDouble()) {
    const int percent = int(completed.toDouble() * 100 / total.toDouble());
    _providerCatalogProgress->setRange(0, 100);
    _providerCatalogProgress->setValue(percent);
    _providerCatalogPercent->setText(QStringLiteral("%1%").arg(percent));
    message += QStringLiteral("\nEtapa atual: %1 de %2 concluídos (%3% desta etapa).")
        .arg(completed.toDouble(), 0, 'f', 0).arg(total.toDouble(), 0, 'f', 0).arg(percent);
  } else {
    _providerCatalogProgress->setRange(0, 0);
    _providerCatalogPercent->setText(QStringLiteral("…"));
  }
  const int sinceReply = int(_providerCatalogObservedAt.elapsed() / 1000);
  message += QStringLiteral("\nTempo: %1 s · Sem avanço confirmado há %2 s · Última resposta há %3 s.")
      .arg(_providerCatalogSnapshot.value(QStringLiteral("elapsed_s")).toInt() + sinceReply)
      .arg(_providerCatalogSnapshot.value(QStringLiteral("idle_s")).toInt() + sinceReply)
      .arg(sinceReply);
  if (!_providerCatalogSnapshot.value(QStringLiteral("observation_error")).toString().isEmpty())
    message += QStringLiteral("\n%1\nRetomando o acompanhamento automaticamente; a preparação não será reenviada.")
        .arg(_providerCatalogSnapshot.value(QStringLiteral("observation_error")).toString());
  _providerCatalogStatus->setText(message);
}

void MainWindow::renderProviderCatalogReply(bool ok, const QJsonDocument& doc,
                                          const QString& error, bool preparation) {
  _providerCatalogRequestPending = false;
  const auto result = doc.object();
  recordLiveDataResult(9, ok || result.contains(QStringLiteral("ready"))
      || result.value(QStringLiteral("loading")).toBool(), error);
  if (result.value(QStringLiteral("loading")).toBool()) {
    renderProviderCatalogProgress(result);
    if (!ok) _providerCatalogSnapshot.insert(QStringLiteral("observation_error"), error);
    updateProviderCatalogMonitor();
    return;
  }
  if (!ok && _providerCatalogBusy
      && result.value(QStringLiteral("error_code")).toString() == QStringLiteral("request_outcome_unknown")) {
    _providerCatalogSnapshot.insert(QStringLiteral("observation_error"), error);
    updateProviderCatalogMonitor();
    return;
  }
  const bool wasPreparing = _providerCatalogBusy || preparation;
  _providerCatalogBusy = false;
  _providerCatalogJobId.clear();
  if (!isPageActive(9)) _providerCatalogWatch.stop();
  _providerCatalogPrepare->setEnabled(_backendReady && !_closing);
  _providerCatalogDataset->setEnabled(true);
  _providerCatalogMinimum->setEnabled(true);
  _providerCatalogMaximum->setEnabled(true);
  const bool inventory = ok && !preparation && result.value(QStringLiteral("indexes_found")).isObject();
  // Passive inventory reads must not erase the last confirmed preparation or
  // its error once the service's short-lived job cache expires.
  if (!inventory && (wasPreparing || result.contains(QStringLiteral("ready")))) {
    _providerCatalogSnapshot = result;
    _providerCatalogSnapshot.insert(QStringLiteral("preparation_result"), true);
    _providerCatalogSnapshot.insert(QStringLiteral("preparation_ok"), ok);
    if (!error.isEmpty() && result.value(QStringLiteral("error")).toString().isEmpty()
        && result.value(QStringLiteral("message")).toString().isEmpty())
      _providerCatalogSnapshot.insert(QStringLiteral("error"), error);
  }
  const bool retained = inventory && _providerCatalogSnapshot.value(QStringLiteral("preparation_result")).toBool();
  const QJsonObject display = retained ? _providerCatalogSnapshot : result;
  const bool partial = display.value(QStringLiteral("state")).toString() == QStringLiteral("partial");
  const bool previouslyReady = (retained ? display.value(QStringLiteral("preparation_ok")).toBool() : ok)
      && display.value(QStringLiteral("ready")) == QJsonValue(true) && !partial;
  bool selectedIndexesPresent = true;
  if (inventory) {
    const auto indexes = result.value(QStringLiteral("indexes_found")).toObject();
    const QString dataset = _providerCatalogDataset->currentData().toString();
    for (const auto& selected : {QStringLiteral("ego4d"), QStringLiteral("nymeria")}) {
      if (dataset == QStringLiteral("ambos") || dataset == selected)
        selectedIndexesPresent &= indexes.value(selected).toBool();
    }
  }
  const bool missingPreparedIndex = retained && previouslyReady && !selectedIndexesPresent;
  const bool ready = previouslyReady && !missingPreparedIndex;
  _providerCatalogProgress->setRange(0, 100);
  _providerCatalogProgress->setValue(ready ? 100 : 0);
  _providerCatalogPercent->setText(ready ? QStringLiteral("100%") : partial ? QStringLiteral("Parcial")
      : missingPreparedIndex ? QStringLiteral("A preparar")
      : inventory && !retained ? QStringLiteral("—") : QStringLiteral("Falha"));
  QString message = display.value(QStringLiteral("message")).toString();
  if (!ready && (!inventory || retained) && message.isEmpty())
    message = display.value(QStringLiteral("error")).toString();
  if (ok && !ready && !inventory && !partial && !display.contains(QStringLiteral("ready"))
      && display.value(QStringLiteral("error")).toString().isEmpty())
    message = QStringLiteral("O serviço não confirmou a preparação dos catálogos.");
  if (message.isEmpty() && !ok) message = error;
  if (message.isEmpty()) message = ready ? QStringLiteral("Catálogos preparados.")
      : inventory ? QStringLiteral("Selecione os provedores e prepare as anotações e índices necessários.")
      : QStringLiteral("O serviço não confirmou a preparação dos catálogos.");
  if (partial) message = QStringLiteral("Preparação concluída com pendências. %1").arg(message);
  if (missingPreparedIndex) message = QStringLiteral("Um índice da seleção não está mais disponível. Prepare os catálogos novamente.\nÚltima preparação: %1")
      .arg(display.value(QStringLiteral("message")).toString());
  else if (retained) message = QStringLiteral("Última preparação: %1\n%2")
      .arg(message, result.value(QStringLiteral("message")).toString());
  _providerCatalogStatus->setText(message);
  QStringList summaries;
  if (inventory) {
    const auto indexes = result.value(QStringLiteral("indexes_found")).toObject();
    const QString dataset = _providerCatalogDataset->currentData().toString();
    for (const auto& selected : {QStringLiteral("ego4d"), QStringLiteral("nymeria")}) {
      if (dataset != QStringLiteral("ambos") && dataset != selected) continue;
      summaries << QStringLiteral("%1: %2")
          .arg(selected == QStringLiteral("ego4d") ? QStringLiteral("Índice base Ego4D") : QStringLiteral("Índice NymeriaPlus"),
               indexes.value(selected).toBool()
                   ? selected == QStringLiteral("ego4d") ? QStringLiteral("arquivo encontrado") : QStringLiteral("encontrado")
                   : QStringLiteral("a preparar"));
    }
  }
  if (retained && !display.value(QStringLiteral("providers")).toArray().isEmpty())
    summaries << QStringLiteral("\nResultado da última preparação:");
  if (inventory) summaries << QStringLiteral("Faixa selecionada: %1–%2 min. A campanha verifica as fontes e a compatibilidade com essa faixa; encontrar o arquivo não confirma essa validação.")
      .arg(_providerCatalogMinimum->value()).arg(_providerCatalogMaximum->value());
  for (const auto& value : display.value(QStringLiteral("providers")).toArray()) {
    const auto item = value.toObject();
    const QString provider = item.value(QStringLiteral("provider")).toString();
    const QString issue = item.value(QStringLiteral("error")).toString();
    QStringList metrics;
    if (item.value(QStringLiteral("candidates")).isDouble())
      metrics << QStringLiteral("%1 candidatos").arg(item.value(QStringLiteral("candidates")).toInt());
    if (item.value(QStringLiteral("rules_with_candidates")).isDouble()) {
      const int available = item.value(QStringLiteral("rules_with_candidates")).toInt();
      metrics << (item.value(QStringLiteral("rules_checked")).isDouble()
          ? QStringLiteral("%1/%2 regras com conteúdo").arg(available).arg(item.value(QStringLiteral("rules_checked")).toInt())
          : QStringLiteral("%1 regras com conteúdo").arg(available));
    }
    if (item.value(QStringLiteral("annotation_hours_upper_bound")).isDouble())
      metrics << QStringLiteral("até %1 h anotadas").arg(item.value(QStringLiteral("annotation_hours_upper_bound")).toDouble(), 0, 'f', 2);
    summaries << (metrics.isEmpty() ? provider : QStringLiteral("%1: %2").arg(provider, metrics.join(QStringLiteral(" · "))));
    if (!issue.isEmpty()) summaries << QStringLiteral("  Pendência: %1").arg(issue);
    const auto sources = item.value(QStringLiteral("sources")).toObject();
    QStringList counts;
    if (sources.value(QStringLiteral("completed")).isDouble() && sources.value(QStringLiteral("total")).isDouble())
      counts << QStringLiteral("%1/%2 fontes verificadas").arg(sources.value(QStringLiteral("completed")).toInt())
          .arg(sources.value(QStringLiteral("total")).toInt());
    if (sources.value(QStringLiteral("cataloged")).isDouble())
      counts << QStringLiteral("%1 disponíveis").arg(sources.value(QStringLiteral("cataloged")).toInt());
    if (sources.value(QStringLiteral("no_annotations")).isDouble())
      counts << QStringLiteral("%1 sem ações atômicas").arg(sources.value(QStringLiteral("no_annotations")).toInt());
    if (sources.value(QStringLiteral("invalid_annotations")).isDouble())
      counts << QStringLiteral("%1 inválidas").arg(sources.value(QStringLiteral("invalid_annotations")).toInt());
    if (sources.value(QStringLiteral("download_failed")).isDouble())
      counts << QStringLiteral("%1 falhas de aquisição").arg(sources.value(QStringLiteral("download_failed")).toInt());
    if (sources.value(QStringLiteral("errors")).isDouble()
        && !sources.contains(QStringLiteral("invalid_annotations")) && !sources.contains(QStringLiteral("download_failed")))
      counts << QStringLiteral("%1 falhas").arg(sources.value(QStringLiteral("errors")).toInt());
    if (!counts.isEmpty()) summaries << QStringLiteral("  Anotações: %1").arg(counts.join(QStringLiteral(" · ")));
    if (sources.value(QStringLiteral("no_annotations")).toInt() > 0)
      summaries << QStringLiteral("  Fontes sem ações atômicas não geram candidatos e não são falhas de aquisição.");
  }
  if (ready) summaries << QStringLiteral("\nAnotações e índices disponíveis. As estimativas ainda serão verificadas com as categorias, o histórico e a mídia de cada envio.");
  if (!summaries.isEmpty()) _providerCatalogSummary->setPlainText(summaries.join(QLatin1Char('\n')));
  if (wasPreparing && !inventory) {
    QSet<QString> prepared;
    for (const auto& value : result.value(QStringLiteral("providers")).toArray()) {
      const auto provider = value.toObject();
      const QString id = provider.value(QStringLiteral("provider")).toString();
      if ((id == QStringLiteral("ego4d") || id == QStringLiteral("nymeria"))
          && provider.value(QStringLiteral("candidates")).isDouble()
          && provider.value(QStringLiteral("state")).toString() != QStringLiteral("error"))
        prepared.insert(id);
    }
    if (ready && prepared.isEmpty()) prepared.insert(_providerCatalogDataset->currentData().toString());
    if (!prepared.isEmpty()) refreshCampaignTasksAfterProviderPreparation(prepared.size() > 1
        ? QStringLiteral("ambos") : *prepared.cbegin());
  }
}
