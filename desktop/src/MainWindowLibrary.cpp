#include "MainWindowSupport.hpp"
#include "MainWindow.hpp"


void MainWindow::onLibraryPageActivated(int index) {
  if (index != 4) _nymeriaPoll.stop();
}

void MainWindow::onLibraryTabActivated(int index) {
  if (index == 1 && _backendReady) {
    loadNymeriaLibrary();
    pollNymeriaJob();
  } else {
    _nymeriaPoll.stop();
  }
}

void MainWindow::shutdownLibraryTimers() {
  _nymeriaPoll.stop();
}

void MainWindow::onLibraryBackendAvailabilityChanged(bool, bool generationChanged) {
  if (generationChanged) {
    ++_nymeriaRequestId;
    ++_nymeriaJobRevision;
    ++_nymeriaPlanRevision;
    _nymeriaInventoryPending = false;
    _nymeriaCommandPending = false;
    _nymeriaJobPending = false;
    _nymeriaPlanPending = false;
    _nymeriaRunning = false;
    _nymeriaPlan = {};
    _nymeriaPoll.stop();
  }
  updateNymeriaLibraryActions();
}

void MainWindow::onCampaignAvailabilityChanged() {
  updateNymeriaLibraryActions();
}

void MainWindow::updateNymeriaLibraryActions() {
  if (!_nymeriaImport) return;
  const bool ready = _backendReady && !_closing && !_shutdownPolling;
  const bool idle = ready && !_nymeriaCommandPending && !_nymeriaRunning && !_nymeriaPlanPending
      && !campaignIsBusy();
  bool hasTask = false;
  for (int i=0; i<_nymeriaTasks->count(); ++i) hasTask |= _nymeriaTasks->item(i)->checkState()==Qt::Checked;
  _nymeriaImport->setEnabled(idle); _nymeriaSync->setEnabled(idle);
  _nymeriaRefresh->setEnabled(ready && !_nymeriaInventoryPending);
  _nymeriaPlanButton->setEnabled(idle && hasTask && _nymeriaMinimum->value()<=_nymeriaMaximum->value());
  const bool spaceComplete = _nymeriaPlan.value("space_check_complete")==QJsonValue(true);
  const bool spaceAllowed = spaceComplete ? _nymeriaPlan.value("fits_available_disk")==QJsonValue(true)
      : _nymeriaPlan.value("fits_source_downloads")==QJsonValue(true);
  _nymeriaAcquire->setText(!_nymeriaPlan.isEmpty() && !spaceComplete
      ? QStringLiteral("Verificar espaço e baixar antecipadamente") : QStringLiteral("Baixar plano antecipadamente"));
  _nymeriaAcquire->setEnabled(idle && !_nymeriaPlan.value("selected_seq_ids").toArray().isEmpty() && spaceAllowed
      && _nymeriaPlan.value("disk_free_bytes").toDouble() >= _nymeriaPlan.value("download_bytes").toDouble()
          + double(_nymeriaReserve->value()) * 1024 * 1024 * 1024);
  _nymeriaStop->setEnabled(ready && _nymeriaRunning && !_nymeriaCommandPending);
  _nymeriaPrevious->setEnabled(ready && !_nymeriaInventoryPending && _nymeriaOffset>0);
  _nymeriaNext->setEnabled(ready && !_nymeriaInventoryPending && _nymeriaOffset+50<_nymeriaTotal);
  _nymeriaUseCombined->setEnabled(ready && !campaignIsBusy());
}

void MainWindow::invalidateNymeriaPlan() {
  ++_nymeriaPlanRevision;
  _nymeriaPlanPending = false;
  _nymeriaPlan = {};
  _nymeriaPotential->setText(QStringLiteral("Planeje o conteúdo para as categorias e a duração escolhidas. O vídeo e o IMU serão medidos após o download."));
  updateNymeriaLibraryActions();
}

void MainWindow::loadNymeriaLibrary() {
  if (!_backendReady || _closing || _nymeriaInventoryPending) return;
  _nymeriaInventoryPending = true;
  const quint64 revision = ++_nymeriaRequestId;
  const quint64 jobRevision = _nymeriaJobRevision;
  const int generation = _operationBackendGeneration;
  QUrlQuery query;
  query.addQueryItem("q", _nymeriaQuery->text().trimmed()); query.addQueryItem("state", _nymeriaFilter->currentData().toString());
  query.addQueryItem("limit", "50"); query.addQueryItem("offset", QString::number(_nymeriaOffset));
  updateNymeriaLibraryActions();
  _api.get(QStringLiteral("/api/library/nymeria/sequences?")+query.toString(QUrl::FullyEncoded),
      [this, revision, jobRevision, generation](bool ok, const QJsonDocument& doc, const QString& error) {
    if (_closing || revision!=_nymeriaRequestId || generation!=_operationBackendGeneration) return;
    _nymeriaInventoryPending = false;
    if (ok && doc.object().value("loading")==QJsonValue(true)) {
      _nymeriaSummary->setText(doc.object().value("message").toString(QStringLiteral("Lendo catálogo Nymeria…")));
      QTimer::singleShot(1200, this, [this, revision] {
        if (revision==_nymeriaRequestId && isPageActive(4)) loadNymeriaLibrary();
      });
    } else if (!ok) _nymeriaSummary->setText(QStringLiteral("Leitura indisponível · dados anteriores preservados. ")+error);
    else {
      renderNymeriaLibrary(doc.object());
      if (jobRevision==_nymeriaJobRevision && !_nymeriaCommandPending && doc.object().value("worker").isObject())
        renderNymeriaJob(doc.object().value("worker").toObject());
    }
    updateNymeriaLibraryActions();
  });
}

void MainWindow::renderNymeriaLibrary(const QJsonObject& result) {
  const auto summary = result.value("summary").toObject();
  if (!result.value("items").isArray() || !result.value("task_names").isArray()
      || !result.value("total").isDouble() || result.value("offset").toInt(-1)!=_nymeriaOffset
      || !summary.value("sequence_count").isDouble()) {
    _nymeriaSummary->setText(QStringLiteral("Resposta do catálogo inválida · dados anteriores preservados.")); return;
  }
  _nymeriaTotal = result.value("total").toInt();
  const auto counts = summary.value("by_state").toObject();
  _nymeriaSummary->setText(QStringLiteral("%1 sequências · %2 h declaradas na origem · %3 ações anotadas\n%4 catalogadas · %5 com fontes baixadas · %6 medidas · %7 livres no disco")
      .arg(summary.value("sequence_count").toInt()).arg(summary.value("declared_hours").toDouble(),0,'f',2)
      .arg(summary.value("atomic_action_count").toInt()).arg(counts.value("cataloged").toInt())
      .arg(counts.value("downloaded").toInt()).arg(counts.value("measured").toInt())
      .arg(bytesText(qint64(summary.value("disk_free_bytes").toDouble()))));
  QSet<QString> checked;
  for (int i=0; i<_nymeriaTasks->count(); ++i)
    if (_nymeriaTasks->item(i)->checkState()==Qt::Checked) checked.insert(_nymeriaTasks->item(i)->data(Qt::UserRole).toString());
  const bool firstTasks = _nymeriaTasks->count()==0;
  if (firstTasks) {
    QSet<QString> availableNames;
    for (const auto& value : result.value("task_names").toArray())
      availableNames.insert(value.toString());
    checked.unite(campaignLibraryTaskSelection(availableNames));
  }
  {
    const QSignalBlocker blocker(_nymeriaTasks);
    _nymeriaTasks->clear();
    for (const auto& value : result.value("task_names").toArray()) {
      const QString name = value.toString();
      if (name.trimmed().isEmpty()) continue;
      auto* item = new QListWidgetItem(name, _nymeriaTasks); item->setData(Qt::UserRole,name);
      item->setCheckState(checked.contains(name)?Qt::Checked:Qt::Unchecked);
    }
  }
  const auto items = result.value("items").toArray();
  _nymeriaTable->setRowCount(items.size());
  const QHash<QString,QString> states{{"missing","Ainda não baixada"},{"cataloged","Anotações disponíveis"},
      {"downloaded","Fontes baixadas"},{"measured","Vídeo e IMU medidos"},
      {"partial","Download parcial"},{"no_annotations","Sem anotações"}};
  for (int i=0; i<items.size(); ++i) {
    const auto row = items[i].toObject();
    _nymeriaTable->setItem(i,0,cell(row.value("seq_id").toString()));
    _nymeriaTable->setItem(i,1,cell(row.value("script").toString()));
    _nymeriaTable->setItem(i,2,cell(row.value("declared_duration_s").isDouble()
        ? QStringLiteral("%1 min").arg(row.value("declared_duration_s").toDouble()/60,0,'f',1) : QStringLiteral("—")));
    _nymeriaTable->setItem(i,3,cell(states.value(row.value("state").toString(),QStringLiteral("Revisar"))));
    _nymeriaTable->setItem(i,4,cell(bytesText(qint64(row.value("download_bytes").toDouble()))));
  }
  _nymeriaCount->setText(QStringLiteral("%1–%2 de %3 sequências").arg(items.isEmpty()?0:_nymeriaOffset+1)
      .arg(_nymeriaOffset+items.size()).arg(_nymeriaTotal));
}

void MainWindow::startNymeriaCommand(const QString& path, const QJsonObject& body) {
  if (!_backendReady || _closing || _nymeriaCommandPending || _nymeriaRunning || _nymeriaPlanPending) return;
  _nymeriaCommandPending = true;
  const quint64 revision = ++_nymeriaJobRevision;
  const int generation = _operationBackendGeneration;
  invalidateNymeriaPlan();
  _nymeriaState->setText(QStringLiteral("Solicitando preparação da Biblioteca…"));
  updateNymeriaLibraryActions();
  _api.post(path,body,[this, revision, generation](bool ok,const QJsonDocument& doc,const QString& error) {
    if (_closing || revision!=_nymeriaJobRevision || generation!=_operationBackendGeneration) return;
    _nymeriaCommandPending = false;
    if (!ok || doc.object().value("ok")!=QJsonValue(true) || !doc.object().value("worker").isObject()) {
      _nymeriaState->setText(QStringLiteral("Preparação não confirmada. ")+error);
      updateNymeriaLibraryActions(); pollNymeriaJob(); return;
    }
    renderNymeriaJob(doc.object().value("worker").toObject());
    pollNymeriaJob();
  });
}

void MainWindow::importNymeriaManifest(const QString& path) {
  startNymeriaCommand(QStringLiteral("/api/library/nymeria/import"),{{"path",path}});
}

void MainWindow::syncNymeriaCatalog() {
  startNymeriaCommand(QStringLiteral("/api/library/nymeria/sync"),{});
}

void MainWindow::planNymeriaLibrary() {
  if (!_nymeriaPlanButton->isEnabled()) return;
  QJsonArray tasks;
  for (int i=0; i<_nymeriaTasks->count(); ++i)
    if (_nymeriaTasks->item(i)->checkState()==Qt::Checked) tasks.append(_nymeriaTasks->item(i)->data(Qt::UserRole).toString());
  if (tasks.isEmpty()) return;
  _nymeriaPlan = {}; _nymeriaPlanPending = true;
  const quint64 revision = ++_nymeriaPlanRevision;
  _nymeriaPotential->setText(QStringLiteral("Procurando conteúdo nas anotações do catálogo completo…"));
  updateNymeriaLibraryActions();
  pollNymeriaPlan({{"task_names",tasks},{"min_dur_s",_nymeriaMinimum->value()*60},
      {"max_dur_s",_nymeriaMaximum->value()*60},{"target_seconds",_nymeriaTargetHours->value()*3600},
      {"min_free_gb",_nymeriaReserve->value()}},revision);
}

void MainWindow::pollNymeriaPlan(const QJsonObject& body, quint64 revision) {
  const int generation = _operationBackendGeneration;
  _api.post(QStringLiteral("/api/library/nymeria/plan"),body,
      [this,body,revision,generation](bool ok,const QJsonDocument& doc,const QString& error) {
    if (_closing || revision!=_nymeriaPlanRevision || generation!=_operationBackendGeneration) return;
    const auto result = doc.object();
    if (ok && result.value("loading")==QJsonValue(true)) {
      _nymeriaPotential->setText(result.value("message").toString(QStringLiteral("Planejando conteúdo Nymeria…")));
      QTimer::singleShot(1200,this,[this,body,revision] {
        if (!_closing && _backendReady && revision==_nymeriaPlanRevision) pollNymeriaPlan(body,revision);
      }); return;
    }
    _nymeriaPlanPending = false;
    const auto ids = result.value("selected_seq_ids").toArray();
    const bool spaceComplete = result.value("space_check_complete")==QJsonValue(true);
    bool valid = result.value("selected_seq_ids").isArray() && result.value("campaign_ready")==QJsonValue(false)
        && result.value("target_found_in_annotations").isBool() && result.value("space_check_complete").isBool()
        && result.value("fits_source_downloads").isBool()
        && (spaceComplete ? result.value("fits_available_disk").isBool() : result.value("fits_available_disk").isNull())
        && result.value("unknown_extraction_seq_ids").isArray();
    for (const auto* key : {"potential_seconds","selected_potential_seconds","download_bytes","disk_free_bytes","extraction_bytes"})
      valid &= result.value(QLatin1String(key)).isDouble() && std::isfinite(result.value(QLatin1String(key)).toDouble())
          && result.value(QLatin1String(key)).toDouble()>=0 && result.value(QLatin1String(key)).toDouble()<=9007199254740991.;
    for (const auto& value : ids) valid &= value.isString() && !value.toString().trimmed().isEmpty();
    const auto unknownExtraction = result.value("unknown_extraction_seq_ids").toArray();
    valid &= spaceComplete == unknownExtraction.isEmpty();
    for (const auto& value : unknownExtraction) valid &= value.isString() && !value.toString().trimmed().isEmpty() && ids.contains(value);
    if (!ok || !valid) _nymeriaPotential->setText(QStringLiteral("Plano não confirmado. ")+(ok?QStringLiteral("Resposta inválida do serviço."):error));
    else {
      _nymeriaPlan = result;
      QString summary = QStringLiteral("%1 h compatíveis nas anotações · %2 sequências no plano · %3 para baixar\n%4 h no plano para a meta de %5 h. Vídeo e IMU ainda precisam ser medidos.")
          .arg(result.value("potential_seconds").toDouble()/3600,0,'f',2).arg(ids.size())
          .arg(bytesText(qint64(result.value("download_bytes").toDouble())))
          .arg(result.value("selected_potential_seconds").toDouble()/3600,0,'f',2).arg(body.value("target_seconds").toDouble()/3600,0,'f',2);
      if (!spaceComplete)
        summary += QStringLiteral("\nO espaço para extrair o IMU será conferido antes de baixar as fontes.");
      else if (result.value("fits_available_disk")!=QJsonValue(true))
        summary += QStringLiteral("\nEspaço insuficiente para as fontes, a extração dos sensores e a reserva escolhida.");
      _nymeriaPotential->setText(summary);
    }
    updateNymeriaLibraryActions();
  });
}

void MainWindow::acquireNymeriaPlan() {
  if (!_nymeriaAcquire->isEnabled()) return;
  startNymeriaCommand(QStringLiteral("/api/library/nymeria/download"),
      {{"seq_ids",_nymeriaPlan.value("selected_seq_ids")},{"min_free_gb",_nymeriaReserve->value()}});
}

void MainWindow::pollNymeriaJob() {
  if (!_backendReady || _closing || _nymeriaJobPending) return;
  _nymeriaJobPending = true;
  const quint64 revision = _nymeriaJobRevision;
  const int generation = _operationBackendGeneration;
  _api.get(QStringLiteral("/api/library/nymeria/operation"),[this,revision,generation](bool ok,const QJsonDocument& doc,const QString& error) {
    if (_closing || generation!=_operationBackendGeneration) return;
    _nymeriaJobPending = false;
    if (revision!=_nymeriaJobRevision) return;
    if (ok) renderNymeriaJob(doc.object());
    else {
      _nymeriaState->setText(QStringLiteral("Estado da preparação indisponível. ")+error);
      if (isPageActive(4)) _nymeriaPoll.start();
    }
  });
}

void MainWindow::renderNymeriaJob(const QJsonObject& result) {
  const QString state = result.value("state").toString();
  if (!result.value("running").isBool() || !QStringList{"idle","running","done","stopped","error"}.contains(state)) {
    _nymeriaState->setText(QStringLiteral("Resposta da preparação inválida; atualize para confirmar o estado.")); return;
  }
  _nymeriaRunning = result.value("running").toBool();
  const QString message = result.value("message").toString();
  _nymeriaState->setText(message.isEmpty() ? (state=="idle"?QStringLiteral("Nenhuma preparação em andamento"):QStringLiteral("Preparando Biblioteca…")) : message);
  const double total = result.value("total").toDouble();
  const double completed = result.value("completed").toDouble();
  if (_nymeriaRunning && total<=0) _nymeriaProgress->setRange(0,0);
  else {
    _nymeriaProgress->setRange(0,100);
    _nymeriaProgress->setValue(state=="done" && !_nymeriaRunning ? 100
        : total>0 && std::isfinite(total) && std::isfinite(completed)
            ? int(qBound(0.,100*completed/total,100.)) : 0);
  }
  if (_nymeriaRunning && isPageActive(4)) _nymeriaPoll.start();
  else _nymeriaPoll.stop();
  if (!_nymeriaRunning && state!="idle") {
    const QString run = result.value("run_id").toString();
    if (!run.isEmpty() && run!=_nymeriaLastTerminalRun) {
      _nymeriaLastTerminalRun = run;
      invalidateNymeriaPlan(); loadNymeriaLibrary();
      if (state=="done" && QStringList{"download", "import", "sync"}.contains(result.value("operation").toString())) {
        ++_taskLoadGeneration;
        if (selectedCampaignDataset() != QStringLiteral("ego4d")) loadTasks();
      }
    }
  }
  updateNymeriaLibraryActions();
}

void MainWindow::stopNymeriaJob() {
  if (!_nymeriaStop->isEnabled()) return;
  _nymeriaCommandPending = true;
  const quint64 revision = ++_nymeriaJobRevision;
  const int generation = _operationBackendGeneration;
  updateNymeriaLibraryActions();
  _api.post(QStringLiteral("/api/library/nymeria/stop"),{},[this,revision,generation](bool ok,const QJsonDocument& doc,const QString& error) {
    if (_closing || revision!=_nymeriaJobRevision || generation!=_operationBackendGeneration) return;
    _nymeriaCommandPending = false;
    if (ok && doc.object().value("ok")==QJsonValue(true) && doc.object().value("worker").isObject())
      renderNymeriaJob(doc.object().value("worker").toObject());
    else _nymeriaState->setText(QStringLiteral("Parada não confirmada. ")+error);
    updateNymeriaLibraryActions(); pollNymeriaJob();
  });
}

QWidget* MainWindow::buildNymeriaLibraryTab() {
  auto* tab = new QWidget;
  auto* layout = new QVBoxLayout(tab);
  layout->setContentsMargins(0, 12, 0, 0);
  layout->setSpacing(12);
  _nymeriaSummary = quietLabel(QStringLiteral("Importe o manifesto NymeriaPlus e sincronize as anotações para ampliar o acervo local."));
  _nymeriaSummary->setObjectName(QStringLiteral("nymeriaLibrarySummary"));
  _nymeriaSummary->setWordWrap(true);
  layout->addWidget(_nymeriaSummary);
  auto* sources = new QHBoxLayout;
  _nymeriaImport = new QPushButton(QStringLiteral("Importar e sincronizar JSON…"));
  _nymeriaImport->setObjectName(QStringLiteral("nymeriaImportManifest"));
  _nymeriaSync = new QPushButton(QStringLiteral("Sincronizar catálogo"));
  _nymeriaSync->setObjectName(QStringLiteral("nymeriaSyncCatalog"));
  _nymeriaRefresh = new QPushButton(QStringLiteral("Atualizar acervo"));
  _nymeriaRefresh->setObjectName(QStringLiteral("nymeriaRefresh"));
  sources->addWidget(_nymeriaImport); sources->addWidget(_nymeriaSync); sources->addWidget(_nymeriaRefresh);
  sources->addStretch(); layout->addLayout(sources);
  connect(_nymeriaImport, &QPushButton::clicked, this, [this] {
    const QString path = QFileDialog::getOpenFileName(this, QStringLiteral("Importar links Nymeria"),
        {}, QStringLiteral("Manifesto JSON (*.json)"));
    if (!path.isEmpty()) importNymeriaManifest(path);
  });
  connect(_nymeriaSync, &QPushButton::clicked, this, &MainWindow::syncNymeriaCatalog);
  connect(_nymeriaRefresh, &QPushButton::clicked, this, [this] { loadNymeriaLibrary(); pollNymeriaJob(); });
  layout->addWidget(quietLabel(QStringLiteral("Preparação manual opcional · categorias")));
  _nymeriaTasks = new QListWidget;
  _nymeriaTasks->setObjectName(QStringLiteral("nymeriaTaskSelection"));
  _nymeriaTasks->setMinimumHeight(100); _nymeriaTasks->setMaximumHeight(150);
  layout->addWidget(_nymeriaTasks);
  connect(_nymeriaTasks, &QListWidget::itemChanged, this, [this] { invalidateNymeriaPlan(); });
  auto* parameters = new QHBoxLayout;
  parameters->addWidget(quietLabel(QStringLiteral("Meta por conta")));
  _nymeriaTargetHours = new QDoubleSpinBox;
  _nymeriaTargetHours->setObjectName(QStringLiteral("nymeriaTargetHours"));
  _nymeriaTargetHours->setRange(0.25, 12); _nymeriaTargetHours->setValue(8);
  _nymeriaTargetHours->setSuffix(QStringLiteral(" h")); _nymeriaTargetHours->setKeyboardTracking(false);
  parameters->addWidget(_nymeriaTargetHours);
  parameters->addWidget(quietLabel(QStringLiteral("Recortes")));
  _nymeriaMinimum = new QSpinBox; _nymeriaMaximum = new QSpinBox;
  _nymeriaMinimum->setObjectName(QStringLiteral("nymeriaMinimumDuration"));
  _nymeriaMaximum->setObjectName(QStringLiteral("nymeriaMaximumDuration"));
  for (auto* spin : {_nymeriaMinimum, _nymeriaMaximum}) {
    spin->setRange(1, 30); spin->setSuffix(QStringLiteral(" min")); spin->setKeyboardTracking(false);
  }
  _nymeriaMinimum->setValue(5); _nymeriaMaximum->setValue(30);
  parameters->addWidget(_nymeriaMinimum); parameters->addWidget(quietLabel(QStringLiteral("até"))); parameters->addWidget(_nymeriaMaximum);
  parameters->addStretch(); layout->addLayout(parameters);
  auto* preparation = new QHBoxLayout;
  preparation->addWidget(quietLabel(QStringLiteral("Manter livre")));
  _nymeriaReserve = new QSpinBox;
  _nymeriaReserve->setObjectName(QStringLiteral("nymeriaDiskReserve"));
  _nymeriaReserve->setRange(5, 1000); _nymeriaReserve->setValue(50); _nymeriaReserve->setSuffix(QStringLiteral(" GiB"));
  preparation->addWidget(_nymeriaReserve); preparation->addStretch();
  _nymeriaPlanButton = new QPushButton(QStringLiteral("Estimar conteúdo"));
  _nymeriaPlanButton->setObjectName(QStringLiteral("nymeriaPlan"));
  _nymeriaAcquire = new QPushButton(QStringLiteral("Baixar plano antecipadamente"));
  _nymeriaAcquire->setObjectName(QStringLiteral("nymeriaAcquirePlan"));
  preparation->addWidget(_nymeriaPlanButton); preparation->addWidget(_nymeriaAcquire); layout->addLayout(preparation);
  connect(_nymeriaTargetHours, qOverload<double>(&QDoubleSpinBox::valueChanged), this, [this] { invalidateNymeriaPlan(); });
  for (auto* spin : {_nymeriaMinimum, _nymeriaMaximum, _nymeriaReserve})
    connect(spin, qOverload<int>(&QSpinBox::valueChanged), this, [this] { invalidateNymeriaPlan(); });
  connect(_nymeriaPlanButton, &QPushButton::clicked, this, &MainWindow::planNymeriaLibrary);
  connect(_nymeriaAcquire, &QPushButton::clicked, this, &MainWindow::acquireNymeriaPlan);
  _nymeriaPotential = quietLabel(QStringLiteral("As anotações estimam o conteúdo. Na campanha, cada fonte é baixada e seu vídeo e IMU são medidos antes do envio. O download antecipado é opcional."));
  _nymeriaPotential->setObjectName(QStringLiteral("nymeriaPlanSummary")); _nymeriaPotential->setWordWrap(true);
  layout->addWidget(_nymeriaPotential);
  auto* campaignOrigin = new QHBoxLayout;
  auto* originHelp = quietLabel(QStringLiteral("Escolha Ego4D + NymeriaPlus em Nova campanha para adquirir Ego4D e Nymeria sob demanda, preparar, enviar e liberar cada recorte confirmado."));
  originHelp->setWordWrap(true); campaignOrigin->addWidget(originHelp, 1);
  _nymeriaUseCombined = new QPushButton(QStringLiteral("Usar Ego4D + Nymeria"));
  _nymeriaUseCombined->setObjectName(QStringLiteral("nymeriaUseCombined"));
  campaignOrigin->addWidget(_nymeriaUseCombined); layout->addLayout(campaignOrigin);
  connect(_nymeriaUseCombined, &QPushButton::clicked, this, [this] {
    if (campaignIsBusy()) return;
    selectCombinedCampaignDataset();
    navigate(3);
  });
  auto* progress = new QHBoxLayout;
  _nymeriaState = quietLabel(QStringLiteral("Nenhuma preparação em andamento"));
  _nymeriaState->setObjectName(QStringLiteral("nymeriaJobState")); _nymeriaState->setWordWrap(true);
  progress->addWidget(_nymeriaState, 1);
  _nymeriaStop = new QPushButton(QStringLiteral("Parar com segurança"));
  _nymeriaStop->setObjectName(QStringLiteral("nymeriaStop"));
  progress->addWidget(_nymeriaStop); layout->addLayout(progress);
  connect(_nymeriaStop, &QPushButton::clicked, this, &MainWindow::stopNymeriaJob);
  _nymeriaProgress = new QProgressBar;
  _nymeriaProgress->setObjectName(QStringLiteral("nymeriaJobProgress")); _nymeriaProgress->setRange(0, 100); _nymeriaProgress->setValue(0);
  layout->addWidget(_nymeriaProgress);
  auto* filters = new QHBoxLayout;
  _nymeriaQuery = new QLineEdit;
  _nymeriaQuery->setObjectName(QStringLiteral("nymeriaLibraryQuery")); _nymeriaQuery->setPlaceholderText(QStringLiteral("Buscar sequência ou atividade")); _nymeriaQuery->setMaxLength(200);
  _nymeriaFilter = new ComboBox;
  _nymeriaFilter->setObjectName(QStringLiteral("nymeriaLibraryFilter"));
  for (const auto& pair : QList<QPair<QString, QString>>{{"Todos", "all"}, {"Catalogadas", "cataloged"},
      {"Fontes baixadas", "downloaded"}, {"Vídeo e IMU medidos", "measured"}, {"Parciais", "partial"},
      {"Ainda não baixadas", "missing"}, {"Sem anotações", "no_annotations"}})
    _nymeriaFilter->addItem(pair.first, pair.second);
  filters->addWidget(_nymeriaQuery, 1); filters->addWidget(_nymeriaFilter); layout->addLayout(filters);
  const auto filterChanged = [this] {
    ++_nymeriaRequestId; _nymeriaInventoryPending = false;
    _nymeriaOffset = 0; loadNymeriaLibrary();
  };
  connect(_nymeriaQuery, &QLineEdit::returnPressed, this, filterChanged);
  connect(_nymeriaFilter, qOverload<int>(&QComboBox::currentIndexChanged), this, filterChanged);
  _nymeriaTable = new QTableWidget(0, 5);
  _nymeriaTable->setObjectName(QStringLiteral("nymeriaLibraryTable"));
  _nymeriaTable->setHorizontalHeaderLabels({QStringLiteral("Sequência"), QStringLiteral("Atividade"), QStringLiteral("Duração declarada"), QStringLiteral("Estado local"), QStringLiteral("Download restante")});
  _nymeriaTable->setEditTriggers(QAbstractItemView::NoEditTriggers); _nymeriaTable->setSelectionBehavior(QAbstractItemView::SelectRows);
  _nymeriaTable->setMinimumHeight(240); _nymeriaTable->setMaximumHeight(420); _nymeriaTable->verticalHeader()->hide();
  _nymeriaTable->horizontalHeader()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
  _nymeriaTable->horizontalHeader()->setSectionResizeMode(1, QHeaderView::Stretch);
  for (int column=2; column<5; ++column) _nymeriaTable->horizontalHeader()->setSectionResizeMode(column, QHeaderView::ResizeToContents);
  layout->addWidget(_nymeriaTable);
  auto* paging = new QHBoxLayout;
  _nymeriaCount = quietLabel(QStringLiteral("Aguardando catálogo")); _nymeriaCount->setObjectName(QStringLiteral("nymeriaLibraryCount"));
  paging->addWidget(_nymeriaCount, 1);
  _nymeriaPrevious = new QPushButton(QStringLiteral("Anterior")); _nymeriaNext = new QPushButton(QStringLiteral("Próxima"));
  _nymeriaPrevious->setObjectName(QStringLiteral("nymeriaPrevious")); _nymeriaNext->setObjectName(QStringLiteral("nymeriaNext"));
  paging->addWidget(_nymeriaPrevious); paging->addWidget(_nymeriaNext); layout->addLayout(paging);
  connect(_nymeriaPrevious, &QPushButton::clicked, this, [this] { _nymeriaOffset = qMax(0, _nymeriaOffset - 50); loadNymeriaLibrary(); });
  connect(_nymeriaNext, &QPushButton::clicked, this, [this] { _nymeriaOffset += 50; loadNymeriaLibrary(); });
  _nymeriaPoll.setInterval(1200);
  connect(&_nymeriaPoll, &QTimer::timeout, this, &MainWindow::pollNymeriaJob);
  updateNymeriaLibraryActions();
  return tab;
}

