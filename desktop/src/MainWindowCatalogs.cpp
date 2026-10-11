#include "MainWindowSupport.hpp"

QWidget* MainWindow::buildProviderCatalogPage() {
  auto* body = new QWidget;
  auto* layout = new QVBoxLayout(body);
  layout->setContentsMargins(0, 0, 0, 0);
  auto* options = new QWidget;
  auto* form = new QFormLayout(options);
  _providerCatalogDataset = new ComboBox;
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
  auto* note = quietLabel(QStringLiteral("Prepara anotações e índices reutilizáveis, sem baixar vídeos ou IMU. As categorias permitidas pela conta, o histórico e a capacidade sem repetições serão verificados na campanha."));
  note->setWordWrap(true);
  form->addRow(note);
  _providerCatalogPrepare = primaryButton(QStringLiteral("Preparar / atualizar catálogos"));
  _providerCatalogPrepare->setObjectName(QStringLiteral("providerCatalogPrepare"));
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
  monitorLayout->addWidget(_providerCatalogSummary, 1);
  layout->addWidget(card(QStringLiteral("Acompanhamento"), monitor), 1);
  auto* campaign = new QPushButton(QStringLiteral("Abrir campanha"));
  connect(campaign, &QPushButton::clicked, this, [this] { _navigation->setCurrentRow(3); });
  layout->addWidget(campaign);
  return pageShell(QStringLiteral("Catálogos"),
      QStringLiteral("Prepare os provedores antes de selecionar e executar a campanha."), body);
}

void MainWindow::onProviderCatalogBackendAvailabilityChanged(bool ready, bool generationChanged) {
  if (!_providerCatalogPrepare) return;
  if (generationChanged && _providerCatalogBusy) {
    _providerCatalogBusy = false;
    _providerCatalogProgress->setRange(0, 100);
    _providerCatalogProgress->setValue(0);
    _providerCatalogPercent->setText(QStringLiteral("Interrompido"));
    _providerCatalogStatus->setText(QStringLiteral("O serviço foi desconectado. Após reconectar, preparar verifica e reutiliza os índices já salvos."));
  }
  _providerCatalogPrepare->setEnabled(ready && !_providerCatalogBusy);
  _providerCatalogDataset->setEnabled(!_providerCatalogBusy);
  _providerCatalogMinimum->setEnabled(!_providerCatalogBusy);
  _providerCatalogMaximum->setEnabled(!_providerCatalogBusy);
}

void MainWindow::loadProviderCatalogStatus() {
  if (_providerCatalogBusy || !_backendReady) return;
  const int generation = _operationBackendGeneration;
  _api.get(QStringLiteral("/api/catalogs/providers"), [this, generation](bool ok, const QJsonDocument& doc, const QString& error) {
    if (_closing || generation != _operationBackendGeneration || _providerCatalogBusy) return;
    if (!ok) { _providerCatalogStatus->setText(error); return; }
    if (_providerCatalogSummary->toPlainText().isEmpty()) {
      const auto indexes = doc.object().value(QStringLiteral("indexes_found")).toObject();
      _providerCatalogStatus->setText(doc.object().value(QStringLiteral("message")).toString());
      _providerCatalogSummary->setPlainText(QStringLiteral("Índice Ego4D: %1\nÍndice NymeriaPlus: %2\n\nA preparação confere se os índices correspondem às fontes atuais.")
          .arg(indexes.value(QStringLiteral("ego4d")).toBool() ? QStringLiteral("encontrado") : QStringLiteral("a preparar"),
               indexes.value(QStringLiteral("nymeria")).toBool() ? QStringLiteral("encontrado") : QStringLiteral("a preparar")));
    }
  });
}

void MainWindow::prepareProviderCatalog() {
  if (_providerCatalogBusy || !_backendReady || _closing) return;
  if (_providerCatalogMinimum->value() > _providerCatalogMaximum->value()) {
    _providerCatalogStatus->setText(QStringLiteral("A duração mínima deve ser menor ou igual à máxima.")); return;
  }
  _providerCatalogBusy = true;
  _providerCatalogPrepare->setEnabled(false);
  _providerCatalogDataset->setEnabled(false);
  _providerCatalogMinimum->setEnabled(false);
  _providerCatalogMaximum->setEnabled(false);
  _providerCatalogProgress->setRange(0, 0);
  _providerCatalogPercent->setText(QStringLiteral("…"));
  _providerCatalogStatus->setText(QStringLiteral("Solicitando a preparação dos índices…"));
  _providerCatalogSummary->clear();
  const int generation = _operationBackendGeneration;
  const QString path = QStringLiteral("/api/catalogs/providers/prepare?dataset=%1&min_dur_s=%2&max_dur_s=%3")
      .arg(encoded(_providerCatalogDataset->currentData().toString()))
      .arg(_providerCatalogMinimum->value() * 60).arg(_providerCatalogMaximum->value() * 60);
  _api.post(path, {}, [this, generation](bool ok, const QJsonDocument& doc, const QString& error) {
    if (_closing || generation != _operationBackendGeneration) return;
    _providerCatalogBusy = false;
    _providerCatalogPrepare->setEnabled(true);
    _providerCatalogDataset->setEnabled(true);
    _providerCatalogMinimum->setEnabled(true);
    _providerCatalogMaximum->setEnabled(true);
    _providerCatalogProgress->setRange(0, 100);
    _providerCatalogProgress->setValue(ok ? 100 : 0);
    _providerCatalogPercent->setText(ok ? QStringLiteral("100%") : QStringLiteral("Falha"));
    _providerCatalogStatus->setText(ok ? doc.object().value(QStringLiteral("message")).toString() : error);
    if (!ok) return;
    QStringList summaries;
    for (const auto& value : doc.object().value(QStringLiteral("providers")).toArray()) {
      const auto item = value.toObject();
      summaries << QStringLiteral("%1: %2 candidatos · %3/%4 regras com conteúdo · até %5 h anotadas")
          .arg(item.value(QStringLiteral("provider")).toString())
          .arg(item.value(QStringLiteral("candidates")).toInt())
          .arg(item.value(QStringLiteral("rules_with_candidates")).toInt())
          .arg(item.value(QStringLiteral("rules_checked")).toInt())
          .arg(item.value(QStringLiteral("annotation_hours_upper_bound")).toDouble(), 0, 'f', 2);
    }
    summaries << QStringLiteral("\nEstimativas antes de deduplicação e da consulta às categorias atuais do Minute. A preparação de cada envio ainda valida vídeo e sensores reais.");
    _providerCatalogSummary->setPlainText(summaries.join(QLatin1Char('\n')));
  }, [this, generation](const QJsonObject& progress) {
    if (_closing || generation != _operationBackendGeneration) return;
    const int completed = progress.value(QStringLiteral("completed")).toInt(-1);
    const int total = progress.value(QStringLiteral("total")).toInt(-1);
    QString message = progress.value(QStringLiteral("message")).toString();
    if (total > 0 && completed >= 0 && completed <= total) {
      const int percent = int(qint64(completed) * 100 / total);
      _providerCatalogProgress->setRange(0, 100);
      _providerCatalogProgress->setValue(percent);
      _providerCatalogPercent->setText(QStringLiteral("%1%").arg(percent));
      message += QStringLiteral("\nEtapa atual: %1 de %2 concluídos.").arg(completed).arg(total);
    } else {
      _providerCatalogProgress->setRange(0, 0);
      _providerCatalogPercent->setText(QStringLiteral("…"));
    }
    message += QStringLiteral("\nTempo: %1 s · Sem avanço confirmado há %2 s.")
        .arg(progress.value(QStringLiteral("elapsed_s")).toInt())
        .arg(progress.value(QStringLiteral("idle_s")).toInt());
    _providerCatalogStatus->setText(message);
  });
}
