#include "../../desktop/src/MainWindow.hpp"
#include "../../desktop/src/AndroidDevicePanel.hpp"
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
#include <QStyleFactory>
#include <QTcpServer>
#include <QTcpSocket>
#include <QTemporaryDir>
#include <QTimer>
#include <QFile>
#include <QLineEdit>
#include <QThread>
#include <QDir>
#include <QFileInfo>
#include <QScreen>
#include <QScrollArea>
#include <QScrollBar>
#include <QAbstractButton>
#include <QAbstractSpinBox>
#include <QAbstractItemView>
#include <QLayout>
#include <QPixmap>
#include <QSignalBlocker>
#include <QFontMetrics>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <memory>
#include <oclero/qlementine/style/QlementineStyle.hpp>

class LocalEditionTests {
public:
#include "frontend_layout_qa.inc"
  static int androidDeviceCampaignPreview() {
    const auto arguments = qApp->arguments();
    const int snapshotIndex = arguments.indexOf(QStringLiteral("--snapshot"));
    QFile source(arguments.value(snapshotIndex + 1));
    if (snapshotIndex < 0 || !source.open(QIODevice::ReadOnly) || source.size() > 65536) return 2;
    const auto snapshot = QJsonDocument::fromJson(source.readAll());
    if (!snapshot.isObject()) return 2;
    QTcpServer fixture;
    if (!fixture.listen(QHostAddress::LocalHost, 0)) return 2;
    QObject::connect(&fixture, &QTcpServer::newConnection, &fixture, [&fixture, snapshot] {
      while (fixture.hasPendingConnections()) {
        auto* socket = fixture.nextPendingConnection();
        QObject::connect(socket, &QTcpSocket::readyRead, socket, [socket, snapshot] {
          const auto bytes = socket->property("qaBytes").toByteArray() + socket->readAll();
          socket->setProperty("qaBytes", bytes);
          if (!bytes.contains("\r\n\r\n") || socket->property("qaAnswered").toBool()) return;
          socket->setProperty("qaAnswered", true);
          const auto request = bytes.left(bytes.indexOf("\r\n")).split(' ');
          const auto path = request.value(1).split('?').first();
          QJsonObject body;
          if (path == "/api/integrations/android/device") body = snapshot.object();
          else if (path == "/api/accounts") body = {{"accounts", QJsonArray{}}};
          else if (path == "/api/campaigns/current")
            body = {{"state", "idle"}, {"totals", QJsonObject{}}, {"events", QJsonArray{}}};
          else if (path == "/api/accounts/bulk-register/status")
            body = {{"state", "idle"}, {"request_id", ""}, {"total", 0}, {"completed", 0},
                    {"created", 0}, {"failed", 0}, {"stop_reason", ""}, {"results", QJsonArray{}}};
          const bool readOnly = request.value(0) == "GET";
          const auto json = QJsonDocument(body).toJson(QJsonDocument::Compact);
          socket->write(QByteArray("HTTP/1.1 ") + (readOnly ? "200 OK" : "405 Method Not Allowed")
              + "\r\nContent-Type: application/json\r\nContent-Length: " + QByteArray::number(json.size())
              + "\r\nConnection: close\r\n\r\n" + json);
          socket->disconnectFromHost();
        });
        QObject::connect(socket, &QTcpSocket::disconnected, socket, &QObject::deleteLater);
      }
    });
    auto* style = new oclero::qlementine::QlementineStyle;
    qApp->setStyle(style);
    qApp->setApplicationVersion(QStringLiteral("1.0.63"));
    MainWindow window(style, nullptr, false);
    window.setWindowTitle(QStringLiteral("QFar — Campanha — QA"));
    window._api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(fixture.serverPort()));
    window.setBackendReady(true);
    window._backendState->setText(QStringLiteral("● Perfil de QA · dados públicos capturados"));
    window._navigation->setCurrentRow(3);
    window.show();
    QTimer::singleShot(120000, qApp, &QApplication::quit);
    return qApp->exec();
  }
  static bool androidDeviceCampaignLifecycle() {
    auto* style = new oclero::qlementine::QlementineStyle;
    qApp->setStyle(style);
    MainWindow window(style, nullptr, false);
    auto* panel = window._campaignDevicePanel;
    if (!panel || !window._pages->widget(3)->isAncestorOf(panel)) return false;
    auto* refresh = panel->findChild<QPushButton*>(QStringLiteral("androidRefreshButton"));
    auto* timer = panel->findChild<QTimer*>(QStringLiteral("androidRefreshTimer"));
    if (!refresh || !timer || refresh->isEnabled() || timer->isActive()) return false;
    // Keep the window hidden: availability dispatch should update the panel
    // without launching any HTTP request or touching a user device.
    window.onCampaignBackendAvailabilityChanged(true, true);
    if (!refresh->isEnabled() || timer->isActive()) return false;
    window.onCampaignBackendAvailabilityChanged(false, true);
    if (refresh->isEnabled() || timer->isActive()) return false;
    window.onCampaignBackendAvailabilityChanged(true, true);
    window.shutdownCampaignTimers();
    return !refresh->isEnabled() && !timer->isActive();
  }
#include "local_nymeria_qa.inc"
#include "local_on_demand_qa.inc"
#include "local_catalog_progress_qa.inc"
#include "local_registration_qa.inc"
#include "local_recovery_qa.inc"
#include "local_claru_qa.inc"
#include "local_balances_qa.inc"
#include "local_live_refresh_qa.inc"
#include "local_provider_catalog_live_qa.inc"
#include "local_passive_data_qa.inc"
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
  static bool campaignAuthenticationPause() {
    QTcpServer fixture;
    if (!fixture.listen(QHostAddress::LocalHost, 0)) return false;
    QObject::connect(&fixture, &QTcpServer::newConnection, &fixture, [&fixture] {
      auto* socket = fixture.nextPendingConnection();
      QObject::connect(socket, &QTcpSocket::readyRead, socket, [socket] {
        auto bytes = socket->property("request").toByteArray() + socket->readAll();
        socket->setProperty("request", bytes);
        if (!bytes.contains("\r\n\r\n") || socket->property("answered").toBool()) return;
        socket->setProperty("answered", true);
        const QJsonObject snapshot{{"state", "running"}, {"pause_requested", true},
            {"stage", QStringLiteral("Aguardando autenticação")},
            {"current", QStringLiteral("Progresso salvo. Corrija o acesso externamente e clique em Retomar.")},
            {"events", QJsonArray{}}, {"totals", QJsonObject{{"total_sends", 2}, {"ok_sends", 1}}}};
        const auto data = QJsonDocument(snapshot).toJson(QJsonDocument::Compact);
        socket->write("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: "
            + QByteArray::number(data.size()) + "\r\n\r\n" + data);
        socket->disconnectFromHost();
      });
      QObject::connect(socket, &QTcpSocket::disconnected, socket, &QObject::deleteLater);
    });
    auto* style = new oclero::qlementine::QlementineStyle;
    qApp->setStyle(style);
    MainWindow window(style, nullptr, false);
    window._api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(fixture.serverPort()));
    window.pollCampaign();
    QElapsedTimer clock; clock.start();
    while (!window._campaignPaused && clock.elapsed() < 5000)
      qApp->processEvents(QEventLoop::AllEvents, 20);
    return window._campaignPaused && window._campaignPause->isEnabled()
        && window._campaignPause->text() == QStringLiteral("Retomar")
        && window._campaignStop->isEnabled()
        && window._campaignStage->text() == QStringLiteral("Aguardando autenticação")
        && window._campaignCurrent->text().contains(QStringLiteral("Corrija o acesso externamente"));
  }
  static bool appCheckIntegrationState() {
    auto* style = new oclero::qlementine::QlementineStyle;
    qApp->setStyle(style);
    MainWindow window(style, nullptr, false);
    auto* status = window.findChild<QLabel*>(QStringLiteral("appCheckStatus"));
    auto* recheck = window.findChild<QPushButton*>(QStringLiteral("appCheckRecheck"));
    auto* refresh = window.findChild<QPushButton*>(QStringLiteral("appCheckRefresh"));
    if (!status || !recheck || !refresh || !refresh->isEnabled()) return false;

    window.renderAppCheckSnapshot({{"configuration_present", false}, {"configured", false}});
    if (recheck->isEnabled() || !status->text().contains(QStringLiteral("não está configurado")))
      return false;
    window.renderAppCheckSnapshot({{"configuration_present", true}, {"configured", false}});
    if (recheck->isEnabled() || !status->text().contains(QStringLiteral("incompleta")))
      return false;
    window.renderAppCheckSnapshot({{"configuration_present", true}, {"configured", true},
                                   {"token_available", false}, {"retry_after_seconds", 0}});
    if (!recheck->isEnabled() || !status->text().contains(QStringLiteral("não há token")))
      return false;
    window.renderAppCheckSnapshot({{"configuration_present", true}, {"configured", true},
                                   {"token_available", false}, {"retry_after_seconds", 0},
                                   {"diagnostic", QJsonObject{{"message", "Permission denied"},
                                                               {"http_status", 403},
                                                               {"firebase_status", "PERMISSION_DENIED"}}}});
    if (!status->text().contains(QStringLiteral("Firebase HTTP 403 / PERMISSION_DENIED")))
      return false;
    window.renderAppCheckSnapshot({{"configuration_present", true}, {"configured", true},
                                   {"token_available", true}, {"retry_after_seconds", 0}});
    if (!recheck->isEnabled()
        || !status->text().contains(QStringLiteral("aceite pelo Minute ainda não foi confirmado")))
      return false;
    window.renderAppCheckSnapshot({{"configuration_present", true}, {"configured", true},
                                   {"retry_after_seconds", 9}});
    if (recheck->isEnabled() || !status->text().contains(QStringLiteral("Aguarde 9 s"))
        || !status->text().contains(QStringLiteral("use Atualizar conexões")))
      return false;
    window.renderAppCheckSnapshot({{"configuration_present", true}, {"configured", true},
                                   {"paused", true}, {"retry_after_seconds", 0}});
    return !recheck->isEnabled()
        && status->text().contains(QStringLiteral("Rechecagem pausada"));
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
  if (app.arguments().contains("--frontend-layout-local")) {
    const int outputArg = app.arguments().indexOf("--output-dir");
    return LocalEditionTests::frontendLayoutAudit(outputArg < 0 ? QString{} : app.arguments().value(outputArg + 1)) ? 0 : 1;
  }
  if (app.arguments().contains("--frontend-preview-local"))
    return LocalEditionTests::frontendLayoutPreview();
  if (app.arguments().contains("--android-device-preview-local"))
    return LocalEditionTests::androidDeviceCampaignPreview();
  if (app.arguments().contains("--backend-output-status-local"))
    return LocalEditionTests::backendOutputStatusSmoke() ? 0 : 1;
  if (app.arguments().contains("--android-device-local"))
    return LocalEditionTests::androidDeviceCampaignLifecycle() ? 0 : 1;
  if (app.arguments().contains("--registration-local")) return LocalEditionTests::registrationSmoke() ? 0 : 1;
  if (app.arguments().contains("--registration-rate-limit-local")) return LocalEditionTests::registrationRateLimitSmoke() ? 0 : 1;
  if (app.arguments().contains("--registration-recovery-local")) return LocalEditionTests::registrationRecoverySmoke() ? 0 : 1;
  if (app.arguments().contains("--claru-admission-recovery-local")) return LocalEditionTests::claruAdmissionRecoverySmoke() ? 0 : 1;
  if (app.arguments().contains("--claru-checkpoint-recovery-local")) return LocalEditionTests::claruCheckpointRecoverySmoke() ? 0 : 1;
  if (app.arguments().contains("--claru-connection-generation-local")) return LocalEditionTests::claruConnectionGenerationSmoke() ? 0 : 1;
  if (app.arguments().contains("--campaign-start-recovery-local")) return LocalEditionTests::campaignStartRecoverySmoke() ? 0 : 1;
  if (app.arguments().contains("--campaign-hours-local")) return LocalEditionTests::campaignHoursMode() ? 0 : 1;
  if (app.arguments().contains("--campaign-authentication-local")) return LocalEditionTests::campaignAuthenticationPause() ? 0 : 1;
  if (app.arguments().contains("--appcheck-integration-local"))
    return LocalEditionTests::appCheckIntegrationState() ? 0 : 1;
  if (app.arguments().contains("--balances-local")) return LocalEditionTests::balancesReadingLifecycleSmoke() ? 0 : 1;
  if (app.arguments().contains("--claru-local")) return LocalEditionTests::claruSmoke() ? 0 : 1;
  if (app.arguments().contains("--catalog-progress-local")) {
    const bool preflight = app.arguments().contains("--preflight-recovery");
    return LocalEditionTests::catalogLimits() && LocalEditionTests::catalogLatestSelection()
        && LocalEditionTests::catalogProgressProtocol(
        preflight, app.arguments().contains("--exhaust"), app.arguments().contains("--idle")) ? 0 : 1;
  }
  if (app.arguments().contains("--catalog-monitor-local"))
    return LocalEditionTests::catalogMonitorSmoke() && LocalEditionTests::catalogNavigationReuse() ? 0 : 1;
  if (app.arguments().contains("--provider-catalog-local"))
    return LocalEditionTests::providerCatalogPageSmoke() && LocalEditionTests::providerCatalogLiveStatus() ? 0 : 1;
  if (app.arguments().contains("--live-data-local"))
    return LocalEditionTests::livePageRefreshSmoke() ? 0 : 1;
  if (app.arguments().contains("--passive-data-local"))
    return LocalEditionTests::passiveAccountsAndIntegrationsSmoke() ? 0 : 1;
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
