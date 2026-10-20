#pragma once
#include <QUuid>
#include <algorithm>
#include <cmath>
#include <utility>
#include "MainWindow.hpp"
#include "DevelopmentProjectRoot.hpp"
#include "ComboBox.hpp"
#include "OwnedServiceProcesses.hpp"
#include "CampaignReviewDialog.hpp"
#include "LibraryRootSelection.hpp"
#include "PackagedServiceEnvironment.hpp"

#include <QApplication>
#include <QCheckBox>
#include <QClipboard>
#include <QCloseEvent>
#include <QColor>
#include <QComboBox>
#include <QCryptographicHash>
#include <QDateTime>
#include <QDebug>
#include <QDesktopServices>
#include <QDir>
#include <QDirIterator>
#include <QDialog>
#include <QDialogButtonBox>
#include <QDoubleSpinBox>
#include <QFileInfo>
#include <QFile>
#include <QFileDialog>
#include <QFormLayout>
#include <QFrame>
#include <QFontMetrics>
#include <QGroupBox>
#include <QHash>
#include <QHeaderView>
#include <QHBoxLayout>
#include <QInputDialog>
#include <QJsonDocument>
#include <QJsonArray>
#include <QJsonObject>
#include <QJsonValue>
#include <QLabel>
#include <QLineEdit>
#include <QListWidget>
#include <QLocale>
#include <QMessageBox>
#include <QMenu>
#include <QPlainTextEdit>
#include <QPixmap>
#include <QProgressBar>
#include <QRandomGenerator>
#include <QProcess>
#include <QProcessEnvironment>
#include <QPushButton>
#include <QPointer>
#include <QRegularExpression>
#include <QScrollArea>
#include <QScreen>
#include <QSaveFile>
#include <QSet>
#include <QSettings>
#include <QSizePolicy>
#include <QSpinBox>
#include <QStackedWidget>
#include <QStandardPaths>
#include <QSignalBlocker>
#include <QTableWidget>
#include <QTabWidget>
#include <QTimer>
#include <QUrl>
#include <QUrlQuery>
#include <QVBoxLayout>

#include <oclero/qlementine/style/QlementineStyle.hpp>

#ifdef Q_OS_WIN
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <tlhelp32.h>
#endif


namespace {

bool confirmWithdrawal(QWidget* parent, int count, QJsonObject& request) {
  QDialog dialog(parent);
  dialog.setWindowTitle(QStringLiteral("Confirmar saque"));
  dialog.resize(620, 510);
  auto* layout = new QVBoxLayout(&dialog);
  layout->setContentsMargins(20, 18, 20, 18);
  layout->setSpacing(12);
  auto* kicker = new QLabel(QStringLiteral("CARTEIRA / CONFIRMAÇÃO"), &dialog);
  kicker->setObjectName(QStringLiteral("kicker"));
  kicker->hide();
  auto* title = new QLabel(QStringLiteral("Revise seu saque"), &dialog);
  title->setObjectName(QStringLiteral("reviewTitle"));
  layout->addWidget(title);
  auto* help = new QLabel(QStringLiteral("Solicitar saque para %1 conta(s) elegível(is)?").arg(count));
  help->setWordWrap(true);
  layout->addWidget(help);
  auto* form = new QFormLayout;
  form->setFieldGrowthPolicy(QFormLayout::AllNonFixedFieldsGrow);
  form->setRowWrapPolicy(QFormLayout::WrapLongRows);
  auto* method = new ComboBox(&dialog);
  method->addItem(QStringLiteral("Método já configurado"), QStringLiteral("saved"));
  method->addItem(QStringLiteral("Wise"), QStringLiteral("wise"));
  method->addItem(QStringLiteral("PayPal"), QStringLiteral("paypal"));
  auto* name = new QLineEdit;
  name->setPlaceholderText(QStringLiteral("Nome completo do titular"));
  name->setMaxLength(200);
  auto* email = new QLineEdit;
  email->setPlaceholderText(QStringLiteral("E-mail cadastrado na Wise"));
  email->setMaxLength(254);
  form->addRow(QStringLiteral("Método"), method);
  form->addRow(QStringLiteral("Nome legal na Wise"), name);
  form->addRow(QStringLiteral("E-mail da Wise"), email);
  layout->addLayout(form);
  auto* explanation = new QLabel;
  explanation->setWordWrap(true);
  layout->addWidget(explanation);
  auto* buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel);
  buttons->button(QDialogButtonBox::Ok)->setText(QStringLiteral("Confirmar e solicitar"));
  buttons->button(QDialogButtonBox::Ok)->setProperty("role", QStringLiteral("primary"));
  buttons->button(QDialogButtonBox::Ok)->setAutoDefault(false);
  buttons->button(QDialogButtonBox::Cancel)->setText(QStringLiteral("Voltar"));
  buttons->button(QDialogButtonBox::Cancel)->setDefault(true);
  auto update = [=] {
    const bool wise = method->currentData().toString() == QStringLiteral("wise");
    name->setEnabled(wise);
    email->setEnabled(wise);
    name->setVisible(wise);
    email->setVisible(wise);
    form->labelForField(name)->setVisible(wise);
    form->labelForField(email)->setVisible(wise);
    explanation->setText(wise
        ? QStringLiteral("Uma conta por vez: vincular Wise → solicitar saque → desvincular Wise → voltar para Dots. "
                         "A limpeza é tentada mesmo em caso de erro. Novos saques ficam bloqueados até confirmá-la.")
        : method->currentData().toString() == QStringLiteral("paypal")
            ? QStringLiteral("Seleciona PayPal na Crowtado e solicita uma conta por vez. "
                             "Siga as instruções de pagamento enviadas pela plataforma. Não usa o e-mail da Wise.")
            : QStringLiteral("Usa o método salvo na Crowtado. No Dots, conclua pelo link da própria conta."));
    buttons->button(QDialogButtonBox::Ok)->setEnabled(!wise ||
        (name->text().trimmed().size() >= 2 && QRegularExpression(
            QStringLiteral("^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$"))
                .match(email->text().trimmed()).hasMatch()));
  };
  QObject::connect(method, QOverload<int>::of(&QComboBox::currentIndexChanged), &dialog, [=](int) { update(); });
  QObject::connect(name, &QLineEdit::textChanged, &dialog, [=](const QString&) { update(); });
  QObject::connect(email, &QLineEdit::textChanged, &dialog, [=](const QString&) { update(); });
  QObject::connect(buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
  QObject::connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
  layout->addStretch();
  layout->addWidget(buttons);
  update();
  if (dialog.exec() != QDialog::Accepted) return false;
  if (method->currentData().toString() == QStringLiteral("wise")) {
    request.insert(QStringLiteral("method"), QStringLiteral("wise"));
    request.insert(QStringLiteral("wise_confirmed"), true);
    request.insert(QStringLiteral("legal_name"), name->text().trimmed());
    request.insert(QStringLiteral("destination_email"), email->text().trimmed());
  } else if (method->currentData().toString() == QStringLiteral("paypal")) {
    request.insert(QStringLiteral("method"), QStringLiteral("paypal"));
    request.insert(QStringLiteral("paypal_confirmed"), true);
  }
  return true;
}

QString provisionEmbeddedService() {
#if defined(Q_OS_WIN) && QMONEY_HAS_EMBEDDED_SERVICE
  HMODULE module = GetModuleHandleW(nullptr);
  HRSRC resource = FindResourceW(module, MAKEINTRESOURCEW(201), RT_RCDATA);
  if (!resource) return {};
  HGLOBAL loaded = LoadResource(module, resource);
  const DWORD size = SizeofResource(module, resource);
  const void* bytes = loaded ? LockResource(loaded) : nullptr;
  if (!bytes || size == 0) return {};

  const QString runtime = QStandardPaths::writableLocation(
                              QStandardPaths::GenericDataLocation)
                          + QStringLiteral("/QMoney/runtime");
  QDir().mkpath(runtime);
  const QString target = runtime + QStringLiteral("/QMoneyService-%1.exe")
                                       .arg(QCoreApplication::applicationVersion());
  const QByteArray expected = QCryptographicHash::hash(
      QByteArrayView(static_cast<const char*>(bytes), size),
      QCryptographicHash::Sha256);

  QFile existing(target);
  if (existing.size() == static_cast<qint64>(size)
      && existing.open(QIODevice::ReadOnly)) {
    QCryptographicHash actual(QCryptographicHash::Sha256);
    if (actual.addData(&existing) && actual.result() == expected) return target;
  }

  QSaveFile output(target);
  if (!output.open(QIODevice::WriteOnly)
      || output.write(static_cast<const char*>(bytes), size) != size
      || !output.commit()) {
    qWarning() << "QMoney: não foi possível disponibilizar o motor incorporado";
    return {};
  }
  qInfo() << "QMoney: motor incorporado preparado para"
          << QCoreApplication::applicationVersion();
  return target;
#else
  return {};
#endif
}

void terminateOwnedServiceTree(qint64 rootPid, const QString& expectedExecutable) {
#ifdef Q_OS_WIN
  if (rootPid <= 0 || expectedExecutable.isEmpty()) return;
  auto identity = [](HANDLE process, quint32 pid, quint32 parent) {
    qmoney::service::ProcessFact fact;
    fact.pid = pid;
    fact.parentPid = parent;
    wchar_t path[32768];
    DWORD length = DWORD(std::size(path));
    FILETIME created{}, exited{}, kernel{}, user{};
    HANDLE token = nullptr;
    if (!QueryFullProcessImageNameW(process, 0, path, &length)
        || !GetProcessTimes(process, &created, &exited, &kernel, &user)
        || !OpenProcessToken(process, TOKEN_QUERY, &token)) return fact;
    DWORD bytes = 0;
    GetTokenInformation(token, TokenUser, nullptr, 0, &bytes);
    QByteArray data(int(bytes), Qt::Uninitialized);
    const bool ownerKnown = bytes > 0 && GetTokenInformation(
        token, TokenUser, data.data(), bytes, &bytes);
    CloseHandle(token);
    if (!ownerKnown) return fact;
    auto* owner = reinterpret_cast<TOKEN_USER*>(data.data());
    if (!IsValidSid(owner->User.Sid)) return fact;
    fact.owner = QByteArray(static_cast<const char*>(owner->User.Sid),
                           int(GetLengthSid(owner->User.Sid)));
    fact.executable = QDir::cleanPath(QDir::fromNativeSeparators(
        QString::fromWCharArray(path, int(length))));
    fact.created = (quint64(created.dwHighDateTime) << 32) | created.dwLowDateTime;
    fact.identityKnown = true;
    return fact;
  };
  const quint32 appPid = GetCurrentProcessId();
  const auto app = identity(GetCurrentProcess(), appPid, 0);
  const QString expected = QDir::cleanPath(QDir::fromNativeSeparators(
      QFileInfo(expectedExecutable).canonicalFilePath()));
  HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
  if (snapshot == INVALID_HANDLE_VALUE || !app.identityKnown || expected.isEmpty()) {
    if (snapshot != INVALID_HANDLE_VALUE) CloseHandle(snapshot);
    qWarning() << "QMoney: árvore do motor preservada; propriedade não comprovada";
    return;
  }
  PROCESSENTRY32W entry{};
  entry.dwSize = sizeof(entry);
  QVector<qmoney::service::ProcessFact> facts;
  if (Process32FirstW(snapshot, &entry)) {
    do {
      HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, entry.th32ProcessID);
      auto fact = process ? identity(process, entry.th32ProcessID, entry.th32ParentProcessID)
                          : qmoney::service::ProcessFact{};
      if (!process) { fact.pid = entry.th32ProcessID; fact.parentPid = entry.th32ParentProcessID; }
      facts.append(fact);
      if (process) CloseHandle(process);
    } while (Process32NextW(snapshot, &entry));
  }
  CloseHandle(snapshot);
  const auto selected = qmoney::service::selectOwnedServiceTree(
      facts, quint32(rootPid), appPid, app.created, app.owner, expected);
  if (selected.isEmpty()) {
    qWarning() << "QMoney: árvore do motor preservada; propriedade não comprovada";
    return;
  }
  for (const auto& fact : selected) {
    HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE
                                 | SYNCHRONIZE, FALSE, fact.pid);
    if (!process) continue;
    // Revalidate the open handle after selection: PID reuse must never turn
    // a previously owned process into permission to terminate its successor.
    const auto now = identity(process, fact.pid, fact.parentPid);
    if (now.identityKnown && now.created == fact.created && now.owner == fact.owner
        && now.executable.compare(fact.executable, Qt::CaseInsensitive) == 0) {
      if (TerminateProcess(process, 0)) WaitForSingleObject(process, 3000);
    }
      CloseHandle(process);
  }
#else
  Q_UNUSED(rootPid)
  Q_UNUSED(expectedExecutable)
#endif
}

QString jsonId(const QJsonValue& value) {
  if (value.isString()) return value.toString();
  if (value.isDouble()) return QString::number(value.toDouble(), 'f', 0);
  return value.toVariant().toString();
}

QString bytesText(qint64 bytes) {
  const double gib = static_cast<double>(bytes) / (1024.0 * 1024.0 * 1024.0);
  if (gib >= 1.0) return QLocale().toString(gib, 'f', gib >= 100.0 ? 0 : 1) + QStringLiteral(" GiB");
  const double mib = static_cast<double>(bytes) / (1024.0 * 1024.0);
  return QLocale().toString(mib, 'f', 1) + QStringLiteral(" MiB");
}

QTableWidgetItem* cell(const QString& text) {
  auto* item = new QTableWidgetItem(text);
  item->setFlags(item->flags() & ~Qt::ItemIsEditable);
  item->setToolTip(text);
  return item;
}

QLabel* quietLabel(const QString& text) {
  auto* label = new QLabel(text);
  label->setObjectName(QStringLiteral("quiet"));
  label->setWordWrap(true);
  return label;
}

void configureCombo(QComboBox* combo, int minimumWidth = 360) {
  combo->setMinimumWidth(qMin(minimumWidth, 240));
  combo->setMinimumHeight(38);
  combo->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
  combo->setMaxVisibleItems(8);
  combo->setSizeAdjustPolicy(QComboBox::AdjustToMinimumContentsLengthWithIcon);
  combo->setMinimumContentsLength(14);
  combo->view()->setTextElideMode(Qt::ElideRight);
  combo->view()->setHorizontalScrollBarPolicy(Qt::ScrollBarAsNeeded);
  combo->view()->setVerticalScrollBarPolicy(Qt::ScrollBarAsNeeded);
  combo->view()->setVerticalScrollMode(QAbstractItemView::ScrollPerItem);
}

void configureTable(QTableWidget* table) {
  table->setWordWrap(false);
  table->setTextElideMode(Qt::ElideRight);
  table->setHorizontalScrollMode(QAbstractItemView::ScrollPerPixel);
  table->setVerticalScrollMode(QAbstractItemView::ScrollPerPixel);
  table->setHorizontalScrollBarPolicy(Qt::ScrollBarAsNeeded);
  table->setVerticalScrollBarPolicy(Qt::ScrollBarAsNeeded);
  table->setAlternatingRowColors(true);
  table->setShowGrid(false);
  table->verticalHeader()->setVisible(false);
  table->verticalHeader()->setSectionResizeMode(QHeaderView::Fixed);
  table->verticalHeader()->setDefaultSectionSize(48);
  table->verticalHeader()->setMinimumSectionSize(48);
  table->horizontalHeader()->setMinimumHeight(42);
  table->horizontalHeader()->setMinimumSectionSize(92);
  table->horizontalHeader()->setDefaultAlignment(Qt::AlignLeft | Qt::AlignVCenter);
  table->horizontalHeader()->setStretchLastSection(false);
}

void configureForm(QFormLayout* form) {
  form->setFieldGrowthPolicy(QFormLayout::AllNonFixedFieldsGrow);
  form->setRowWrapPolicy(QFormLayout::WrapLongRows);
  form->setLabelAlignment(Qt::AlignLeft | Qt::AlignVCenter);
  form->setFormAlignment(Qt::AlignTop);
  form->setHorizontalSpacing(18);
  form->setVerticalSpacing(12);
}

}  // namespace
