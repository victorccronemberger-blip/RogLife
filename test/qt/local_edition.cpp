#include "../../desktop/src/MainWindow.hpp"
#include "../../desktop/src/CampaignReviewDialog.hpp"
#include <QCheckBox>
#include <QApplication>
#include <QComboBox>
#include <QDoubleSpinBox>
#include <QDialog>
#include <QDialogButtonBox>
#include <QLabel>
#include <QElapsedTimer>
#include <QMessageBox>
#include <QMenu>
#include <QProgressBar>
#include <QRegularExpression>
#include <QSpinBox>
#include <QStackedWidget>
#include <QTableWidget>
#include <QTabWidget>
#include <QUrlQuery>
#include <QEventLoop>
#include <QJsonObject>
#include <QListWidget>
#include <QPushButton>
#include <QSettings>
#include <QTcpServer>
#include <QTcpSocket>
#include <QTemporaryDir>
#include <QTimer>
#include <QFile>
#include <QLineEdit>
#include <QThread>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <memory>
#include <oclero/qlementine/style/QlementineStyle.hpp>

class LocalEditionTests {
public:
#include "local_nymeria_qa.inc"
#include "local_on_demand_qa.inc"
#include "local_catalog_progress_qa.inc"
#include "local_registration_qa.inc"
#include "local_recovery_qa.inc"
#include "local_claru_qa.inc"
  static bool backendOutputStatusSmoke() {
    auto* style = new oclero::qlementine::QlementineStyle;
    qApp->setStyle(style);
    MainWindow window(style, nullptr, false);
    window.show();
    window._status->setText(QStringLiteral("Serviço local pronto."));
    window._backend.disconnect(&window);
    window.drainBackendOutput();
    auto readyReadCount = std::make_shared<int>(0);
    QObject::connect(&window._backend, &QProcess::readyReadStandardOutput,
                     &window, [readyReadCount] { ++*readyReadCount; });
    QEventLoop loop;
    bool passed = false;
    QObject::connect(&window._backend,
                     qOverload<int, QProcess::ExitStatus>(&QProcess::finished),
                     &loop, [&window, readyReadCount, &passed, &loop](int exitCode, QProcess::ExitStatus status) {
      passed = status == QProcess::NormalExit && exitCode == 0 && *readyReadCount >= 2
          && window._status->text() == QStringLiteral("Serviço local pronto.");
      loop.quit();
    });
    QTimer::singleShot(4000, &loop, &QEventLoop::quit);
    window._backend.start(QCoreApplication::applicationFilePath(),
                          {QStringLiteral("--emit-fragmented-backend-log")});
    loop.exec();
    return passed;
  }

  static bool shell() {
    auto* style = new oclero::qlementine::QlementineStyle;
    qApp->setStyle(style);
    MainWindow window(style, nullptr, false);
    window.show();
    qApp->processEvents();
    QEventLoop layout;
    QTimer::singleShot(150, &layout, &QEventLoop::quit);
    layout.exec();
    const bool valid = window._backend.state() == QProcess::NotRunning
        && window._campaignPause && window._campaignTasks && window._accountsTable
        && window._nymeriaImport && window._dataset->findData("nymeria") >= 0
        && window._dataset->findData("ambos") >= 0;
    window.grab().save(QStringLiteral("local-edition-preview.png"));
    window.close();
    return valid;
  }
  static bool campaignHoursMode() {
    auto* style = new oclero::qlementine::QlementineStyle;
    qApp->setStyle(style);
    MainWindow window(style, nullptr, false);
    if (!window._targetHours || !window._runUntilExhausted || window._runUntilExhausted->isChecked()
        || !window._targetHours->isEnabled())
      return false;
    window._runUntilExhausted->setChecked(true);
    if (window._targetHours->isEnabled()) return false;
    const QJsonObject request{{"run_until_exhausted", true}, {"target_hours", 0.0}};
    return CampaignReviewDialog::requestedSeconds({}, request) == 0
        && CampaignReviewDialog::capacityAllowsStart({}, request);
  }
  static bool protocol(const QString& path, bool post, bool mutation = false) {
    QTcpServer server;
    if (!server.listen(QHostAddress::LocalHost, 0)) return false;
    int calls = 0;
    QList<QByteArray> requests;
    QObject::connect(&server, &QTcpServer::newConnection, &server, [&] {
      auto* socket = server.nextPendingConnection();
      auto bytes = std::make_shared<QByteArray>();
      auto answered = std::make_shared<bool>(false);
      QObject::connect(socket, &QTcpSocket::readyRead, socket, [&, socket, bytes, answered] {
        bytes->append(socket->readAll());
        const int headerEnd = bytes->indexOf("\r\n\r\n");
        if (*answered || headerEnd < 0) return;
        int length = 0;
        for (const auto& header : bytes->left(headerEnd).split('\n'))
          if (header.trimmed().toLower().startsWith("content-length:"))
            length = header.mid(header.indexOf(':') + 1).trimmed().toInt();
        if (bytes->size() < headerEnd + 4 + length) return;
        *answered = true;
        requests.append(*bytes); ++calls;
        const QByteArray body = mutation ? QByteArray("{\"error\":\"unknown\"}")
            : calls == 1 ? QByteArray("{\"loading\":true,\"job_id\":\"fixture-job\"}")
                         : QByteArray("{\"ok\":true,\"tasks\":[]}");
        const QByteArray code = mutation ? "500 Internal Server Error" : calls == 1 ? "202 Accepted" : "200 OK";
        socket->write("HTTP/1.1 " + code + "\r\nContent-Type: application/json\r\nContent-Length: "
                      + QByteArray::number(body.size()) + "\r\nConnection: close\r\n\r\n" + body);
        socket->disconnectFromHost();
      });
      QObject::connect(socket, &QTcpSocket::disconnected, socket, &QObject::deleteLater);
    });
    ApiClient api;
    api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    QEventLoop loop;
    bool completed = false;
    ApiClient::Callback done = [&](bool ok, const QJsonDocument& doc, const QString&) {
      completed = mutation ? !ok && doc.object().value("error_code") == QStringLiteral("request_outcome_unknown")
                           : ok && doc.object().value("ok") == QJsonValue(true);
      loop.quit();
    };
    if (post) api.post(path, {{QStringLiteral("fixture"), true}}, done);
    else api.get(path, done);
    QTimer::singleShot(6000, &loop, &QEventLoop::quit);
    loop.exec();
    if (!completed || calls != (mutation ? 1 : 2)) return false;
    if (!mutation && post && requests[0].split('\n')[0] != requests[1].split('\n')[0]) return false;
    if (!mutation && !post && !requests[1].contains("job_id=fixture-job")) return false;
    return true;
  }
};

int main(int argc, char** argv) {
  QApplication app(argc, argv);
  if (app.arguments().contains("--emit-fragmented-backend-log")) {
    const char* first = "127.0.0.1 - - [08/Oct/2026 18:28:22] \"GET /api/accounts HTTP/1.";
    const char* second = "1\" 400 -\n";
    std::fwrite(first, 1, std::strlen(first), stdout);
    std::fflush(stdout);
    QThread::msleep(150);
    std::fwrite(second, 1, std::strlen(second), stdout);
    std::fflush(stdout);
    return 0;
  }
  QTemporaryDir settings;
  QSettings::setDefaultFormat(QSettings::IniFormat);
  QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, settings.path());
  app.setOrganizationName("LocalEditionFixture"); app.setApplicationName("LocalEditionFixture");
  if (app.arguments().contains("--backend-output-status-local"))
    return LocalEditionTests::backendOutputStatusSmoke() ? 0 : 1;
  if (app.arguments().contains("--registration-local")) return LocalEditionTests::registrationSmoke() ? 0 : 1;
  if (app.arguments().contains("--registration-rate-limit-local")) return LocalEditionTests::registrationRateLimitSmoke() ? 0 : 1;
  if (app.arguments().contains("--registration-recovery-local")) return LocalEditionTests::registrationRecoverySmoke() ? 0 : 1;
  if (app.arguments().contains("--claru-admission-recovery-local")) return LocalEditionTests::claruAdmissionRecoverySmoke() ? 0 : 1;
  if (app.arguments().contains("--claru-checkpoint-recovery-local")) return LocalEditionTests::claruCheckpointRecoverySmoke() ? 0 : 1;
  if (app.arguments().contains("--claru-connection-generation-local")) return LocalEditionTests::claruConnectionGenerationSmoke() ? 0 : 1;
  if (app.arguments().contains("--campaign-start-recovery-local")) return LocalEditionTests::campaignStartRecoverySmoke() ? 0 : 1;
  if (app.arguments().contains("--campaign-hours-local")) return LocalEditionTests::campaignHoursMode() ? 0 : 1;
  if (app.arguments().contains("--claru-local")) return LocalEditionTests::claruSmoke() ? 0 : 1;
  if (app.arguments().contains("--catalog-progress-local")) {
    const bool preflight = app.arguments().contains("--preflight-recovery");
    return LocalEditionTests::catalogLimits() && LocalEditionTests::catalogLatestSelection()
        && LocalEditionTests::catalogProgressProtocol(
        preflight, app.arguments().contains("--exhaust"), app.arguments().contains("--idle")) ? 0 : 1;
  }
  if (app.arguments().contains("--catalog-monitor-local"))
    return LocalEditionTests::catalogMonitorSmoke() ? 0 : 1;
  if (app.arguments().contains("--provider-catalog-local"))
    return LocalEditionTests::providerCatalogPageSmoke() ? 0 : 1;
  if (app.arguments().contains("--campaign-verification-monitor-local"))
    return LocalEditionTests::campaignVerificationMonitorSmoke() ? 0 : 1;
  if (app.arguments().contains("--nymeria-local") || app.arguments().contains("--demand-local")) {
    auto* style = new oclero::qlementine::QlementineStyle;
    app.setStyle(style);
    MainWindow window(style, nullptr, false);
    window.show();
    QTimer::singleShot(0, &window, [&window] {
      if (qApp->arguments().contains("--demand-local")) LocalEditionTests::campaignOnDemandSmoke(window);
      else LocalEditionTests::nymeriaLibrarySmoke(window);
    });
    return app.exec();
  }
  const bool passed = LocalEditionTests::protocol("/api/tasks?email=fixture%40example.invalid", false)
      && LocalEditionTests::protocol("/api/integrations/ego4d/catalog", true)
      && LocalEditionTests::protocol("/api/campaigns/preflight", true)
      && LocalEditionTests::protocol("/api/campaigns", true, true)
      && LocalEditionTests::shell();
  std::cout << (passed ? "Local edition contracts passed\n" : "Local edition contracts failed\n");
  return passed ? 0 : 1;
}
