#include "MainWindowSupport.hpp"
#include "MainWindow.hpp"

namespace {
bool collectRegistrationProfiles(QWidget* parent, int count, QJsonArray& profiles) {

  QDialog dialog(parent);
  dialog.setObjectName(QStringLiteral("registrationProfileDialog"));
  dialog.setWindowTitle(QStringLiteral("Dados dos titulares"));
  dialog.resize(620, qMin(560, 190 + count * 48));
  auto* layout = new QVBoxLayout(&dialog);
  auto* description = new QLabel(QStringLiteral(
      "Informe os dados reais de cada titular. O cadastro salvará o perfil e vinculará o e-mail Minute na tarefa."));
  description->setWordWrap(true);
  layout->addWidget(description);

  auto* table = new QTableWidget(count, 3, &dialog);
  table->setHorizontalHeaderLabels({QStringLiteral("Mês de nascimento"), QStringLiteral("Ano de nascimento"), QStringLiteral("Gênero")});

  table->horizontalHeader()->setSectionResizeMode(QHeaderView::Stretch);
  table->verticalHeader()->setDefaultSectionSize(52);
  const QDate today = QDate::currentDate();
  for (int row = 0; row < count; ++row) {

    table->setVerticalHeaderItem(row, new QTableWidgetItem(QStringLiteral("Titular %1").arg(row + 1)));

    auto* month = new QSpinBox(table);
    month->setObjectName(QStringLiteral("profileMonth%1").arg(row));
    month->setRange(0, 12); month->setSpecialValueText(QStringLiteral("Selecione"));

    auto* year = new QSpinBox(table);
    year->setObjectName(QStringLiteral("profileYear%1").arg(row));
    year->setRange(1899, today.year()); year->setSpecialValueText(QStringLiteral("Informe"));

    auto* gender = new ComboBox(&dialog);

    gender->setObjectName(QStringLiteral("profileGender%1").arg(row));

    gender->addItem(QStringLiteral("Selecione"), QString());

    gender->addItem(QStringLiteral("Feminino"), QStringLiteral("female"));
    gender->addItem(QStringLiteral("Masculino"), QStringLiteral("male"));
    gender->addItem(QStringLiteral("Não binário"), QStringLiteral("non_binary"));
    gender->addItem(QStringLiteral("Prefiro não informar"), QStringLiteral("prefer_not_to_say"));

    table->setCellWidget(row, 0, month); table->setCellWidget(row, 1, year); table->setCellWidget(row, 2, gender);
  }

  layout->addWidget(table);
  auto* error = new QLabel;
  error->setWordWrap(true); layout->addWidget(error);
  auto* buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, &dialog);
  buttons->button(QDialogButtonBox::Ok)->setText(QStringLiteral("Continuar cadastro"));
  layout->addWidget(buttons);
  QObject::connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
  QObject::connect(buttons, &QDialogButtonBox::accepted, &dialog, [&] {
    QJsonArray result;
    for (int row = 0; row < count; ++row) {
      const int month = qobject_cast<QSpinBox*>(table->cellWidget(row, 0))->value();
      const int year = qobject_cast<QSpinBox*>(table->cellWidget(row, 1))->value();
      const QString gender = qobject_cast<QComboBox*>(table->cellWidget(row, 2))->currentData().toString();
      if (month < 1 || year < 1900 || gender.isEmpty() || QDate(year, month, 1) > today) {
        error->setText(QStringLiteral("Confira os dados do titular %1.").arg(row + 1)); return;
      }
      result.append(QJsonObject{{QStringLiteral("birth_month"), month}, {QStringLiteral("birth_year"), year}, {QStringLiteral("gender"), gender}});
    }
    profiles = result; dialog.accept();
  });

  return dialog.exec() == QDialog::Accepted;
}
}


void MainWindow::onAccountsBackendAvailabilityChanged(bool ready, bool generationChanged) {
  if (generationChanged) {
    _claruConnectionOperationId.clear();
    ++_registrationProxiesRevision;
    ++_bulkRegisterDomainsRevision;
    ++_bulkRegisterPreflightRevision;
    _bulkRegisterRequestInFlight = false;
    _bulkRegisterStarting = false;
    _registrationProxiesReady = false;
    _bulkRegisterPreflightReady = false;
    _bulkRegisterPoll.stop();
    if (!ready) {
      _bulkRegisterPolling = _bulkRegisterPolling || !_bulkRegisterPendingRequestId.isEmpty()
          || !_bulkRegisterAcceptedRequestId.isEmpty() || !_claruBatchRequestId.isEmpty()
          || _bulkRegisterFollowingForeignRequest;
    }
  }
  if (!ready) {
    setAccountTransferBusy(false);
    return;
  }
  // The service can restore a durable registration checkpoint after a restart.
  _bulkRegisterPolling = true;
  setAccountTransferBusy(_accountTransferBusy);
  _bulkRegisterPoll.start();
  pollBulkRegister();
}

void MainWindow::shutdownAccountTimers() {
  _orgMigrationPoll.stop();
  _bulkRegisterPoll.stop();
}

void MainWindow::removePermanentlyDeletedAccountFromAccounts(const QString& email) {
  _accountChecks.remove(email);
  if (!_accountsTable) return;
  for (int row = _accountsTable->rowCount() - 1; row >= 0; --row)
    if (_accountsTable->item(row, 0) && _accountsTable->item(row, 0)->text() == email)
      _accountsTable->removeRow(row);
}

void MainWindow::setAccountTransferBusy(bool busy) {
  _accountTransferBusy = busy;
  busy = busy || _bulkRegisterPolling || _bulkRegisterStarting;
  busy = busy || _orgMigrationRunning;
  _accountsImport->setEnabled(!busy);
  _accountsExport->setEnabled(!busy);
  _accountsExportSelected->setEnabled(!busy);
  _accountAdd->setEnabled(!busy);
  _accountRegister->setEnabled(!busy && _registrationProxiesReady);
  _accountProxy->setEnabled(!busy && _registrationProxiesReady);
  _bulkRegisterProxy->setEnabled(!busy && _registrationProxiesReady);
  _accountProxyImport->setEnabled(!busy);
  _bulkProxyImport->setEnabled(!busy);
  _bulkRegisterDomain->setEnabled(!busy && !_bulkRegisterDomain->currentData().toString().isEmpty());
  _bulkRegisterCount->setEnabled(!busy);
  _claruConnections->setEnabled(!busy);
  _claruEmail->setEnabled(!busy);
  _claruPassword->setEnabled(!busy);
  _claruMinutePassword->setEnabled(!busy);
  _claruConnect->setEnabled(!busy);
  _claruProxy->setEnabled(!busy && _registrationProxiesReady);
  _claruAddMinute->setEnabled(!busy && !_claruMinuteEmail->text().isEmpty());
  _claruRegisterMinute->setEnabled(!busy && _registrationProxiesReady && !_claruMinuteEmail->text().isEmpty());
  _claruBatchAdd->setEnabled(!busy);
  _claruBatchImport->setEnabled(!busy);
  _claruBatchStart->setEnabled(!busy && _registrationProxiesReady && !_claruBatchAccounts.isEmpty());
  _claruBatchResume->setEnabled(!busy && _registrationProxiesReady && !_claruPending.isEmpty());
  _accountsCheckAll->setEnabled(!busy);
  _accountsTable->setEnabled(!busy);
  _accountsMigrate->setEnabled(!busy && _accountsTable->rowCount() > 0);
  updateBulkRegisterStartEnabled();
}

void MainWindow::updateBulkRegisterStartEnabled() {
  if (!_bulkRegisterStart) return;
  const bool busy = _accountTransferBusy || _bulkRegisterPolling || _bulkRegisterStarting
      || _bulkRegisterOutcomeUnknown || _orgMigrationRunning || !_backendReady;
  const bool matchingPreflight = _bulkRegisterPreflightReady
      && _bulkRegisterPreflightDomain == _bulkRegisterDomain->currentData().toString()
      && _bulkRegisterPreflightProxy == _bulkRegisterProxy->currentData().toString();
  _bulkRegisterStart->setEnabled(!busy && _registrationProxiesReady && matchingPreflight);
}

void MainWindow::startOrgMigration() {
  setAccountTransferBusy(true);
  _migrationStatus->setText(QStringLiteral("Iniciando atualização das organizações…"));
  _api.post(QStringLiteral("/api/accounts/migration"), {},
    [this](bool ok, const QJsonDocument& doc, const QString& error) {
      if (ok) {
        _orgMigrationSnapshot = doc.object();
        _orgMigrationRunning = true;
      }
      setAccountTransferBusy(false);
      if (!ok) {
        _migrationStatus->setText(error);
        pollOrgMigration();
        return showError(QStringLiteral("Migração não iniciada"), error);
      }
      _orgMigrationPoll.start();
      pollOrgMigration();
    });
}

void MainWindow::pollOrgMigration() {
  if (_orgMigrationPolling) return;
  _orgMigrationPolling = true;
  _api.get(QStringLiteral("/api/accounts/migration"),
    [this](bool ok, const QJsonDocument& doc, const QString&) {
      _orgMigrationPolling = false;
      if (!ok) {
        if (_orgMigrationRunning)
          _migrationStatus->setText(QStringLiteral("Reconectando ao progresso da migração…"));
        return;
      }
      const bool wasRunning = _orgMigrationRunning;
      _orgMigrationSnapshot = doc.object();
      const QString state = _orgMigrationSnapshot.value("state").toString();
      _orgMigrationRunning = state == QStringLiteral("running");
      setAccountTransferBusy(_accountTransferBusy);
      _migrationReport->setEnabled(!_orgMigrationSnapshot.value("results").toArray().isEmpty());
      if (_orgMigrationRunning) {
        _orgMigrationPoll.start();
        _migrationStatus->setText(QStringLiteral("Atualizando %1 de %2 · %3 · mantenha o QMoney aberto")
            .arg(_orgMigrationSnapshot.value("completed").toInt())
            .arg(_orgMigrationSnapshot.value("total").toInt())
            .arg(_orgMigrationSnapshot.value("current_email").toString()));
      } else {
        _orgMigrationPoll.stop();
        const auto counts = _orgMigrationSnapshot.value("counts").toObject();
        if (state == QStringLiteral("completed"))
          _migrationStatus->setText(QStringLiteral("%1 atualizadas · %2 já corretas · %3 Claru preservadas · %4 com pendências")
              .arg(counts.value("migrated").toInt()).arg(counts.value("already").toInt())
              .arg(counts.value("skipped").toInt())
              .arg(counts.value("error").toInt() + counts.value("restricted").toInt()));
        else if (state == QStringLiteral("interrupted"))
          _migrationStatus->setText(QStringLiteral("Migração interrompida. Confira o relatório e execute novamente para concluir."));
        else if (state == QStringLiteral("error"))
          _migrationStatus->setText(_orgMigrationSnapshot.value("error").toString(QStringLiteral("Migração interrompida. Tente novamente.")));
        if (wasRunning) {
          _accountChecks.clear();
          loadAccounts();
        }
      }
    });
}

void MainWindow::showOrgMigrationReport() {
  QDialog dialog(this);
  dialog.setWindowTitle(QStringLiteral("Migração das organizações"));
  dialog.resize(850, 440);
  auto* layout = new QVBoxLayout(&dialog);
  auto* table = new QTableWidget(0, 3, &dialog);
  configureTable(table);
  table->setHorizontalHeaderLabels({QStringLiteral("Conta"), QStringLiteral("Tipo"), QStringLiteral("Resultado")});
  table->horizontalHeader()->setSectionResizeMode(0, QHeaderView::Stretch);
  table->horizontalHeader()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
  table->horizontalHeader()->setSectionResizeMode(2, QHeaderView::Stretch);
  const auto report = _orgMigrationSnapshot;
  for (const auto value : report.value("results").toArray()) {
    const auto result = value.toObject();
    const int row = table->rowCount();
    table->insertRow(row);
    table->setItem(row, 0, cell(result.value("email").toString()));
    table->setItem(row, 1, cell(result.value("account_kind").toString() == "claru" ? QStringLiteral("Claru") : QStringLiteral("Crowtado")));
    auto* message = cell(result.value("message").toString());
    const auto issue = result.value("issue").toObject();
    message->setToolTip(issue.value("action").toString());
    table->setItem(row, 2, message);
  }
  layout->addWidget(table);
  auto* buttons = new QDialogButtonBox(QDialogButtonBox::Close, &dialog);
  auto* save = buttons->addButton(QStringLiteral("Salvar relatório"), QDialogButtonBox::ActionRole);
  connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
  connect(save, &QPushButton::clicked, &dialog, [this, report, &dialog] {
    QString path = QFileDialog::getSaveFileName(&dialog, QStringLiteral("Salvar relatório"),
        QStringLiteral("QMoney-migracao.json"), QStringLiteral("JSON (*.json)"));
    if (path.isEmpty()) return;
    if (!path.endsWith(".json", Qt::CaseInsensitive)) path += QStringLiteral(".json");
    QSaveFile output(path);
    const auto bytes = QJsonDocument(report).toJson(QJsonDocument::Indented);
    if (!output.open(QIODevice::WriteOnly) || output.write(bytes) != bytes.size() || !output.commit())
      showError(QStringLiteral("Relatório não salvo"), QStringLiteral("Confira o espaço e as permissões da pasta."));
  });
  layout->addWidget(buttons);
  dialog.exec();
}

void MainWindow::importAccounts() {
  const QString path = QFileDialog::getOpenFileName(this, QStringLiteral("Importar contas"),
      QString(), QStringLiteral("Contas JSON (*.json)"));
  if (path.isEmpty()) return;
  QFile file(path);
  if (!file.open(QIODevice::ReadOnly) || file.size() > 10 * 1024 * 1024) {
    QMessageBox::warning(this, QStringLiteral("Importação"),
        QStringLiteral("Não foi possível ler o arquivo. O limite é de 10 MB."));
    return;
  }
  QByteArray bytes = file.readAll();
  if (bytes.startsWith("\xEF\xBB\xBF")) bytes.remove(0, 3);
  const QString content = QString::fromUtf8(bytes);
  if (content.toUtf8() != bytes) {
    QMessageBox::warning(this, QStringLiteral("Importação"), QStringLiteral("Salve o JSON usando UTF-8."));
    return;
  }
  setAccountTransferBusy(true);
  setStatus(QStringLiteral("Validando arquivo de contas…"));
  _api.post(QStringLiteral("/api/accounts/import"), {{"content", content}, {"apply", false}},
    [this, content](bool ok, const QJsonDocument& doc, const QString& error) {
      if (!ok) {
        setAccountTransferBusy(false);
        QMessageBox::warning(this, QStringLiteral("Importação"), error);
        return;
      }
      const auto counts = doc.object().value("counts").toObject();
      QStringList details;
      for (const auto value : doc.object().value("results").toArray()) {
        const auto row = value.toObject();
        details << QStringLiteral("Linha %1 · %2 · %3").arg(row.value("row").toInt())
            .arg(row.value("email").toString(), row.value("message").toString());
      }
      const int fresh = counts.value("new").toInt();
      QMessageBox review(QMessageBox::Information, QStringLiteral("Revisar importação"),
          QStringLiteral("%1 novas · %2 duplicadas · %3 inválidas\n\n"
                         "Contas existentes serão preservadas. Somente as novas e válidas serão importadas. "
                         "A validade dos acessos será conferida em Verificar todas.")
              .arg(fresh).arg(counts.value("duplicate").toInt()).arg(counts.value("invalid").toInt()),
          fresh > 0 ? QMessageBox::Ok | QMessageBox::Cancel : QMessageBox::Close, this);
      review.setDetailedText(details.join('\n'));
      if (fresh > 0) review.button(QMessageBox::Ok)->setText(QStringLiteral("Importar %1 conta(s)").arg(fresh));
      if (review.exec() != QMessageBox::Ok || fresh == 0) {
        setAccountTransferBusy(false);
        return;
      }
      setStatus(QStringLiteral("Importando contas… Aguarde a conclusão antes de fechar o aplicativo."));
      _api.post(QStringLiteral("/api/accounts/import"), {{"content", content}, {"apply", true}},
        [this](bool saved, const QJsonDocument& result, const QString& failure) {
          setAccountTransferBusy(false);
          loadAccounts();
          if (!saved) {
            QMessageBox::warning(this, QStringLiteral("Importação"),
                failure + QStringLiteral("\nAtualize a lista antes de tentar novamente; contas já importadas serão ignoradas."));
            return;
          }
          const auto count = result.object().value("counts").toObject();
          QStringList details;
          for (const auto value : result.object().value("results").toArray()) {
            const auto row = value.toObject();
            details << QStringLiteral("Linha %1 · %2 · %3").arg(row.value("row").toInt())
                .arg(row.value("email").toString(), row.value("message").toString());
          }
          const QString summary = QStringLiteral("%1 importadas · %2 duplicadas preservadas · %3 inválidas · %4 falhas")
              .arg(count.value("imported").toInt()).arg(count.value("duplicate").toInt())
              .arg(count.value("invalid").toInt()).arg(count.value("error").toInt());
          QMessageBox report(QMessageBox::Information, QStringLiteral("Resultado da importação"),
              summary + QStringLiteral("\n\nUse Verificar todas para conferir os acessos importados."), QMessageBox::Ok, this);
          report.setDetailedText(details.join('\n'));
          setStatus(summary);
          report.exec();
        });
    });
}

void MainWindow::exportAccounts(bool selectedOnly) {
  QJsonObject body;
  if (selectedOnly) {
    QJsonArray emails;
    for (const auto& index : _accountsTable->selectionModel()->selectedRows())
      emails.append(_accountsTable->item(index.row(), 0)->text());
    if (emails.isEmpty()) {
      QMessageBox::information(this, QStringLiteral("Exportação"), QStringLiteral("Selecione uma ou mais contas na tabela."));
      return;
    }
    body.insert("emails", emails);
  }
  QString path = QFileDialog::getSaveFileName(this,
      QStringLiteral("Salvar backup de contas — contém credenciais de acesso"),
      QStringLiteral("QMoney-contas-%1.json").arg(QDateTime::currentDateTime().toString("yyyyMMdd-HHmmss")),
      QStringLiteral("Contas JSON (*.json)"));
  if (path.isEmpty()) return;
  if (!path.endsWith(".json", Qt::CaseInsensitive)) path += QStringLiteral(".json");
  setAccountTransferBusy(true);
  _api.post(QStringLiteral("/api/accounts/export"), body,
    [this, path](bool ok, const QJsonDocument& doc, const QString& error) {
      setAccountTransferBusy(false);
      if (!ok) { QMessageBox::warning(this, QStringLiteral("Exportação"), error); return; }
      QSaveFile output(path);
      const auto bytes = doc.toJson(QJsonDocument::Indented);
      if (!output.open(QIODevice::WriteOnly) || output.write(bytes) != bytes.size() || !output.commit()) {
        QMessageBox::warning(this, QStringLiteral("Exportação"),
            QStringLiteral("Não foi possível salvar o backup completo. Confira as permissões e o espaço disponível."));
        return;
      }
      QFile::setPermissions(path, QFileDevice::ReadOwner | QFileDevice::WriteOwner);
      const int count = doc.object().value("accounts").toArray().size();
      setStatus(QStringLiteral("%1 conta(s) exportada(s).").arg(count));
      QMessageBox::information(this, QStringLiteral("Backup salvo"),
          QStringLiteral("%1 conta(s) exportada(s) para:\n%2\n\nO JSON contém credenciais de acesso. Guarde em local seguro.")
              .arg(count).arg(QDir::toNativeSeparators(path)));
    });
}

void MainWindow::refreshAccountsPage() {
  loadAccounts();
  if (!_bulkRegisterPolling) loadBulkRegisterDomains();
}

void MainWindow::loadAccounts() {
  loadClaruAccounts();
  pollOrgMigration();
  _api.get(QStringLiteral("/api/accounts"), [this](bool ok, const QJsonDocument& doc, const QString& error) {
    if (!ok) {
      setAccountTransferBusy(_accountTransferBusy);
      return showError(QStringLiteral("Falha ao carregar contas"), error);
    }
    const auto accounts = doc.object().value(QStringLiteral("accounts")).toArray();
    _accountsTable->setRowCount(accounts.size());
    int row = 0;
    for (const auto value : accounts) {
      const auto account = value.toObject();
      const QString email = account.value(QStringLiteral("email")).toString();
      _accountsTable->setItem(row, 0, cell(email));
      const bool isClaru = account.value(QStringLiteral("account_kind")).toString() == QStringLiteral("claru");
      const QString org = account.value(QStringLiteral("org_key")).toString();
      const QString kind = isClaru ? QStringLiteral("Claru") : QStringLiteral("Crowtado");
      auto* orgCell = cell(kind + QStringLiteral(" · ") + account.value("org_name").toString(QStringLiteral("Não verificada")));
      orgCell->setToolTip(kind + QStringLiteral("\n") + org);
      _accountsTable->setItem(row, 1, orgCell);
      const qint64 expiry = static_cast<qint64>(account.value(QStringLiteral("expires_at")).toDouble());
      const auto lastCheck = _accountChecks.contains(email)
          ? _accountChecks.value(email) : account.value(QStringLiteral("last_check")).toObject();
      QString state = expiry > QDateTime::currentSecsSinceEpoch()
          ? QStringLiteral("Token no prazo · não verificada")
          : QStringLiteral("Acesso precisa ser verificado");
      if (!lastCheck.isEmpty()) {
        state = lastCheck.value(QStringLiteral("status_label")).toString(
                    lastCheck.value(QStringLiteral("status")).toString() == QStringLiteral("active")
                        ? QStringLiteral("Acesso verificado") : QStringLiteral("Verificação inconclusiva"))
                + QStringLiteral(" · ") + friendlyDate(lastCheck.value(QStringLiteral("checked_at")).toString());
      }
      auto* statusCell = cell(state);
      QString checkDetails = state + QStringLiteral("\n") + lastCheck.value(QStringLiteral("error")).toString();
      if (!lastCheck.value("last_success_at").toString().isEmpty())
        checkDetails += QStringLiteral("\nÚltimo acesso confirmado: ") + friendlyDate(lastCheck.value("last_success_at").toString());
      statusCell->setToolTip(checkDetails);
      _accountsTable->setItem(row, 2, statusCell);
      auto* actions = new QWidget;
      auto* actionsLayout = new QHBoxLayout(actions);
      actionsLayout->setContentsMargins(5, 5, 5, 5);
      actionsLayout->setSpacing(7);
      auto* check = new QPushButton(QStringLiteral("Verificar"));
      check->setMinimumSize(86, 32);
      connect(check, &QPushButton::clicked, this, [this, email] {
        _api.post(QStringLiteral("/api/accounts/") + encoded(email) + QStringLiteral("/check"), {},
          [this, email](bool ok, const QJsonDocument& doc, const QString& error) {
            auto check = doc.object();
            if (!check.contains(QStringLiteral("status"))) {
              check.insert(QStringLiteral("status"), QStringLiteral("inconclusive"));
              check.insert(QStringLiteral("status_label"), QStringLiteral("Verificação inconclusiva"));
              check.insert(QStringLiteral("error"), error);
            }
            check.insert(QStringLiteral("checked_at"), QDateTime::currentDateTime().toString(Qt::ISODate));
            _accountChecks.insert(email, check);
            if (!ok) {
              const auto issue = check.value(QStringLiteral("issue")).toObject();
              showAccountIssues(QStringLiteral("Conta não verificada"),
                  issue.isEmpty() ? QStringList{email + QStringLiteral(": ") + error} : QStringList{},
                  issue.isEmpty() ? QJsonArray{} : QJsonArray{issue});
            } else setStatus(QStringLiteral("Acesso de %1 verificado agora.").arg(email));
            loadAccounts();
          });
      });
      auto* remove = new QPushButton(QStringLiteral("Remover"));
      remove->setMinimumSize(86, 32);
      connect(remove, &QPushButton::clicked, this, [this, email] {
        removeAccount(email);
      });
      actionsLayout->addWidget(check);
      actionsLayout->addWidget(remove);
      _accountsTable->setCellWidget(row, 3, actions);
      ++row;
    }
    setAccountTransferBusy(_accountTransferBusy);
    if (_accountsCheckAll) _accountsCheckAll->setEnabled(!accounts.isEmpty() && !_accountTransferBusy && !_orgMigrationRunning);
    setStatus(QStringLiteral("%1 conta(s) cadastrada(s).").arg(accounts.size()));
  });
}

void MainWindow::removeAccount(const QString& email, std::function<void()> onRemoved) {
  if (QMessageBox::question(this, QStringLiteral("Remover conta"),
        QStringLiteral("Remover definitivamente %1 deste QMoney?\n\n"
                       "Falhas de verificação, saldo ou envio não comprovam que a conta está inválida. "
                       "Para corrigir o acesso, verifique novamente ou conecte a mesma conta com a senha.\n\n"
                       "O acesso salvo será apagado e não voltará ao reiniciar. "
                       "O histórico de campanhas será preservado.").arg(email),
        QMessageBox::Yes | QMessageBox::No, QMessageBox::No)
      != QMessageBox::Yes) return;
  _api.remove(QStringLiteral("/api/accounts/") + encoded(email),
              [this, email, onRemoved = std::move(onRemoved)](
                  bool ok, const QJsonDocument&, const QString& error) mutable {
    if (!ok) return showError(QStringLiteral("Conta não removida"), error);
    _accountChecks.remove(email);
    setStatus(QStringLiteral("%1 removida definitivamente deste QMoney.").arg(email));
    loadAccounts();
    if (onRemoved) onRemoved();
  });
}

void MainWindow::checkAllAccounts() {
  _accountsCheckAll->setEnabled(false);
  _accountsCheckAll->setText(QStringLiteral("Verificando…"));
  setStatus(QStringLiteral("Verificando todas as contas em paralelo…"));
  _api.post(QStringLiteral("/api/accounts/check-all"), {},
            [this](bool ok, const QJsonDocument& doc, const QString& error) {
    _accountsCheckAll->setText(QStringLiteral("Verificar todas"));
    if (!ok) {
      _accountsCheckAll->setEnabled(true);
      return showError(QStringLiteral("Verificação não concluída"), error);
    }
    const auto result = doc.object();
    const int total = result.value(QStringLiteral("total")).toInt();
    const int active = result.value(QStringLiteral("active")).toInt();
    const int disabled = result.value(QStringLiteral("disabled")).toArray().size();
    const int errors = result.value(QStringLiteral("errors")).toArray().size();
    QJsonArray issues;
    QStringList legacyErrors;
    for (const auto& value : result.value(QStringLiteral("results")).toArray()) {
      auto check = value.toObject();
      const QString email = check.value(QStringLiteral("email")).toString();
      check.insert(QStringLiteral("checked_at"), QDateTime::currentDateTime().toString(Qt::ISODate));
      _accountChecks.insert(email, check);
      if (check.value(QStringLiteral("status")).toString() != QStringLiteral("active")) {
        const auto issue = check.value(QStringLiteral("issue")).toObject();
        if (!issue.isEmpty()) issues.append(issue);
        else legacyErrors << email + QStringLiteral(": ") + check.value(QStringLiteral("error")).toString();
      }
    }
    setStatus(QStringLiteral("Verificação: %1 acessos confirmados · %2 restrições confirmadas · %3 pendências · %4 no total. Não remova contas por falhas temporárias.")
                  .arg(active).arg(disabled).arg(errors).arg(total));
    loadAccounts();
    if (!issues.isEmpty() || !legacyErrors.isEmpty())
      showAccountIssues(QStringLiteral("Verificação concluída com pendências"), legacyErrors, issues);
  });
}

void MainWindow::addAccount(bool registerNew) {
  if (!_backendReady || _bulkRegisterPolling || _bulkRegisterStarting || _accountTransferBusy
      || _bulkRegisterOutcomeUnknown
      || (registerNew && !_registrationProxiesReady)) return;
  const QString email = _accountEmail->text().trimmed();
  const QString password = _accountPassword->text();
  if (email.isEmpty() || password.isEmpty()) {
    return showError(QStringLiteral("Dados incompletos"), QStringLiteral("Informe email e senha."));
  }
  _accountAdd->setEnabled(false);
  _accountRegister->setEnabled(false);
  QJsonObject body{{QStringLiteral("email"), email}, {QStringLiteral("password"), password}};
  if (registerNew) {
    const int generation = _operationBackendGeneration;
    QJsonArray profiles;
    if (!email.endsWith(QStringLiteral("@supply.claru.ai"), Qt::CaseInsensitive)
        && !collectRegistrationProfiles(this, 1, profiles)) { setAccountTransferBusy(_accountTransferBusy); return; }
    if (!_backendReady || generation != _operationBackendGeneration || _bulkRegisterPolling
        || _bulkRegisterStarting || _accountTransferBusy || _bulkRegisterOutcomeUnknown) {
      setAccountTransferBusy(_accountTransferBusy); return;
    }
    const QJsonObject profile = profiles.isEmpty() ? QJsonObject{} : profiles.first().toObject();
    for (auto it = profile.begin(); it != profile.end(); ++it) body.insert(it.key(), it.value());
    body.insert(QStringLiteral("proxy_id"), _accountProxy->currentData().toString());
    _bulkRegisterPendingRequestId = QUuid::createUuid().toString(QUuid::WithoutBraces);
    body.insert(QStringLiteral("request_id"), _bulkRegisterPendingRequestId);
    _bulkRegisterPendingPath = QStringLiteral("/api/accounts/register?async=1");
    _bulkRegisterPendingBody = body;
    _bulkRegisterPendingManualRegistration = true;
    _bulkRegisterPendingClaruPassword = false;
    _bulkRegisterPendingClaruBatch = false;
    _bulkRegisterPendingClearResults = true;
    _bulkRegisterPendingTotal = 1;
    _bulkRegisterOwnRequestAccepted = false;
    _bulkRegisterOutcomeUnknown = false;
    _bulkRegisterRetryInFlight = false;
    _bulkRegisterBaselineReady = false;
    _bulkRegisterBaselineInvalid = false;
    _bulkRegisterStarting = true;
    setAccountTransferBusy(_accountTransferBusy);
    setStatus(QStringLiteral("Registrando %1 — verificando o provedor e o Minute… pode levar alguns minutos.")
                  .arg(email));
    submitPendingRegistrationRequest();
    return;
  }
  _api.post(QStringLiteral("/api/accounts"), body,
            [this, email](bool ok, const QJsonDocument&, const QString& error) {
    _accountAdd->setEnabled(true);
    _accountRegister->setEnabled(true);
    if (!ok) return showError(QStringLiteral("Conta não conectada"), error);
    _accountChecks.remove(email);
    _accountEmail->clear();
    _accountPassword->clear();
    setStatus(QStringLiteral("Conta conectada."));
    loadAccounts();
  });
}

void MainWindow::loadClaruAccounts() {
  if (!_backendReady || _accountTransferBusy || _bulkRegisterPolling || _bulkRegisterStarting) return;
  _api.get(QStringLiteral("/api/accounts/claru"), [this](bool ok, const QJsonDocument& doc, const QString& error) {
    if (_accountTransferBusy || _bulkRegisterPolling || _bulkRegisterStarting) return;
    const auto rows = doc.object().value("accounts");
    if (!ok || !rows.isArray()) {
      _claruStatus->setText(QStringLiteral("Não foi possível carregar as conexões Claru. ") + error);
      return;
    }
    _claruPending = doc.object().value("pending").toArray();
    if (_claruBatchAccounts.isEmpty()) renderClaruBatch({});
    const QString selected = _claruEmail->text().trimmed();
    const QSignalBlocker block(_claruConnections);
    _claruConnections->clear();
    _claruConnections->addItem(QStringLiteral("Nova conexão Claru"), QJsonObject{});
    int selection = 0;
    for (const auto value : rows.toArray()) {
      const auto row = value.toObject();
      const QString login = row.value("login_email").toString();
      const QString minute = row.value("minute_email").toString();
      if (login.isEmpty() || !minute.endsWith(QStringLiteral("@supply.claru.ai"))) continue;
      _claruConnections->addItem(login + QStringLiteral(" → ") + minute, row);
      if (login.compare(selected, Qt::CaseInsensitive) == 0) selection = _claruConnections->count() - 1;
    }
    _claruConnections->setCurrentIndex(selection);
    setAccountTransferBusy(_accountTransferBusy);
  });
}

void MainWindow::importClaruBatchFile(const QString& path) {
  if (_accountTransferBusy || _bulkRegisterPolling || _bulkRegisterStarting) return;
  QFile file(path);
  if (!file.open(QIODevice::ReadOnly) || file.size() > 1024 * 1024)
    return showError(QStringLiteral("Lote inválido"), QStringLiteral("Escolha um JSON de até 1 MB."));
  QJsonParseError parse;
  const auto doc = QJsonDocument::fromJson(file.readAll(), &parse);
  const auto entries = doc.isArray() ? doc.array() : doc.object().value("accounts").toArray();
  if (parse.error != QJsonParseError::NoError || entries.isEmpty() || entries.size() > 50)
    return showError(QStringLiteral("Lote inválido"), QStringLiteral("Informe uma lista de 1 a 50 objetos com login_email, minute_password e claru_password opcional."));
  QJsonArray normalized;
  QSet<QString> seen;
  for (const auto value : entries) {
    auto entry = value.toObject();
    const auto email = entry.value("login_email").toString().trimmed().toLower();
    bool extra = false;
    for (const auto& key : entry.keys())
      extra |= key != "login_email" && key != "minute_password" && key != "claru_password";
    if (extra || !email.contains('@') || email.endsWith(QStringLiteral("@supply.claru.ai")) || seen.contains(email)
        || entry.value("minute_password").toString().size() < 8
        || entry.value("minute_password").toString().size() > 4096
        || (entry.contains("claru_password") && (!entry.value("claru_password").isString() || entry.value("claru_password").toString().isEmpty())))
      return showError(QStringLiteral("Lote inválido"), QStringLiteral("Confira os campos, as senhas e os logins repetidos. A lista anterior foi preservada."));
    entry.insert("login_email", email); seen.insert(email); normalized.append(entry);
  }
  _claruBatchAccounts = normalized;
  renderClaruBatch({});
  setAccountTransferBusy(_accountTransferBusy);
  _claruStatus->setText(QStringLiteral("%1 conta(s) preparadas para cadastro no Minute.").arg(normalized.size()));
}

void MainWindow::startClaruBatch(bool resume) {
  if (!_backendReady || _accountTransferBusy || _bulkRegisterPolling || _bulkRegisterStarting
      || _bulkRegisterOutcomeUnknown || !_registrationProxiesReady) return;
  QJsonArray entries = _claruBatchAccounts;
  if (resume) {
    entries = {};
    for (const auto value : _claruPending)
      entries.append(QJsonObject{{"login_email", value.toObject().value("login_email")}});
  }
  if (entries.isEmpty()) return;
  _bulkRegisterPreserveClaruQueue = resume && !_claruBatchAccounts.isEmpty();
  _bulkRegisterPendingConsumesClaruQueue = !resume;
  _claruBatchRequestId = QUuid::createUuid().toString(QUuid::WithoutBraces);
  _claruBatchRequestError.clear();
  _bulkRegisterPendingRequestId = _claruBatchRequestId;
  _bulkRegisterPendingPath = QStringLiteral("/api/accounts/bulk-register");
  _bulkRegisterPendingBody = {{QStringLiteral("provider"), QStringLiteral("claru")},
      {QStringLiteral("accounts"), entries}, {QStringLiteral("proxy_id"), _claruProxy->currentData().toString()},
      {QStringLiteral("request_id"), _claruBatchRequestId}};
  _bulkRegisterPendingManualRegistration = false;
  _bulkRegisterPendingClaruPassword = false;
  _bulkRegisterPendingClaruBatch = true;
  _bulkRegisterPendingClearResults = false;
  _bulkRegisterPendingTotal = entries.size();
  _bulkRegisterOwnRequestAccepted = false;
  _bulkRegisterOutcomeUnknown = false;
  _bulkRegisterRetryInFlight = false;
  _bulkRegisterBaselineReady = false;
  _bulkRegisterBaselineInvalid = false;
  _bulkRegisterStarting = true;
  setAccountTransferBusy(_accountTransferBusy);
  _claruStatus->setText(QStringLiteral("Iniciando cadastro Minute para %1 conta(s) Claru…").arg(entries.size()));
  submitPendingRegistrationRequest();
}

void MainWindow::renderClaruBatch(const QJsonObject& root) {
  if (!root.isEmpty()) _claruBatchSnapshot = root;
  QJsonArray entries = root.value("results").toArray();
  const bool queued = !_claruBatchAccounts.isEmpty();
  if (root.isEmpty()) {
    entries = queued ? _claruBatchAccounts : _claruBatchSnapshot.value("results").toArray();
    if (entries.isEmpty()) entries = _claruPending;
  } else if (queued) {
    QSet<QString> renderedLogins;
    for (const auto& value : entries) {
      const auto row = value.toObject();
      const QString login = row.value("claru_login").toString(row.value("login_email").toString())
                                .trimmed().toCaseFolded();
      if (!login.isEmpty()) renderedLogins.insert(login);
    }
    for (const auto& value : _claruBatchAccounts) {
      const QString login = value.toObject().value("login_email").toString().trimmed().toCaseFolded();
      if (!login.isEmpty() && !renderedLogins.contains(login)) entries.append(value);
    }
  }
  _claruBatchTable->setRowCount(entries.size());
  for (int i = 0; i < entries.size(); ++i) {
    const auto row = entries.at(i).toObject();
    const auto login = row.value("claru_login").toString(row.value("login_email").toString());
    const auto minute = row.value("minute_email").toString();
    QString result = row.value("created").toBool() ? QStringLiteral("Minute cadastrado e validado") : row.value("error").toString();
    if (result.isEmpty()) result = queued ? QStringLiteral("Preparada") : QStringLiteral("Pendente · retomar com o cofre salvo");
    _claruBatchTable->setItem(i, 0, cell(login));
    _claruBatchTable->setItem(i, 1, cell(minute.isEmpty() ? QStringLiteral("Consultar perfil Claru") : minute));
    _claruBatchTable->setItem(i, 2, cell(result));
  }
  _claruBatchStop->setEnabled(root.value("state").toString() == QStringLiteral("running"));
}

void MainWindow::connectClaruAccount() {
  if (!_backendReady || _accountTransferBusy || _bulkRegisterPolling || _bulkRegisterStarting) return;
  const QString email = _claruEmail->text().trimmed();
  const QString password = _claruPassword->text();
  if (email.isEmpty()) return showError(QStringLiteral("Login Claru incompleto"), QStringLiteral("Informe o e-mail usado para entrar no Claru."));
  QJsonObject body{{"email", email}};
  if (!password.isEmpty()) body.insert("password", password);
  setAccountTransferBusy(true);
  _claruMinuteEmail->clear();
  _claruStatus->setText(QStringLiteral("Consultando o perfil e a caixa Claru…"));
  const int generation = _operationBackendGeneration;
  const QString operationId = _claruConnectionOperationId =
      QUuid::createUuid().toString(QUuid::WithoutBraces);
  _api.post(QStringLiteral("/api/accounts/claru/connect"), body,
      [this, generation, operationId, email, password](bool ok, const QJsonDocument& doc, const QString& error) {
    if (!_backendReady || generation != _operationBackendGeneration
        || operationId != _claruConnectionOperationId) return;
    const auto row = doc.object().value("account").toObject();
    const QString minute = row.value("minute_email").toString();
    const bool confirmed = ok && row.value("login_email").toString().compare(email, Qt::CaseInsensitive) == 0
        && minute.endsWith(QStringLiteral("@supply.claru.ai"));
    if (confirmed) {
      if (_claruEmail->text().trimmed().compare(email, Qt::CaseInsensitive) == 0
          && _claruPassword->text() == password) _claruPassword->clear();
      _claruMinuteEmail->setText(minute);
      _claruStatus->setText(QStringLiteral("E-mail gerado confirmado. Informe a senha do Minute para conectar ou registrar na organização Claru (WSNEHSKC)."));
    } else {
      _claruStatus->setText(QStringLiteral("Conexão Claru não confirmada. ") + error);
    }
    setAccountTransferBusy(false);
    if (confirmed) loadClaruAccounts();
  });
}

void MainWindow::connectClaruMinute(bool registerNew) {
  if (!_backendReady || _accountTransferBusy || _bulkRegisterPolling || _bulkRegisterStarting
      || _bulkRegisterOutcomeUnknown || (registerNew && !_registrationProxiesReady)) return;
  const QString email = _claruMinuteEmail->text();
  const QString password = _claruMinutePassword->text();
  if (!email.endsWith(QStringLiteral("@supply.claru.ai")) || password.isEmpty())
    return showError(QStringLiteral("Minute incompleto"), QStringLiteral("Consulte o perfil Claru e informe a senha própria do Minute."));
  QJsonObject body{{"email", email}, {"password", password}};
  if (registerNew) {
    body.insert("proxy_id", _claruProxy->currentData().toString());
    _bulkRegisterPendingRequestId = QUuid::createUuid().toString(QUuid::WithoutBraces);
    body.insert("request_id", _bulkRegisterPendingRequestId);
    _bulkRegisterPendingPath = QStringLiteral("/api/accounts/register?async=1");
    _bulkRegisterPendingBody = body;
    _bulkRegisterPendingManualRegistration = true;
    _bulkRegisterPendingClaruPassword = true;
    _bulkRegisterPendingClaruBatch = false;
    _bulkRegisterPendingClearResults = true;
    _bulkRegisterPendingTotal = 1;
    _bulkRegisterOwnRequestAccepted = false;
    _bulkRegisterOutcomeUnknown = false;
    _bulkRegisterRetryInFlight = false;
    _bulkRegisterBaselineReady = false;
    _bulkRegisterBaselineInvalid = false;
    _bulkRegisterStarting = true;
    setAccountTransferBusy(_accountTransferBusy);
    _claruStatus->setText(QStringLiteral("Registrando Minute com o e-mail gerado e a organização Claru…"));
    submitPendingRegistrationRequest();
    return;
  }
  setAccountTransferBusy(true);
  _claruStatus->setText(QStringLiteral("Conectando o Minute existente e verificando a organização Claru…"));
  const int generation = _operationBackendGeneration;
  const QString operationId = _claruConnectionOperationId =
      QUuid::createUuid().toString(QUuid::WithoutBraces);
  _api.post(QStringLiteral("/api/accounts"), body,
      [this, generation, operationId, email, password](bool ok, const QJsonDocument&, const QString& error) {
    if (!_backendReady || generation != _operationBackendGeneration
        || operationId != _claruConnectionOperationId) return;
    setAccountTransferBusy(false);
    if (ok && _claruMinuteEmail->text().trimmed().compare(email, Qt::CaseInsensitive) == 0
        && _claruMinutePassword->text() == password) _claruMinutePassword->clear();
    _claruStatus->setText(ok ? QStringLiteral("Minute conectado e organização Claru confirmada.")
                            : QStringLiteral("Minute não confirmado. ") + error);
    if (ok) loadAccounts();
  });
}

void MainWindow::loadRegistrationProxies(bool preserveStatus) {
  if (!_backendReady || _bulkRegisterPolling || _bulkRegisterStarting || _accountTransferBusy) return;
  const int revision = ++_registrationProxiesRevision;
  const int generation = _operationBackendGeneration;
  _registrationProxiesReady = false;
  _bulkRegisterPreflightReady = false;
  setAccountTransferBusy(_accountTransferBusy);
  _api.get(QStringLiteral("/api/accounts/proxies"), [this, revision, generation, preserveStatus](bool ok, const QJsonDocument& doc, const QString& error) {
    if (!_backendReady || generation != _operationBackendGeneration
        || revision != _registrationProxiesRevision || _bulkRegisterPolling || _bulkRegisterStarting) return;
    const auto rows = doc.object().value(QStringLiteral("proxies"));
    _registrationProxiesReady = ok && rows.isArray();
    if (_registrationProxiesReady) {
      for (const auto value : rows.toArray()) {
        const auto row = value.toObject();
        if (!row.value("id").isString() || !row.value("label").isString()) _registrationProxiesReady = false;
      }
    }
    for (auto* combo : {_accountProxy, _bulkRegisterProxy, _claruProxy}) {
      const QSignalBlocker block(combo);
      const QString previous = combo->currentData().toString();
      combo->clear();
      if (_registrationProxiesReady) {
        combo->addItem(QStringLiteral("Sem proxy"), QString());
        for (const auto value : rows.toArray()) {
          const auto row = value.toObject();
          combo->addItem(row.value("label").toString(), row.value("id").toString());
        }
        combo->setCurrentIndex(qMax(0, combo->findData(previous)));
      } else combo->addItem(QStringLiteral("Proxies indisponíveis"), QString());
    }
    setAccountTransferBusy(_accountTransferBusy);
    if (!_registrationProxiesReady) {
      _bulkRegisterStart->setEnabled(false);
      const QString prefix = preserveStatus
          ? _bulkRegisterStatus->text().section('\n', 0, 0) + QStringLiteral("\n") : QString();
      _bulkRegisterStatus->setText(prefix + QStringLiteral("Não foi possível carregar os proxies. Atualize antes de criar contas. ") + error);
      return;
    }
    checkBulkRegisterDomain(preserveStatus);
  });
}

void MainWindow::importRegistrationProxiesFile(const QString& path) {
  if (!_backendReady || _bulkRegisterPolling || _bulkRegisterStarting || _accountTransferBusy) return;
  QFile file(path);
  if (!file.open(QIODevice::ReadOnly) || file.size() > 256 * 1024) {
    showError(QStringLiteral("Importação não concluída"), QStringLiteral("Selecione um TXT acessível de até 256 KB.")); return;
  }
  const auto bytes = file.readAll();
  if (!bytes.isValidUtf8()) {
    showError(QStringLiteral("Importação não concluída"), QStringLiteral("Salve o arquivo TXT em UTF-8.")); return;
  }
  ++_registrationProxiesRevision;
  setAccountTransferBusy(true);
  _bulkRegisterStart->setEnabled(false);
  const int generation = _operationBackendGeneration;
  _api.post(QStringLiteral("/api/accounts/proxies/import"), {{"text", QString::fromUtf8(bytes)}},
      [this, generation](bool ok, const QJsonDocument& doc, const QString& error) {
    if (!_backendReady || generation != _operationBackendGeneration) return;
    setAccountTransferBusy(false);
    if (!ok) { loadRegistrationProxies(); showError(QStringLiteral("Importação não concluída"), error); return; }
    setStatus(QStringLiteral("%1 proxies disponíveis. Credenciais protegidas no cofre local.").arg(doc.object().value("total").toInt()));
    loadRegistrationProxies();
  });
}

void MainWindow::loadBulkRegisterDomains(bool preserveStatus) {
  loadRegistrationProxies(preserveStatus);
  if (!_backendReady || _bulkRegisterPolling || _bulkRegisterStarting) return;
  const int revision = ++_bulkRegisterDomainsRevision;
  const int generation = _operationBackendGeneration;
  const QString statusPrefix = preserveStatus
      ? _bulkRegisterStatus->text().section('\n', 0, 0) + QStringLiteral("\n") : QString();
  const QString selectedDomain = _bulkRegisterDomain->currentData().toString();
  ++_bulkRegisterPreflightRevision;
  const QSignalBlocker blocker(_bulkRegisterDomain);
  _bulkRegisterStart->setEnabled(false);
  _bulkRegisterDomain->clear();
  _bulkRegisterDomain->addItem(QStringLiteral("carregando domínios…"));
  _bulkRegisterDomain->setEnabled(false);
  _api.get(QStringLiteral("/api/accounts/domains"), [this, revision, generation, selectedDomain, preserveStatus, statusPrefix](bool ok, const QJsonDocument& doc, const QString& error) {
    if (!_backendReady || generation != _operationBackendGeneration
        || revision != _bulkRegisterDomainsRevision || _bulkRegisterPolling || _bulkRegisterStarting) return;
    const QSignalBlocker blocker(_bulkRegisterDomain);
    _bulkRegisterDomain->clear();
    _bulkRegisterWebmail->hide();
    if (!ok) {
      _bulkRegisterDomain->addItem(QStringLiteral("não foi possível carregar"));
      _bulkRegisterDomain->setEnabled(false);
      _bulkRegisterStatus->setText(statusPrefix + QStringLiteral("Falha ao carregar domínios: ") + error);
      return;
    }
    const auto root = doc.object();
    const auto domains = root.value(QStringLiteral("domains")).toArray();
    const QString webmailUrl = root.value(QStringLiteral("webmail_url")).toString();
    if (domains.isEmpty()) {
      _bulkRegisterDomain->addItem(QStringLiteral("configure um domínio em Integrações"));
      _bulkRegisterDomain->setEnabled(false);
      _bulkRegisterStart->setEnabled(false);
      const QString warning = root.value(QStringLiteral("warning")).toString();
      _bulkRegisterStatus->setText(statusPrefix + (warning.isEmpty()
          ? QStringLiteral("Nenhum domínio disponível. Em Integrações, use Identificar e conectar com o token da API Mail da Hostinger; depois clique em Atualizar domínios.")
          : warning));
      return;
    }
    for (const auto value : domains) {
      const auto entry = value.toObject();
      const QString domain = entry.value(QStringLiteral("domain")).toString();
      const QString profile = entry.value(QStringLiteral("profile_name")).toString();
      _bulkRegisterDomain->addItem(profile.isEmpty() ? domain : QStringLiteral("%1 (%2)").arg(domain, profile), domain);
    }
    const int previousIndex = _bulkRegisterDomain->findData(selectedDomain);
    if (previousIndex >= 0) _bulkRegisterDomain->setCurrentIndex(previousIndex);
    _bulkRegisterDomain->setEnabled(true);
    if (!webmailUrl.isEmpty()) {
      _bulkRegisterWebmail->setText(
          QStringLiteral("Conferir caixa de entrada: <a href=\"%1\">%1</a>")
              .arg(webmailUrl));
      _bulkRegisterWebmail->show();
    }
    checkBulkRegisterDomain(preserveStatus);
  });
}

void MainWindow::checkBulkRegisterDomain(bool preserveStatus) {
    if (!_backendReady || _bulkRegisterPolling || _bulkRegisterStarting || _accountTransferBusy) return;
    const int revision = ++_bulkRegisterPreflightRevision;
    const int generation = _operationBackendGeneration;
    const QString domain = _bulkRegisterDomain->currentData().toString();
    const QString proxy = _bulkRegisterProxy->currentData().toString();
    _bulkRegisterPreflightReady = false;
    updateBulkRegisterStartEnabled();
    if (domain.isEmpty() || !_registrationProxiesReady) return;
    const QString statusPrefix = preserveStatus
        ? _bulkRegisterStatus->text().section('\n', 0, 0) + QStringLiteral("\n") : QString();
    if (!preserveStatus)
      _bulkRegisterStatus->setText(QStringLiteral("Validando dependências (Hostinger, Chrome, APIs)…"));
    _api.get(QStringLiteral("/api/accounts/bulk-register/preflight?domain=")
                 + QString::fromLatin1(QUrl::toPercentEncoding(domain)) + QStringLiteral("&proxy_id=")
                 + encoded(_bulkRegisterProxy->currentData().toString()),
             [this, revision, generation, domain, proxy, statusPrefix](bool ok, const QJsonDocument& doc, const QString& error) {
      if (!_backendReady || generation != _operationBackendGeneration
          || revision != _bulkRegisterPreflightRevision || _bulkRegisterPolling || _bulkRegisterStarting
          || domain != _bulkRegisterDomain->currentData().toString()
          || proxy != _bulkRegisterProxy->currentData().toString()) return;
      if (!ok) {
        _bulkRegisterStatus->setText(statusPrefix + QStringLiteral("Preflight falhou: ") + error);
        updateBulkRegisterStartEnabled();
        return;
      }
      const auto root = doc.object();
      const bool ready = root.value(QStringLiteral("ready")).toBool();
      const auto checks = root.value(QStringLiteral("checks")).toObject();
      QStringList issues;
      const QStringList checkKeys = {
          QStringLiteral("hostinger"), QStringLiteral("chrome"),
          QStringLiteral("crowtado_api"), QStringLiteral("minute_api"), QStringLiteral("invite_code"),
      };
      const QStringList checkNames = {
          QStringLiteral("Hostinger"), QStringLiteral("Chrome"),
          QStringLiteral("Crowtado API"), QStringLiteral("Minute API"), QStringLiteral("Código de convite"),
      };
      bool checksReady = true;
      for (int i = 0; i < checkKeys.size(); ++i) {
        const auto value = checks.value(checkKeys.at(i));
        const auto check = value.toObject();
        if (!value.isObject() || !check.value(QStringLiteral("ok")).isBool()
            || !check.value(QStringLiteral("ok")).toBool()) {
          checksReady = false;
          issues << QStringLiteral("%1: %2")
                        .arg(checkNames.at(i), check.value(QStringLiteral("detail")).toString());
        }
      }
      if (!proxy.isEmpty()) {
        const auto proxyValue = checks.value(QStringLiteral("proxy"));
        const auto proxyCheck = proxyValue.toObject();
        if (!proxyValue.isObject() || !proxyCheck.value(QStringLiteral("ok")).isBool()
            || !proxyCheck.value(QStringLiteral("ok")).toBool()) {
          checksReady = false;
          issues.prepend(QStringLiteral("Proxy: ") + proxyCheck.value(QStringLiteral("detail")).toString());
        }
      } else if (checks.contains(QStringLiteral("proxy"))
                 && !checks.value(QStringLiteral("proxy")).toObject().value(QStringLiteral("ok")).toBool()) {
        checksReady = false;
        issues.prepend(QStringLiteral("Proxy: ") + checks.value(QStringLiteral("proxy")).toObject().value(QStringLiteral("detail")).toString());
      }
      _bulkRegisterPreflightReady = ready && checksReady && _registrationProxiesReady;
      _bulkRegisterPreflightDomain = domain;
      _bulkRegisterPreflightProxy = proxy;
      if (_bulkRegisterPreflightReady) {
        _bulkRegisterStatus->setText(statusPrefix + QStringLiteral(
            "Pronto para criar Crowtado + Minute, preencher o perfil e confirmar o vínculo na tarefa."));
      } else {
        _bulkRegisterStatus->setText(statusPrefix + QStringLiteral("Dependências com problema: %1").arg(issues.join(QStringLiteral("; "))));
      }
      updateBulkRegisterStartEnabled();
    });
}

void MainWindow::startBulkRegister() {
  if (!_backendReady || _bulkRegisterPolling || _bulkRegisterStarting
      || _bulkRegisterOutcomeUnknown || _accountTransferBusy || !_registrationProxiesReady
      || !_bulkRegisterPreflightReady
      || _bulkRegisterPreflightDomain != _bulkRegisterDomain->currentData().toString()
      || _bulkRegisterPreflightProxy != _bulkRegisterProxy->currentData().toString()) return;
  const QString domain = _bulkRegisterDomain->currentData().toString();
  if (domain.isEmpty()) {
    return showError(QStringLiteral("Domínio indisponível"),
                     QStringLiteral("Configure um domínio catch-all em Integrações."));
  }
  const int count = _bulkRegisterCount->value();
  const int generation = _operationBackendGeneration;
  QJsonArray profiles;
  if (!collectRegistrationProfiles(this, count, profiles)) return;
  if (!_backendReady || generation != _operationBackendGeneration || _bulkRegisterPolling
      || _bulkRegisterStarting || _accountTransferBusy || _bulkRegisterOutcomeUnknown
      || !_bulkRegisterPreflightReady) return;
  _bulkRegisterStarting = true;
  setAccountTransferBusy(_accountTransferBusy);
  ++_bulkRegisterDomainsRevision;
  ++_bulkRegisterPreflightRevision;
  _bulkRegisterStart->setEnabled(false);
  _bulkRegisterDomain->setEnabled(false);
  _bulkRegisterCount->setEnabled(false);
  _bulkRegisterPendingRequestId = QUuid::createUuid().toString(QUuid::WithoutBraces);
  _bulkRegisterPendingPath = QStringLiteral("/api/accounts/bulk-register");
  _bulkRegisterPendingBody = {{QStringLiteral("count"), count}, {QStringLiteral("domain"), domain},
      {QStringLiteral("profiles"), profiles},
      {QStringLiteral("proxy_id"), _bulkRegisterProxy->currentData().toString()},
      {QStringLiteral("request_id"), _bulkRegisterPendingRequestId}};
  _bulkRegisterPendingManualRegistration = false;
  _bulkRegisterPendingClaruPassword = false;
  _bulkRegisterPendingClaruBatch = false;
  _bulkRegisterPendingClearResults = true;
  _bulkRegisterPendingTotal = count;
  _bulkRegisterOwnRequestAccepted = false;
  _bulkRegisterOutcomeUnknown = false;
  _bulkRegisterRetryInFlight = false;
  _bulkRegisterBaselineReady = false;
  _bulkRegisterBaselineInvalid = false;
  _bulkRegisterPreflightReady = false;
  _bulkRegisterStatus->setText(QStringLiteral("Iniciando criação de %1 contas…").arg(count));
  submitPendingRegistrationRequest();
}

void MainWindow::submitPendingRegistrationRequest() {
  if (!_backendReady || _bulkRegisterPendingRequestId.isEmpty()
      || _bulkRegisterPendingPath.isEmpty() || _bulkRegisterPendingBody.isEmpty()
      || _bulkRegisterRequestInFlight) return;
  if (!_bulkRegisterBaselineReady) {
    _bulkRegisterStarting = true;
    _bulkRegisterRequestInFlight = true;
    setAccountTransferBusy(_accountTransferBusy);
    const int generation = _operationBackendGeneration;
    const QString expectedId = _bulkRegisterPendingRequestId;
    _api.get(QStringLiteral("/api/accounts/bulk-register/status"),
        [this, generation, expectedId](bool ok, const QJsonDocument& doc, const QString& error) {
      if (!_backendReady || generation != _operationBackendGeneration
          || expectedId != _bulkRegisterPendingRequestId) return;
      _bulkRegisterRequestInFlight = false;
      _bulkRegisterStarting = false;
      const auto root = doc.object();
      const QString state = root.value(QStringLiteral("state")).toString();
      const QString requestId = root.value(QStringLiteral("request_id")).toString();
      const bool knownState = state == QStringLiteral("idle") || state == QStringLiteral("running")
          || state == QStringLiteral("stopping") || state == QStringLiteral("done")
          || state == QStringLiteral("failed");
      if (!ok || !knownState) {
        const QString detail = !ok ? error : QStringLiteral("o estado atual não pôde ser identificado");
        rejectPendingRegistrationRequest(QStringLiteral(
            "Não enviei o cadastro porque não consegui confirmar o estado anterior do serviço. Os dados digitados foram mantidos. ") + detail);
        return;
      }
      if (state == QStringLiteral("running") || state == QStringLiteral("stopping")) {
        const bool rejectedClaruAdmission = _bulkRegisterPendingClaruBatch;
        if (rejectedClaruAdmission) {
          _bulkRegisterFollowingForeignRequest = true;
          _bulkRegisterForeignRequestId = requestId;
          _bulkRegisterPreserveClaruQueue = true;
        }
        rejectPendingRegistrationRequest(QStringLiteral(
            "Já existe uma criação em andamento no serviço. Aguarde antes de iniciar outra; os dados digitados foram mantidos."));
        _bulkRegisterPolling = true;
        beginRegistrationPolling();
        return;
      }
      _bulkRegisterBaselineState = state;
      _bulkRegisterBaselineRequestId = requestId;
      _bulkRegisterBaselineReady = true;
      _bulkRegisterBaselineInvalid = false;
      setAccountTransferBusy(_accountTransferBusy);
      submitPendingRegistrationRequest();
    });
    return;
  }

  const bool retry = _bulkRegisterRetryInFlight || _bulkRegisterOutcomeUnknown;
  _bulkRegisterRetryInFlight = retry;
  _bulkRegisterStarting = true;
  _bulkRegisterOutcomeUnknown = false;
  setAccountTransferBusy(_accountTransferBusy);
  const int generation = _operationBackendGeneration;
  const QString expectedId = _bulkRegisterPendingRequestId;
  const QJsonObject body = _bulkRegisterPendingBody;
  const QString path = _bulkRegisterPendingPath;
  _api.post(path, body, [this, generation, expectedId, retry](bool ok, const QJsonDocument& doc, const QString& error) {
    if (!_backendReady || generation != _operationBackendGeneration
        || expectedId != _bulkRegisterPendingRequestId) return;
    _bulkRegisterStarting = false;
    _bulkRegisterRetryInFlight = false;
    const auto response = doc.object();
    const QString responseId = response.value(QStringLiteral("request_id")).toString();
    if (ok && response.value(QStringLiteral("ok")).toBool() && responseId == expectedId) {
      acceptPendingRegistrationRequest();
      return;
    }
    const bool unknown = response.value(QStringLiteral("error_code")).toString()
        == QStringLiteral("request_outcome_unknown");
    if (response.value(QStringLiteral("not_admitted")).toBool()) {
      rejectPendingRegistrationRequest(error.isEmpty()
          ? response.value(QStringLiteral("error")).toString(QStringLiteral("O serviço confirmou que não iniciou o cadastro."))
          : error);
      return;
    }
    if (unknown || retry) {
      _bulkRegisterOutcomeUnknown = true;
      setAccountTransferBusy(_accountTransferBusy);
      _bulkRegisterStatus->setText(unknown
          ? QStringLiteral("A resposta do cadastro não chegou. Mantive a senha e vou confirmar a solicitação antes de continuar.")
          : QStringLiteral("A solicitação ainda não foi confirmada. Mantive a senha e consultarei o serviço antes de reenviar o mesmo identificador."));
      setStatus(_bulkRegisterStatus->text());
      if (!_bulkRegisterPolling) beginRegistrationPolling();
      else _bulkRegisterPoll.setInterval(3000);
      return;
    }
    rejectPendingRegistrationRequest(error.isEmpty()
        ? response.value(QStringLiteral("error")).toString(QStringLiteral("O serviço recusou a solicitação."))
        : error);
  });
}

void MainWindow::acceptPendingRegistrationRequest() {
  if (_bulkRegisterPendingRequestId.isEmpty()) return;
  _bulkRegisterOwnRequestAccepted = true;
  _bulkRegisterAcceptedRequestId = _bulkRegisterPendingRequestId;
  _bulkRegisterOutcomeUnknown = false;
  _bulkRegisterRetryInFlight = false;
  _bulkRegisterStarting = false;
  _bulkRegisterStatus->setText(QStringLiteral(
      "O serviço aceitou a solicitação. As credenciais ficam preservadas até o progresso confirmar o checkpoint."));
  setStatus(_bulkRegisterStatus->text());
  setAccountTransferBusy(_accountTransferBusy);
  if (!_bulkRegisterPolling) beginRegistrationPolling();
}

void MainWindow::consumeRegistrationCheckpoints(const QJsonArray& results) {
  QSet<QString> savedIdentities;
  for (const auto& value : results) {
    const auto item = value.toObject();
    const auto savePartial = item.value(QStringLiteral("steps")).toObject()
                                 .value(QStringLiteral("save_partial")).toObject();
    const QString status = savePartial.value(QStringLiteral("status")).toString();
    if (status != QStringLiteral("ok")) continue;
    for (const auto& key : {QStringLiteral("email"), QStringLiteral("claru_login"),
                            QStringLiteral("minute_email"), QStringLiteral("login_email")}) {
      const QString identity = item.value(key).toString().trimmed().toCaseFolded();
      if (!identity.isEmpty()) savedIdentities.insert(identity);
    }
  }
  if (savedIdentities.isEmpty()) return;

  if (_bulkRegisterPendingManualRegistration) {
    const QString email = _bulkRegisterPendingBody.value(QStringLiteral("email")).toString()
                              .trimmed().toCaseFolded();
    if (savedIdentities.contains(email)) {
      const QString password = _bulkRegisterPendingBody.value(QStringLiteral("password")).toString();
      if (_bulkRegisterPendingClaruPassword) {
        if (_claruMinuteEmail->text().trimmed().compare(
                _bulkRegisterPendingBody.value(QStringLiteral("email")).toString(), Qt::CaseInsensitive) == 0
            && _claruMinutePassword->text() == password) _claruMinutePassword->clear();
      } else if (_accountEmail->text().trimmed().compare(
                     _bulkRegisterPendingBody.value(QStringLiteral("email")).toString(), Qt::CaseInsensitive) == 0
                 && _accountPassword->text() == password) {
        _accountPassword->clear();
      }
      _bulkRegisterPendingBody.remove(QStringLiteral("password"));
    }
  }

  if (_bulkRegisterPendingClaruBatch && _bulkRegisterPendingConsumesClaruQueue) {
    const auto matchesSavedLogin = [&savedIdentities](const QJsonValue& value) {
      return savedIdentities.contains(value.toObject().value(QStringLiteral("login_email"))
                                          .toString().trimmed().toCaseFolded());
    };
    QJsonArray remainingQueue;
    for (const auto& value : _claruBatchAccounts)
      if (!matchesSavedLogin(value)) remainingQueue.append(value);
    _claruBatchAccounts = remainingQueue;

    QJsonArray remainingBody;
    for (const auto& value : _bulkRegisterPendingBody.value(QStringLiteral("accounts")).toArray())
      if (!matchesSavedLogin(value)) remainingBody.append(value);
    _bulkRegisterPendingBody.insert(QStringLiteral("accounts"), remainingBody);
  }
}

void MainWindow::rejectPendingRegistrationRequest(const QString& error) {
  QString safeError = error;
  const bool rejectedClaruQueue = _bulkRegisterPendingClaruBatch;
  const QString pendingPassword = _bulkRegisterPendingBody.value(QStringLiteral("password")).toString();
  if (!pendingPassword.isEmpty()) safeError.replace(pendingPassword, QStringLiteral("[oculto]"));
  _bulkRegisterPoll.stop();
  _bulkRegisterPolling = false;
  _bulkRegisterStarting = false;
  _bulkRegisterRequestInFlight = false;
  _bulkRegisterOutcomeUnknown = false;
  _bulkRegisterOwnRequestAccepted = false;
  _bulkRegisterRetryInFlight = false;
  _bulkRegisterPendingRequestId.clear();
  _bulkRegisterPendingPath.clear();
  _bulkRegisterPendingBody = {};
  _bulkRegisterPendingManualRegistration = false;
  _bulkRegisterPendingClaruPassword = false;
  _bulkRegisterPendingClaruBatch = false;
  _bulkRegisterPendingConsumesClaruQueue = false;
  _bulkRegisterPendingClearResults = false;
  _bulkRegisterBaselineReady = false;
  _bulkRegisterBaselineInvalid = false;
  _bulkRegisterBaselineState.clear();
  _bulkRegisterBaselineRequestId.clear();
  _bulkRegisterPreflightReady = false;
  if (rejectedClaruQueue) {
    // The queued Claru credentials remain available for an explicit later
    // attempt, but this rejected request id must not filter another job.
    _claruBatchRequestId.clear();
    _claruBatchRequestError = safeError;
  }
  _bulkRegisterDomain->setEnabled(true);
  _bulkRegisterCount->setEnabled(true);
  _bulkRegisterStatus->setText(QStringLiteral("Solicitação não confirmada: ") + safeError);
  setStatus(_bulkRegisterStatus->text());
  setAccountTransferBusy(_accountTransferBusy);
}

void MainWindow::retryPendingRegistrationRequest() {
  if (_bulkRegisterPendingRequestId.isEmpty() || _bulkRegisterOwnRequestAccepted
      || _bulkRegisterStarting || _bulkRegisterRetryInFlight || !_backendReady
      || !_bulkRegisterBaselineReady || _bulkRegisterBaselineInvalid) return;
  _bulkRegisterRetryInFlight = true;
  submitPendingRegistrationRequest();
}

void MainWindow::beginRegistrationPolling() {
  if (!_backendReady || _bulkRegisterStarting || _bulkRegisterRequestInFlight) return;
  _bulkRegisterPolling = true;
  ++_bulkRegisterDomainsRevision;
  ++_bulkRegisterPreflightRevision;
  setAccountTransferBusy(_accountTransferBusy);
  _bulkRegisterPoll.start();
  pollBulkRegister();
}

void MainWindow::resumeAccount(const QString& email) {
  if (!_backendReady || _bulkRegisterPolling || _bulkRegisterStarting || _bulkRegisterOutcomeUnknown
      || _accountTransferBusy || _orgMigrationRunning) return;
  const int generation = _operationBackendGeneration;
  QJsonArray profiles;
  if (!email.endsWith(QStringLiteral("@supply.claru.ai"), Qt::CaseInsensitive)
      && !collectRegistrationProfiles(this, 1, profiles)) return;
  if (!_backendReady || generation != _operationBackendGeneration || _bulkRegisterPolling
      || _bulkRegisterStarting || _bulkRegisterOutcomeUnknown || _accountTransferBusy) return;
  _bulkRegisterPendingRequestId = QUuid::createUuid().toString(QUuid::WithoutBraces);
  _bulkRegisterPendingPath = QStringLiteral("/api/accounts/") + encoded(email) + QStringLiteral("/resume");
  _bulkRegisterPendingBody = {{QStringLiteral("request_id"), _bulkRegisterPendingRequestId}};
  if (!profiles.isEmpty()) {
    const QJsonObject profile = profiles.first().toObject();
    for (auto it = profile.begin(); it != profile.end(); ++it) _bulkRegisterPendingBody.insert(it.key(), it.value());
  }
  _bulkRegisterPendingManualRegistration = false;
  _bulkRegisterPendingClaruPassword = false;
  _bulkRegisterPendingClaruBatch = false;
  _bulkRegisterPendingClearResults = false;
  _bulkRegisterPendingTotal = 1;
  _bulkRegisterOwnRequestAccepted = false;
  _bulkRegisterOutcomeUnknown = false;
  _bulkRegisterPreflightReady = false;
  _bulkRegisterBaselineReady = false;
  _bulkRegisterBaselineInvalid = false;
  _bulkRegisterStarting = true;
  submitPendingRegistrationRequest();
}

void MainWindow::openWebmail() {
  const QString text = _bulkRegisterWebmail->text();
  // Extrai o href do link
  const QRegularExpression hrefRe(QStringLiteral("href=\"([^\"]+)\""));
  const auto match = hrefRe.match(text);
  if (match.hasMatch()) {
    QDesktopServices::openUrl(QUrl(match.captured(1)));
  }
}

void MainWindow::pollBulkRegister() {
  if (!_backendReady || _bulkRegisterRequestInFlight || _bulkRegisterStarting) return;
  _bulkRegisterRequestInFlight = true;
  const int generation = _operationBackendGeneration;
  _api.get(QStringLiteral("/api/accounts/bulk-register/status"),
           [this, generation](bool ok, const QJsonDocument& doc, const QString& error) {
    if (!_backendReady || generation != _operationBackendGeneration) return;
    _bulkRegisterRequestInFlight = false;
    if (!ok) {
      _bulkRegisterPoll.setInterval(3000);
      _bulkRegisterStatus->setText(QStringLiteral("Conexão interrompida. Tentando recuperar o progresso… ") + error);
      return;
    }
    _bulkRegisterPoll.setInterval(900);
    const auto root = doc.object();
    const QString state = root.value(QStringLiteral("state")).toString();
    if (state != QStringLiteral("idle") && state != QStringLiteral("running")
        && state != QStringLiteral("stopping") && state != QStringLiteral("done")
        && state != QStringLiteral("failed")) {
      _bulkRegisterPoll.setInterval(3000);
      _bulkRegisterStatus->setText(QStringLiteral("Progresso inválido · registros e senha preservados enquanto o serviço é consultado novamente."));
      setStatus(_bulkRegisterStatus->text());
      return;
    }
    const QString statusRequestId = root.value(QStringLiteral("request_id")).toString();
    if (!_bulkRegisterPendingRequestId.isEmpty()) {
      if (statusRequestId == _bulkRegisterPendingRequestId) {
        if (!_bulkRegisterOwnRequestAccepted) acceptPendingRegistrationRequest();
      } else {
        const bool unchangedBaseline = !_bulkRegisterOwnRequestAccepted && _bulkRegisterBaselineReady
            && !_bulkRegisterBaselineInvalid && state == _bulkRegisterBaselineState
            && statusRequestId == _bulkRegisterBaselineRequestId
            && (state == QStringLiteral("idle")
                || ((state == QStringLiteral("done") || state == QStringLiteral("failed"))
                    && !_bulkRegisterBaselineRequestId.isEmpty()));
        _bulkRegisterPoll.setInterval(3000);
        if (unchangedBaseline) {
          _bulkRegisterStatus->setText(QStringLiteral("O estado continua igual ao observado antes do pedido. Vou confirmar a solicitação reenviando o mesmo identificador e conteúdo; a senha permanece no formulário."));
          setStatus(_bulkRegisterStatus->text());
          retryPendingRegistrationRequest();
        } else if (!_bulkRegisterAcceptedRequestId.isEmpty()) {
          _bulkRegisterStatus->setText(QStringLiteral(
              "O serviço mostrou outro lote antes de confirmar o pedido aceito. Os dados e o identificador original permanecem bloqueados."));
          setStatus(_bulkRegisterStatus->text());
        } else if (!_bulkRegisterOwnRequestAccepted) {
          _bulkRegisterBaselineInvalid = true;
          _bulkRegisterStatus->setText(QStringLiteral("O estado mudou desde antes do pedido e não corresponde a esta solicitação. Os dados permanecem bloqueados; não vou repetir o cadastro."));
          setStatus(_bulkRegisterStatus->text());
        } else {
          _bulkRegisterStatus->setText(QStringLiteral("O serviço ainda não confirmou o resultado desta solicitação. Os dados e o identificador ficam preservados, sem misturar outro lote."));
          setStatus(_bulkRegisterStatus->text());
        }
        return;
      }
    }
    if (!_bulkRegisterAcceptedRequestId.isEmpty()
        && statusRequestId != _bulkRegisterAcceptedRequestId) {
      _bulkRegisterPoll.setInterval(3000);
      _bulkRegisterStatus->setText(QStringLiteral(
          "O serviço mostrou outro lote antes de confirmar o pedido aceito. Dados, credenciais e resultados permanecem bloqueados até o identificador original reaparecer."));
      setStatus(_bulkRegisterStatus->text());
      return;
    }
    if (!_claruBatchRequestId.isEmpty() && _bulkRegisterPendingRequestId.isEmpty()
        && statusRequestId != _claruBatchRequestId) return;
    if (_bulkRegisterFollowingForeignRequest) {
      const bool activeForeign = state == QStringLiteral("running") || state == QStringLiteral("stopping");
      if (activeForeign) {
        // The global status endpoint may switch to a different client’s job
        // between polls. Follow the currently active identity without
        // treating its rows as part of the local Claru queue.
        if (!statusRequestId.isEmpty()) _bulkRegisterForeignRequestId = statusRequestId;
        _bulkRegisterPoll.setInterval(3000);
        _bulkRegisterStatus->setText(QStringLiteral(
            "Aguardando o lote já ativo no serviço. A fila Claru preparada permanece intacta."));
        setStatus(_bulkRegisterStatus->text());
        return;
      }
      if (state != QStringLiteral("idle") && state != QStringLiteral("done")
          && state != QStringLiteral("failed")) {
        _bulkRegisterPoll.setInterval(3000);
        return;
      }
      _bulkRegisterFollowingForeignRequest = false;
      _bulkRegisterForeignRequestId.clear();
      _bulkRegisterPoll.stop();
      _bulkRegisterPolling = false;
      _bulkRegisterPreserveClaruQueue = true;
      _claruStatus->setText(QStringLiteral(
          "O lote anterior terminou. A fila Claru preparada continua aguardando envio explícito."));
      _bulkRegisterStatus->setText(QStringLiteral(
          "O lote anterior terminou; nenhuma conta ou resultado foi atribuído à fila Claru preparada."));
      setStatus(_bulkRegisterStatus->text());
      setAccountTransferBusy(_accountTransferBusy);
      if (!_registrationProxiesReady || _bulkRegisterDomain->currentData().toString().isEmpty())
        loadBulkRegisterDomains(true);
      else
        checkBulkRegisterDomain(true);
      return;
    }
    if (state == QStringLiteral("idle")) {
      if (!_bulkRegisterPendingRequestId.isEmpty() || !_bulkRegisterAcceptedRequestId.isEmpty()) {
        _bulkRegisterPoll.setInterval(3000);
        _bulkRegisterStatus->setText(QStringLiteral("O serviço ainda não confirmou o resultado deste cadastro. A senha e a solicitação permanecem bloqueadas."));
        setStatus(_bulkRegisterStatus->text());
        return;
      }
      _bulkRegisterPoll.stop();
      _bulkRegisterPolling = false;
      setAccountTransferBusy(_accountTransferBusy);
      _bulkRegisterStatus->setText(QStringLiteral(
          "O serviço não possui um lote em andamento. Confira as contas salvas antes de iniciar outro cadastro."));
      loadAccounts();
      if (!_registrationProxiesReady || _bulkRegisterDomain->currentData().toString().isEmpty())
        loadBulkRegisterDomains(true);
      else
        checkBulkRegisterDomain(true);
      return;
    }
    const int total = root.value(QStringLiteral("total")).toInt();
    const int completed = root.value(QStringLiteral("completed")).toInt();
    const int created = root.value(QStringLiteral("created")).toInt();
    const int failed = root.value(QStringLiteral("failed")).toInt();
    const QString current = root.value(QStringLiteral("current_email")).toString();
    const QString currentStep = root.value(QStringLiteral("current_step")).toString();
    const auto results = root.value(QStringLiteral("results")).toArray();
    const bool terminal = state == QStringLiteral("done") || state == QStringLiteral("failed");
    const auto validCount = [](const QJsonValue& value) {
      return value.isDouble() && value.toDouble() >= 0 && value.toDouble() <= 50
          && std::floor(value.toDouble()) == value.toDouble();
    };
    if (!validCount(root.value(QStringLiteral("total")))
        || !validCount(root.value(QStringLiteral("completed")))
        || !validCount(root.value(QStringLiteral("created")))
        || !validCount(root.value(QStringLiteral("failed")))
        || !root.value(QStringLiteral("results")).isArray()
        || completed > total || created + failed != completed || results.size() != completed) {
      _bulkRegisterStatus->setText(QStringLiteral("Progresso inconsistente · o estado anterior e as credenciais permanecem preservados."));
      setStatus(_bulkRegisterStatus->text());
      _bulkRegisterPoll.setInterval(3000);
      return;
    }
    QSet<QString> resultOwners;
    const QSet<QString> allowedStepNames{
        QStringLiteral("proxy"), QStringLiteral("ban_check"), QStringLiteral("save_partial"),
        QStringLiteral("crowtado_signup"), QStringLiteral("demographics"),
        QStringLiteral("minute_identity"), QStringLiteral("minute_register"),
        QStringLiteral("link_minute"), QStringLiteral("validate")};
    const QSet<QString> allowedStepStatuses{
        QStringLiteral("ok"), QStringLiteral("skip"), QStringLiteral("fail"), QStringLiteral("manual")};
    const QSet<QString> allowedStepFields{
        QStringLiteral("status"), QStringLiteral("detail"), QStringLiteral("code"),
        QStringLiteral("remote_effect_possible"), QStringLiteral("retry_after_seconds"),
        QStringLiteral("retry_at"), QStringLiteral("http_status"), QStringLiteral("phase"),
        QStringLiteral("provider_error_code")};
    const QSet<QString> allowedStepPhases{
        QStringLiteral("browser_setup"), QStringLiteral("signup_submission"),
        QStringLiteral("email_verification"), QStringLiteral("signup_confirmation")};
    const QSet<QString> allowedProviderErrorCodes{
        QStringLiteral("user_banned"), QStringLiteral("user_locked"),
        QStringLiteral("user_account_disabled"), QStringLiteral("user_disabled"),
        QStringLiteral("form_identifier_not_found"), QStringLiteral("form_password_incorrect"),
        QStringLiteral("form_identifier_exists")};
    int confirmedCreated = 0;
    bool validResults = true;
    for (const auto& value : results) {
      const auto item = value.toObject();
      const QString email = item.value(QStringLiteral("email")).toString().trimmed().toCaseFolded();
      const QJsonValue errorValue = item.value(QStringLiteral("error"));
      const QJsonValue stepsValue = item.value(QStringLiteral("steps"));
      bool validSteps = stepsValue.isObject();
      const auto stepsObject = stepsValue.toObject();
      for (auto step = stepsObject.constBegin(); validSteps && step != stepsObject.constEnd(); ++step) {
        const QJsonValue stepValue = step.value();
        const auto stepObject = stepValue.toObject();
        const QString status = stepObject.value(QStringLiteral("status")).toString();
        const QJsonValue detailValue = stepObject.value(QStringLiteral("detail"));
        const QJsonValue codeValue = stepObject.value(QStringLiteral("code"));
        validSteps = allowedStepNames.contains(step.key()) && stepValue.isObject()
            && allowedStepStatuses.contains(status)
            && (detailValue.isUndefined() || detailValue.isString())
            && (codeValue.isUndefined() || codeValue.isString());
        for (const auto& field : stepObject.keys()) {
          if (!allowedStepFields.contains(field)) validSteps = false;
          const auto fieldValue = stepObject.value(field);
          if (field == QStringLiteral("remote_effect_possible") && !fieldValue.isBool()) validSteps = false;
          if (field == QStringLiteral("retry_after_seconds") || field == QStringLiteral("retry_at")
              || field == QStringLiteral("http_status")) {
            const double number = fieldValue.toDouble(-1);
            if (!fieldValue.isDouble() || number < 0 || std::floor(number) != number) validSteps = false;
          }
          if (field == QStringLiteral("phase")
              && (!fieldValue.isString() || !allowedStepPhases.contains(fieldValue.toString()))) validSteps = false;
          if (field == QStringLiteral("provider_error_code")
              && (!fieldValue.isString() || !allowedProviderErrorCodes.contains(fieldValue.toString()))) validSteps = false;
        }
      }
      if (!value.isObject() || email.isEmpty() || !email.contains(QLatin1Char('@'))
          || resultOwners.contains(email) || !item.value(QStringLiteral("created")).isBool()
          || !validSteps
          || !(errorValue.isNull() || errorValue.isString())
          || (item.value(QStringLiteral("created")).toBool() && !errorValue.isNull())
          || (!item.value(QStringLiteral("created")).toBool() && errorValue.toString().trimmed().isEmpty())) {
        validResults = false; break;
      }
      resultOwners.insert(email);
      if (item.value(QStringLiteral("created")).toBool()) ++confirmedCreated;
    }
    if (!validResults || confirmedCreated != created) {
      _bulkRegisterStatus->setText(QStringLiteral("Resultados inconsistentes · o diagnóstico e os dados anteriores foram preservados."));
      setStatus(_bulkRegisterStatus->text());
      _bulkRegisterPoll.setInterval(3000);
      return;
    }
    QStringList registrationDiagnosticSecrets;
    const auto captureAccountSecrets = [&registrationDiagnosticSecrets](const QJsonArray& accounts) {
      for (const auto& value : accounts) {
        const auto account = value.toObject();
        for (const auto& key : {QStringLiteral("password"), QStringLiteral("minute_password"),
                                QStringLiteral("claru_password")}) {
          const QString secret = account.value(key).toString();
          if (!secret.isEmpty()) registrationDiagnosticSecrets.append(secret);
        }
      }
    };
    for (const auto& key : {QStringLiteral("password"), QStringLiteral("minute_password"),
                            QStringLiteral("claru_password")}) {
      const QString secret = _bulkRegisterPendingBody.value(key).toString();
      if (!secret.isEmpty()) registrationDiagnosticSecrets.append(secret);
    }
    captureAccountSecrets(_bulkRegisterPendingBody.value(QStringLiteral("accounts")).toArray());
    captureAccountSecrets(_claruBatchAccounts);
    if (!_bulkRegisterAcceptedRequestId.isEmpty()
        && statusRequestId == _bulkRegisterAcceptedRequestId)
      consumeRegistrationCheckpoints(results);
    if (!_bulkRegisterPendingRequestId.isEmpty()
        && statusRequestId == _bulkRegisterPendingRequestId && _bulkRegisterPendingClearResults) {
      _bulkRegisterTable->setRowCount(0);
      _bulkRegisterResults = {};
      _bulkRegisterProgress->setRange(0, qMax(1, _bulkRegisterPendingTotal));
      _bulkRegisterProgress->setValue(0);
      _bulkRegisterPendingClearResults = false;
    }
    _bulkRegisterProgress->setRange(0, qMax(1, total));
    _bulkRegisterProgress->setValue(completed);
    if (root.value("provider").toString() == QStringLiteral("claru")
        && !_bulkRegisterPreserveClaruQueue) renderClaruBatch(root);
    _bulkRegisterResults = results;
    _bulkRegisterTable->setRowCount(results.size());
    const auto sanitizeRegistrationDiagnostic = [this, registrationDiagnosticSecrets](QString text) {
      QStringList secrets = registrationDiagnosticSecrets;
      secrets << _accountPassword->text() << _claruPassword->text() << _claruMinutePassword->text();
      std::sort(secrets.begin(), secrets.end(), [](const QString& left, const QString& right) {
        return left.size() > right.size();
      });
      for (const auto& secret : secrets)
        if (!secret.isEmpty()) text.replace(secret, QStringLiteral("[oculto]"));
      return text;
    };
    for (int row = 0; row < results.size(); ++row) {
      const auto item = results.at(row).toObject();
      auto* emailItem = cell(item.value(QStringLiteral("email")).toString());
      _bulkRegisterTable->setItem(row, 0, emailItem);
      _bulkRegisterTable->setItem(row, 1, cell(item.value(QStringLiteral("nome")).toString()));
      _bulkRegisterTable->setItem(row, 2, cell(item.value(QStringLiteral("sobrenome")).toString()));
      _bulkRegisterTable->setItem(row, 3, cell(item.value(QStringLiteral("gender")).toString()));
      // Construir tooltip com todas as etapas
      QStringList stepLines;
      const auto steps = item.value(QStringLiteral("steps")).toObject();
      const QStringList stepKeys = {
          QStringLiteral("ban_check"), QStringLiteral("save_partial"),
          QStringLiteral("crowtado_signup"), QStringLiteral("demographics"),
          QStringLiteral("minute_identity"), QStringLiteral("minute_register"), QStringLiteral("link_minute"),
          QStringLiteral("validate"),
      };
      const QStringList stepNames = {
          QStringLiteral("Verificação"), QStringLiteral("Credenciais"),
          item.value("email").toString().endsWith(QStringLiteral("@supply.claru.ai")) ? QStringLiteral("Claru") : QStringLiteral("Crowtado"), QStringLiteral("Perfil"),
          QStringLiteral("Identidade Minute"), QStringLiteral("Minute"), QStringLiteral("Vínculo"),
          QStringLiteral("Validação"),
      };
      for (int s = 0; s < stepKeys.size(); ++s) {
        const auto stepObj = steps.value(stepKeys.at(s)).toObject();
        const QString status = stepObj.value(QStringLiteral("status")).toString();
        const QString detail = sanitizeRegistrationDiagnostic(
            stepObj.value(QStringLiteral("detail")).toString());
        QString icon;
        if (status == QStringLiteral("ok")) icon = QStringLiteral("✓");
        else if (status == QStringLiteral("skip")) icon = QStringLiteral("↷");
        else if (status == QStringLiteral("fail")) icon = QStringLiteral("✗");
        else icon = QStringLiteral("·");
        QString line = detail.isEmpty()
            ? QStringLiteral("%1 %2").arg(icon, stepNames.at(s))
            : QStringLiteral("%1 %2: %3").arg(icon, stepNames.at(s), detail);
        const QString providerCode = stepObj.value(QStringLiteral("provider_error_code")).toString();
        if (!providerCode.isEmpty()) line += QStringLiteral(" · código do provedor: ") + providerCode;
        if (stepObj.value(QStringLiteral("http_status")).isDouble())
          line += QStringLiteral(" · HTTP ") + QString::number(stepObj.value(QStringLiteral("http_status")).toInt());
        const QString phase = stepObj.value(QStringLiteral("phase")).toString();
        if (!phase.isEmpty()) line += QStringLiteral(" · etapa técnica: ") + phase;
        if (stepObj.value(QStringLiteral("retry_after_seconds")).isDouble())
          line += QStringLiteral(" · aguarde %1 s antes de tentar novamente").arg(stepObj.value(QStringLiteral("retry_after_seconds")).toInt());
        if (stepObj.value(QStringLiteral("remote_effect_possible")).toBool())
          line += QStringLiteral(" · efeito remoto ainda não confirmado; retomar verifica esta conta sem repetir a criação");
        stepLines << line;
      }
      emailItem->setToolTip(stepLines.join(QStringLiteral("\n")));
      const QString errorText = sanitizeRegistrationDiagnostic(item.value(QStringLiteral("error")).toString());
      const bool removed = item.value(QStringLiteral("removed")).toBool(false);
      QString outcome;
      if (removed) {
        outcome = QStringLiteral("Removida deste QMoney");
      } else if (errorText.isEmpty()) {
        outcome = QStringLiteral("✓ completa");
      } else {
        // Descobrir em qual etapa falhou
        QString failedStep;
        for (int s = stepKeys.size() - 1; s >= 0; --s) {
          const auto stepObj = steps.value(stepKeys.at(s)).toObject();
          if (stepObj.value(QStringLiteral("status")).toString() == QStringLiteral("fail")) {
            failedStep = stepNames.at(s);
            break;
          }
        }
        outcome = failedStep.isEmpty()
            ? QStringLiteral("✗ ") + errorText
            : QStringLiteral("✗ falhou em: ") + failedStep;
      }
      auto* outcomeItem = cell(outcome);
      outcomeItem->setToolTip(stepLines.join(QStringLiteral("\n")));
      _bulkRegisterTable->setItem(row, 4, outcomeItem);
      const bool removable = item.value(QStringLiteral("removable"))
                                 .toBool(item.value(QStringLiteral("created")).toBool(false));
      auto* actionsButton = new QPushButton(QStringLiteral("Ações"));
      auto* actions = new QMenu(actionsButton);
      actions->addAction(QStringLiteral("Ver etapas e diagnóstico"), this, [this, item, stepLines, errorText] {
        QStringList details = stepLines;
        if (!errorText.isEmpty()) details << QStringLiteral("Erro: ") + errorText;
        QMessageBox::information(this, QStringLiteral("Diagnóstico do cadastro"),
            details.isEmpty() ? QStringLiteral("Ainda não há detalhes de etapas disponíveis.")
                              : details.join(QLatin1Char('\n')));
      });
      const QString email = item.value(QStringLiteral("email")).toString();
      const bool pendingProfile = steps.value(QStringLiteral("demographics")).toObject().value(QStringLiteral("status")).toString() == QStringLiteral("manual")
          || steps.value(QStringLiteral("link_minute")).toObject().value(QStringLiteral("status")).toString() == QStringLiteral("manual");
      if (terminal && (item.value(QStringLiteral("partial")).toBool() || pendingProfile) && !removed)
        actions->addAction(QStringLiteral("Retomar esta conta"), this, [this, email] { resumeAccount(email); });
      auto* removeAction = actions->addAction(QStringLiteral("Remover deste computador"), this, [this, email] {
        removeAccount(email, [this] { pollBulkRegister(); });
      });
      removeAction->setEnabled(terminal && removable && !removed);
      actionsButton->setMenu(actions);
      actionsButton->setEnabled(terminal);
      _bulkRegisterTable->setCellWidget(row, 5, actionsButton);
    }
    if (terminal) {
      if (statusRequestId == _bulkRegisterAcceptedRequestId)
        _bulkRegisterAcceptedRequestId.clear();
      if (!_bulkRegisterPendingRequestId.isEmpty() && statusRequestId == _bulkRegisterPendingRequestId) {
        _bulkRegisterPendingRequestId.clear();
        _bulkRegisterOwnRequestAccepted = false;
        _bulkRegisterBaselineReady = false;
        _bulkRegisterBaselineInvalid = false;
        _bulkRegisterBaselineState.clear();
        _bulkRegisterBaselineRequestId.clear();
        _bulkRegisterPendingPath.clear();
        _bulkRegisterPendingBody = {};
        _bulkRegisterPendingManualRegistration = false;
        _bulkRegisterPendingClaruPassword = false;
        _bulkRegisterPendingClaruBatch = false;
        _bulkRegisterPendingConsumesClaruQueue = false;
        _bulkRegisterPendingClearResults = false;
      }
      if (!_claruBatchRequestId.isEmpty() && statusRequestId == _claruBatchRequestId) {
        _claruBatchRequestId.clear();
        _claruBatchRequestError.clear();
      }
      _bulkRegisterPoll.stop();
      _bulkRegisterPolling = false;
      setAccountTransferBusy(_accountTransferBusy);
      _bulkRegisterDomain->setEnabled(true);
      _bulkRegisterCount->setEnabled(true);
      if (state == QStringLiteral("failed")) {
        _bulkRegisterStatus->setText(QStringLiteral("Falha: ")
                                     + root.value(QStringLiteral("error")).toString());
      } else {
        _bulkRegisterStatus->setText(
            QStringLiteral("Concluído: %1 criada(s), %2 falha(s) de %3.")
                .arg(created).arg(failed).arg(total));
        const QString stopReason = root.value(QStringLiteral("stop_reason")).toString();
        if (!stopReason.isEmpty())
          _bulkRegisterStatus->setText(stopReason + QStringLiteral(" %1 de %2 processada(s).")
              .arg(completed).arg(total));
        loadAccounts();
      }
      const QString diagnosticPrefix = _bulkRegisterStatus->text().section('\n', 0, 0);
      if (!_registrationProxiesReady || _bulkRegisterDomain->currentData().toString().isEmpty())
        loadBulkRegisterDomains(true);
      else
        checkBulkRegisterDomain(true);
      if (!diagnosticPrefix.isEmpty()) setStatus(diagnosticPrefix);
    } else {
      QString label;
      if (current.isEmpty()) {
        label = QStringLiteral("Processando…");
      } else if (currentStep.isEmpty()) {
        label = QStringLiteral("Registrando %1").arg(current);
      } else {
        label = QStringLiteral("%1 — etapa: %2").arg(current, currentStep);
      }
      _bulkRegisterStatus->setText(
          QStringLiteral("%1 — %2/%3 concluído(s).")
              .arg(label).arg(completed).arg(total));
    }
    if (root.value("domain").toString() == QStringLiteral("supply.claru.ai"))
      _claruStatus->setText(_bulkRegisterStatus->text());
  });
}

QWidget* MainWindow::buildAccountsPage() {
  auto* body = new QWidget;
  auto* layout = new QVBoxLayout(body);
  layout->setContentsMargins(0, 0, 0, 0);
  layout->setSpacing(14);
  auto* tabs = new QTabWidget;
  tabs->setDocumentMode(true);
  layout->addWidget(tabs);
  auto addTab = [tabs](const QString& title, QWidget* widget) {
    auto* content = new QWidget;
    auto* contentLayout = new QVBoxLayout(content);
    contentLayout->setContentsMargins(0, 0, 0, 0);
    contentLayout->addWidget(widget);
    contentLayout->addStretch();
    auto* scroll = new QScrollArea;
    scroll->setWidgetResizable(true);
    scroll->setFrameShape(QFrame::NoFrame);
    scroll->setWidget(content);
    tabs->addTab(scroll, title);
  };

  auto* formBody = new QWidget;
  auto* form = new QFormLayout(formBody);
  configureForm(form);
  form->setContentsMargins(0, 0, 0, 0);
  _accountEmail = new QLineEdit;
  _accountEmail->setPlaceholderText(QStringLiteral("email da conta"));
  form->addRow(QStringLiteral("Email"), _accountEmail);
  _accountPassword = new QLineEdit;
  _accountPassword->setEchoMode(QLineEdit::Password);
  _accountPassword->setPlaceholderText(QStringLiteral("senha do Minute / Crowtado"));
  form->addRow(QStringLiteral("Senha"), _accountPassword);
  _accountProxy = new ComboBox;
  configureCombo(_accountProxy);
  _accountProxy->addItem(QStringLiteral("Carregando proxies…"), QString());
  _accountProxyImport = new QPushButton(QStringLiteral("Importar proxies…"));
  auto* accountProxyRow = new QWidget;
  auto* accountProxyLayout = new QHBoxLayout(accountProxyRow);
  accountProxyLayout->setContentsMargins(0, 0, 0, 0);
  accountProxyLayout->addWidget(_accountProxy, 1);
  accountProxyLayout->addWidget(_accountProxyImport);
  form->addRow(QStringLiteral("Proxy (nova conta)"), accountProxyRow);
  connect(_accountProxyImport, &QPushButton::clicked, this, [this] {
    const auto path = QFileDialog::getOpenFileName(this, QStringLiteral("Importar proxies"), {},
        QStringLiteral("Lista de proxies (*.txt);;Todos os arquivos (*)"));
    if (!path.isEmpty()) importRegistrationProxiesFile(path);
  });
  auto* actions = new QWidget;
  auto* actionLayout = new QHBoxLayout(actions);
  actionLayout->setContentsMargins(0, 0, 0, 0);
  actionLayout->addStretch();
  _accountAdd = new QPushButton(QStringLiteral("Adicionar existente"));
  connect(_accountAdd, &QPushButton::clicked, this, [this] { addAccount(false); });
  actionLayout->addWidget(_accountAdd);
  _accountRegister = primaryButton(QStringLiteral("Registrar nova"));
  connect(_accountRegister, &QPushButton::clicked, this, [this] { addAccount(true); });
  actionLayout->addWidget(_accountRegister);
  form->addRow(QString(), actions);
  addTab(QStringLiteral("Conectar conta"), card(QStringLiteral("Conectar conta"), formBody));

  auto* claruBody = new QWidget;
  auto* claruForm = new QFormLayout(claruBody);
  configureForm(claruForm);
  claruForm->setContentsMargins(0, 0, 0, 0);
  claruForm->addRow(quietLabel(QStringLiteral(
      "Entre com sua conta Claru. O e-mail gerado na caixa Claru será usado no Minute.")));
  _claruConnections = new ComboBox;
  configureCombo(_claruConnections);
  _claruConnections->addItem(QStringLiteral("Nova conexão Claru"), QJsonObject{});
  claruForm->addRow(QStringLiteral("Conexões salvas"), _claruConnections);
  _claruEmail = new QLineEdit;
  _claruEmail->setPlaceholderText(QStringLiteral("e-mail usado para entrar no Claru"));
  claruForm->addRow(QStringLiteral("Login Claru"), _claruEmail);
  _claruPassword = new QLineEdit;
  _claruPassword->setEchoMode(QLineEdit::Password);
  _claruPassword->setPlaceholderText(QStringLiteral("senha do Claru; deixe vazio para usar a conexão salva"));
  claruForm->addRow(QStringLiteral("Senha Claru"), _claruPassword);
  _claruConnect = new QPushButton(QStringLiteral("Consultar perfil Claru"));
  claruForm->addRow(QString(), _claruConnect);
  connect(_claruConnect, &QPushButton::clicked, this, &MainWindow::connectClaruAccount);
  _claruMinuteEmail = new QLineEdit;
  _claruMinuteEmail->setReadOnly(true);
  _claruMinuteEmail->setPlaceholderText(QStringLiteral("será obtido automaticamente no perfil e na caixa Claru"));
  claruForm->addRow(QStringLiteral("E-mail do Minute"), _claruMinuteEmail);
  _claruMinutePassword = new QLineEdit;
  _claruMinutePassword->setEchoMode(QLineEdit::Password);
  _claruMinutePassword->setPlaceholderText(QStringLiteral("senha própria da conta Minute"));
  claruForm->addRow(QStringLiteral("Senha do Minute"), _claruMinutePassword);
  _claruProxy = new ComboBox;
  configureCombo(_claruProxy);
  _claruProxy->addItem(QStringLiteral("Carregando proxies…"), QString());
  claruForm->addRow(QStringLiteral("Proxy (cadastro Minute)"), _claruProxy);
  claruForm->addRow(quietLabel(QStringLiteral("Organização Minute: Claru · WSNEHSKC")));
  auto* claruActions = new QWidget;
  auto* claruActionLayout = new QHBoxLayout(claruActions);
  claruActionLayout->setContentsMargins(0, 0, 0, 0);
  auto* claruInbox = new QPushButton(QStringLiteral("Abrir caixa Claru"));
  connect(claruInbox, &QPushButton::clicked, this, [] {
    QDesktopServices::openUrl(QUrl(QStringLiteral("https://app.claru.ai/inbox")));
  });
  claruActionLayout->addWidget(claruInbox);
  claruActionLayout->addStretch();
  _claruAddMinute = new QPushButton(QStringLiteral("Conectar Minute existente"));
  _claruRegisterMinute = primaryButton(QStringLiteral("Registrar Minute"));
  claruActionLayout->addWidget(_claruAddMinute);
  claruActionLayout->addWidget(_claruRegisterMinute);
  claruForm->addRow(QString(), claruActions);
  connect(_claruAddMinute, &QPushButton::clicked, this, [this] { connectClaruMinute(false); });
  connect(_claruRegisterMinute, &QPushButton::clicked, this, [this] { connectClaruMinute(true); });
  _claruStatus = quietLabel(QStringLiteral("Consulte o perfil para confirmar o e-mail gerado. As duas senhas são armazenadas separadamente no cofre local."));
  _claruStatus->setWordWrap(true);
  claruForm->addRow(QString(), _claruStatus);
  connect(_claruEmail, &QLineEdit::textChanged, this, [this] {
    _claruMinuteEmail->clear();
    _claruMinutePassword->clear();
    _claruAddMinute->setEnabled(false);
    _claruRegisterMinute->setEnabled(false);
  });
  connect(_claruConnections, qOverload<int>(&QComboBox::currentIndexChanged), this, [this] {
    const auto row = _claruConnections->currentData().toJsonObject();
    _claruEmail->setText(row.value("login_email").toString());
    _claruPassword->clear();
    _claruMinuteEmail->setText(row.value("minute_email").toString());
    setAccountTransferBusy(_accountTransferBusy);
  });
  _claruAddMinute->setEnabled(false);
  _claruRegisterMinute->setEnabled(false);
  claruForm->addRow(quietLabel(QStringLiteral("Cadastrar Minute para várias contas Claru")));
  claruForm->addRow(quietLabel(QStringLiteral(
      "Selecione uma conexão Claru e informe a senha do Minute para adicionar ao lote, ou importe um JSON. "
      "Cada login Claru existente será associado ao seu próprio e-mail gerado no Minute.")));
  auto* claruBatchActions = new QWidget;
  auto* claruBatchLayout = new QHBoxLayout(claruBatchActions);
  claruBatchLayout->setContentsMargins(0, 0, 0, 0);
  _claruBatchAdd = new QPushButton(QStringLiteral("Adicionar ao lote Minute"));
  _claruBatchImport = new QPushButton(QStringLiteral("Importar lote JSON…"));
  auto* claruSignup = new QPushButton(QStringLiteral("Abrir perfil Claru"));
  connect(claruSignup, &QPushButton::clicked, this, [] {
    QDesktopServices::openUrl(QUrl(QStringLiteral("https://app.claru.ai/users/profile")));
  });
  connect(_claruBatchAdd, &QPushButton::clicked, this, [this] {
    const auto email = _claruEmail->text().trimmed().toLower();
    if (!email.contains('@') || email.endsWith(QStringLiteral("@supply.claru.ai"))
        || _claruMinutePassword->text().size() < 8)
      return showError(QStringLiteral("Conta incompleta"), QStringLiteral("Informe o login Claru e a senha do Minute, com pelo menos 8 caracteres. A senha Claru pode ficar vazia para conexões salvas."));
    if (_claruBatchAccounts.size() >= 50)
      return showError(QStringLiteral("Lote completo"), QStringLiteral("Use no máximo 50 contas por lote."));
    for (const auto value : _claruBatchAccounts)
      if (value.toObject().value("login_email").toString() == email)
        return showError(QStringLiteral("Conta repetida"), QStringLiteral("Este login já está no lote."));
    QJsonObject entry{{"login_email", email}, {"minute_password", _claruMinutePassword->text()}};
    if (!_claruPassword->text().isEmpty()) entry.insert("claru_password", _claruPassword->text());
    _claruBatchAccounts.append(entry);
    _claruPassword->clear(); _claruMinutePassword->clear();
    renderClaruBatch({});
    setAccountTransferBusy(_accountTransferBusy);
  });
  connect(_claruBatchImport, &QPushButton::clicked, this, [this] {
    const auto path = QFileDialog::getOpenFileName(this, QStringLiteral("Importar lote Minute de contas Claru"), {}, QStringLiteral("Lote Claru (*.json)"));
    if (!path.isEmpty()) importClaruBatchFile(path);
  });
  claruBatchLayout->addWidget(_claruBatchAdd);
  claruBatchLayout->addWidget(_claruBatchImport);
  claruBatchLayout->addWidget(claruSignup);
  claruForm->addRow(claruBatchActions);
  _claruBatchTable = new QTableWidget(0, 3);
  configureTable(_claruBatchTable);
  _claruBatchTable->setMinimumHeight(180);
  _claruBatchTable->setHorizontalHeaderLabels({QStringLiteral("Login Claru"), QStringLiteral("E-mail Minute"), QStringLiteral("Etapa / resultado")});
  _claruBatchTable->horizontalHeader()->setSectionResizeMode(QHeaderView::Stretch);
  _claruBatchTable->setEditTriggers(QAbstractItemView::NoEditTriggers);
  claruForm->addRow(_claruBatchTable);
  auto* claruRunActions = new QWidget;
  auto* claruRunLayout = new QHBoxLayout(claruRunActions);
  claruRunLayout->setContentsMargins(0, 0, 0, 0);
  _claruBatchStart = primaryButton(QStringLiteral("Cadastrar Minutes do lote"));
  _claruBatchResume = new QPushButton(QStringLiteral("Retomar pendentes"));
  _claruBatchStop = new QPushButton(QStringLiteral("Parar lote"));
  _claruBatchStart->setEnabled(false); _claruBatchResume->setEnabled(false); _claruBatchStop->setEnabled(false);
  connect(_claruBatchStart, &QPushButton::clicked, this, [this] { startClaruBatch(false); });
  connect(_claruBatchResume, &QPushButton::clicked, this, [this] { startClaruBatch(true); });
  connect(_claruBatchStop, &QPushButton::clicked, this, [this] {
    _claruBatchStop->setEnabled(false);
    _api.post(QStringLiteral("/api/accounts/bulk-register/stop"), {}, [this](bool ok, const QJsonDocument&, const QString& error) {
      if (!ok) { _claruStatus->setText(error); _claruBatchStop->setEnabled(_bulkRegisterPolling); }
      else _claruStatus->setText(QStringLiteral("Parada solicitada. A conta em andamento termina antes de interromper o lote."));
    });
  });
  claruRunLayout->addWidget(_claruBatchStart); claruRunLayout->addWidget(_claruBatchResume); claruRunLayout->addWidget(_claruBatchStop);
  claruForm->addRow(claruRunActions);
  addTab(QStringLiteral("Claru"), card(QStringLiteral("Claru → Minute"), claruBody));

  // --- Criador de contas ---------------------------------------------------
  auto* bulkBody = new QWidget;
  auto* bulkForm = new QFormLayout(bulkBody);
  configureForm(bulkForm);
  bulkForm->setContentsMargins(0, 0, 0, 0);
  _bulkRegisterDomain = new ComboBox;
  configureCombo(_bulkRegisterDomain);
  _bulkRegisterDomain->setEditable(false);
  _bulkRegisterDomain->addItem(QStringLiteral("carregando domínios…"));
  connect(_bulkRegisterDomain, qOverload<int>(&QComboBox::currentIndexChanged),
          this, [this] { checkBulkRegisterDomain(); });
  bulkForm->addRow(QStringLiteral("Domínio"), _bulkRegisterDomain);
  _bulkRegisterCount = new QSpinBox;
  _bulkRegisterCount->setRange(1, 50);
  _bulkRegisterCount->setValue(1);
  _bulkRegisterCount->setMaximumWidth(200);
  bulkForm->addRow(QStringLiteral("Quantidade"), _bulkRegisterCount);
  _bulkRegisterProxy = new ComboBox;
  configureCombo(_bulkRegisterProxy);
  _bulkRegisterProxy->addItem(QStringLiteral("Carregando proxies…"), QString());
  _bulkProxyImport = new QPushButton(QStringLiteral("Importar proxies…"));
  auto* proxyRow = new QWidget;
  auto* proxyLayout = new QHBoxLayout(proxyRow);
  proxyLayout->setContentsMargins(0, 0, 0, 0);
  proxyLayout->addWidget(_bulkRegisterProxy, 1);
  proxyLayout->addWidget(_bulkProxyImport);
  bulkForm->addRow(QStringLiteral("Proxy"), proxyRow);
  bulkForm->addRow(quietLabel(QStringLiteral(
      "TXT: host:porta:login:senha. A conexão escolhida permanece atribuída à conta.")));
  bulkForm->addRow(quietLabel(QStringLiteral("Organização Crowtado: PE8EAR5V")));
  connect(_bulkRegisterProxy, qOverload<int>(&QComboBox::currentIndexChanged),
      this, [this] { checkBulkRegisterDomain(); });
  connect(_bulkProxyImport, &QPushButton::clicked, _accountProxyImport, &QPushButton::click);
  auto* bulkActions = new QWidget;
  auto* bulkActionLayout = new QHBoxLayout(bulkActions);
  bulkActionLayout->setContentsMargins(0, 0, 0, 0);
  bulkActionLayout->addStretch();
  auto* bulkRefreshDomains = new QPushButton(QStringLiteral("Atualizar domínios"));
  connect(bulkRefreshDomains, &QPushButton::clicked, this, &MainWindow::loadBulkRegisterDomains);
  bulkActionLayout->addWidget(bulkRefreshDomains);
  _bulkRegisterStart = primaryButton(QStringLiteral("Criar contas"));
  connect(_bulkRegisterStart, &QPushButton::clicked, this, &MainWindow::startBulkRegister);
  bulkActionLayout->addWidget(_bulkRegisterStart);
  bulkForm->addRow(QString(), bulkActions);
  _bulkRegisterStatus = quietLabel(QStringLiteral(
      "Cadastro Crowtado e Minute com verificação de acesso. "
      "O perfil é preenchido e o Minute é vinculado à tarefa. Conclua o equipamento no site."));
  _bulkRegisterStatus->setWordWrap(true);
  bulkForm->addRow(QString(), _bulkRegisterStatus);
  _bulkRegisterWebmail = new QLabel;
  _bulkRegisterWebmail->setOpenExternalLinks(false);
  _bulkRegisterWebmail->setTextFormat(Qt::RichText);
  _bulkRegisterWebmail->hide();
  connect(_bulkRegisterWebmail, &QLabel::linkActivated, this, &MainWindow::openWebmail);
  bulkForm->addRow(QString(), _bulkRegisterWebmail);
  _bulkRegisterProgress = new QProgressBar;
  _bulkRegisterProgress->setObjectName(QStringLiteral("bulkRegisterProgress"));
  _bulkRegisterProgress->setRange(0, 100);
  _bulkRegisterProgress->setValue(0);
  _bulkRegisterProgress->setTextVisible(true);
  _bulkRegisterProgress->setFormat(QStringLiteral("%v/%m"));
  bulkForm->addRow(QStringLiteral("Progresso"), _bulkRegisterProgress);
  _bulkRegisterTable = new QTableWidget(0, 6);
  configureTable(_bulkRegisterTable);
  _bulkRegisterTable->setMinimumHeight(160);
  _bulkRegisterTable->setHorizontalHeaderLabels({
      QStringLiteral("Email"), QStringLiteral("Nome"), QStringLiteral("Sobrenome"),
      QStringLiteral("Gênero"), QStringLiteral("Resultado"), QStringLiteral("Ação"),
  });
  _bulkRegisterTable->horizontalHeader()->setSectionResizeMode(0, QHeaderView::Stretch);
  _bulkRegisterTable->horizontalHeader()->setSectionResizeMode(1, QHeaderView::Interactive);
  _bulkRegisterTable->horizontalHeader()->setSectionResizeMode(2, QHeaderView::Interactive);
  _bulkRegisterTable->horizontalHeader()->setSectionResizeMode(3, QHeaderView::Interactive);
  _bulkRegisterTable->horizontalHeader()->setSectionResizeMode(4, QHeaderView::Interactive);
  _bulkRegisterTable->horizontalHeader()->setSectionResizeMode(5, QHeaderView::Fixed);
  _bulkRegisterTable->setColumnWidth(1, 140);
  _bulkRegisterTable->setColumnWidth(2, 140);
  _bulkRegisterTable->setColumnWidth(3, 90);
  _bulkRegisterTable->setColumnWidth(4, 180);
  _bulkRegisterTable->setColumnWidth(5, 108);
  _bulkRegisterTable->setSelectionBehavior(QAbstractItemView::SelectRows);
  _bulkRegisterTable->setSelectionMode(QAbstractItemView::NoSelection);
  bulkForm->addRow(quietLabel(QStringLiteral("Resultados")));
  bulkForm->addRow(_bulkRegisterTable);
  _bulkRegisterPoll.setInterval(900);
  connect(&_bulkRegisterPoll, &QTimer::timeout, this, &MainWindow::pollBulkRegister);
  _bulkRegisterStart->setEnabled(false);
  addTab(QStringLiteral("Criar contas"), card(QStringLiteral("Criador de contas"), bulkBody));

  _accountsTable = new QTableWidget(0, 4);
  configureTable(_accountsTable);
  _accountsTable->setMinimumHeight(230);
  _accountsTable->setHorizontalHeaderLabels(
      {QStringLiteral("Conta"), QStringLiteral("Organização"), QStringLiteral("Última verificação"), QStringLiteral("Ações")});
  _accountsTable->horizontalHeader()->setSectionResizeMode(0, QHeaderView::Stretch);
  _accountsTable->horizontalHeader()->setSectionResizeMode(1, QHeaderView::Interactive);
  _accountsTable->horizontalHeader()->setSectionResizeMode(2, QHeaderView::Interactive);
  _accountsTable->horizontalHeader()->setSectionResizeMode(3, QHeaderView::Fixed);
  _accountsTable->setColumnWidth(1, 150);
  _accountsTable->setColumnWidth(2, 175);
  _accountsTable->setColumnWidth(3, 232);
  _accountsTable->setSelectionBehavior(QAbstractItemView::SelectRows);
  _accountsTable->setSelectionMode(QAbstractItemView::ExtendedSelection);

  auto* accountsBody = new QWidget;
  auto* accountsLayout = new QVBoxLayout(accountsBody);
  accountsLayout->setContentsMargins(0, 0, 0, 0);
  accountsLayout->setSpacing(10);
  auto* tableActions = new QHBoxLayout;
  tableActions->addWidget(quietLabel(
      QStringLiteral("Valide tokens e disponibilidade de todas as identidades em uma única leitura.")), 1);
  _accountsCheckAll = primaryButton(QStringLiteral("Verificar todas"));
  connect(_accountsCheckAll, &QPushButton::clicked, this, &MainWindow::checkAllAccounts);
  tableActions->addWidget(_accountsCheckAll);
  accountsLayout->addLayout(tableActions);
  auto* transferActions = new QHBoxLayout;
  _accountsImport = new QPushButton(QStringLiteral("Importar JSON"));
  _accountsExport = new QPushButton(QStringLiteral("Exportar todas"));
  _accountsExportSelected = new QPushButton(QStringLiteral("Exportar selecionadas"));
  connect(_accountsImport, &QPushButton::clicked, this, &MainWindow::importAccounts);
  connect(_accountsExport, &QPushButton::clicked, this, [this] { exportAccounts(false); });
  connect(_accountsExportSelected, &QPushButton::clicked, this, [this] { exportAccounts(true); });
  transferActions->addWidget(_accountsImport);
  transferActions->addWidget(_accountsExport);
  transferActions->addWidget(_accountsExportSelected);
  auto* exportBanned = new QPushButton(QStringLiteral("Exportar banidas"));
  transferActions->addWidget(exportBanned);
  connect(exportBanned, &QPushButton::clicked, this, [this] {
    const QString path = QFileDialog::getSaveFileName(this, QStringLiteral("Salvar contas banidas"),
        QStringLiteral("banned_accounts.json"), QStringLiteral("JSON (*.json)"));
    if (path.isEmpty()) return;
    _api.get(QStringLiteral("/api/accounts/banned"), [this, path](bool ok, const QJsonDocument& doc, const QString& error) {
      if (!ok) return showError(QStringLiteral("Falha ao exportar banidas"), error);
      QSaveFile output(path);
      if (!output.open(QIODevice::WriteOnly) || output.write(doc.toJson(QJsonDocument::Indented)) < 0 || !output.commit())
        return showError(QStringLiteral("Falha ao exportar banidas"), QStringLiteral("Não foi possível salvar o arquivo."));
      setStatus(QStringLiteral("Registro de contas banidas salvo."));
    });
  });
  transferActions->addStretch();
  accountsLayout->addLayout(transferActions);
  auto* migrationActions = new QHBoxLayout;
  _accountsMigrate = new QPushButton(QStringLiteral("Atualizar organização Crowtado"));
  _accountsMigrate->setToolTip(QStringLiteral("Atualiza todas as contas Crowtado cadastradas. Contas Claru são ignoradas."));
  _migrationReport = new QPushButton(QStringLiteral("Ver relatório"));
  _migrationReport->setEnabled(false);
  connect(_accountsMigrate, &QPushButton::clicked, this, &MainWindow::startOrgMigration);
  connect(_migrationReport, &QPushButton::clicked, this, &MainWindow::showOrgMigrationReport);
  migrationActions->addWidget(_accountsMigrate);
  migrationActions->addWidget(_migrationReport);
  migrationActions->addStretch();
  accountsLayout->addLayout(migrationActions);
  _migrationStatus = quietLabel(QStringLiteral("Crowtado: organização nova obrigatória. Claru: mantida sem troca de código."));
  _migrationStatus->setWordWrap(true);
  accountsLayout->addWidget(_migrationStatus);
  _orgMigrationPoll.setInterval(1200);
  connect(&_orgMigrationPoll, &QTimer::timeout, this, &MainWindow::pollOrgMigration);
  accountsLayout->addWidget(quietLabel(QStringLiteral(
      "Backup JSON com credenciais de acesso. Guarde em local seguro. Use Ctrl ou Shift para selecionar contas.")));
  accountsLayout->addWidget(_accountsTable, 1);
  auto* accountsScroll = new QScrollArea;
  accountsScroll->setWidgetResizable(true);
  accountsScroll->setFrameShape(QFrame::NoFrame);
  accountsScroll->setWidget(card(QStringLiteral("Contas cadastradas"), accountsBody));
  tabs->insertTab(0, accountsScroll, QStringLiteral("Contas cadastradas"));
  tabs->setCurrentIndex(0);
  return pageShell(QStringLiteral("Contas"),
                   QStringLiteral("Gerencie as identidades usadas no Minute e valide cada acesso."), body);
}

