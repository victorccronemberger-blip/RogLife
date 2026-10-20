#include "../../desktop/src/AndroidDevicePanel.hpp"
#include "../../desktop/src/ApiClient.hpp"
#include "../../desktop/src/UsbDeviceWatcher.hpp"

#include <QApplication>
#include <QComboBox>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLabel>
#include <QNetworkProxy>
#include <QProgressBar>
#include <QPushButton>
#include <QTcpServer>
#include <QTcpSocket>
#include <QTimer>
#include <QUrl>
#include <QUrlQuery>
#include <QVBoxLayout>
#include <functional>
#include <iostream>

namespace {
bool waitUntil(const std::function<bool()>& predicate, int maximumMs = 1500) {
  QElapsedTimer elapsed;
  elapsed.start();
  while (!predicate() && elapsed.elapsed() < maximumMs) {
    QEventLoop loop;
    QTimer::singleShot(5, &loop, &QEventLoop::quit);
    loop.exec();
  }
  return predicate();
}

void processFor(int milliseconds) {
  QEventLoop loop;
  QTimer::singleShot(milliseconds, &loop, &QEventLoop::quit);
  loop.exec();
}

QJsonObject connectedSnapshot(const QString& serial = QStringLiteral("fixture-usb-A"),
                              const QString& model = QStringLiteral("Celular de teste"),
                              const QString& rootState = QStringLiteral("su_available")) {
  return {{"adb_available", true}, {"connection_state", "connected"},
          {"devices", QJsonArray{QJsonObject{{"serial", serial}, {"state", "device"}, {"model", model}}}},
          {"selected_serial", serial},
          {"device", QJsonObject{{"model", model}, {"manufacturer", "Fabricante de teste"},
                                {"android_version", "14"}, {"sdk", "34"}}},
          {"root", QJsonObject{{"state", rootState}, {"message", "Verificação somente de leitura."}}},
          {"collection", QJsonObject{{"state", "done"}, {"message", "Informações públicas lidas."},
                                     {"checked_at", "2026-10-12T15:20:30.123Z"}, {"elapsed_ms", 42}}}};
}

QJsonObject stateSnapshot(const QString& state) {
  return {{"adb_available", state != QStringLiteral("adb_missing")},
          {"connection_state", state}, {"devices", QJsonArray{}},
          {"selected_serial", ""}, {"device", QJsonObject{}},
          {"root", QJsonObject{{"state", "unknown"}, {"message", ""}}},
          {"collection", QJsonObject{{"state", state == QStringLiteral("error") ? "error" : "unavailable"},
                                     {"message", ""}, {"checked_at", ""}, {"elapsed_ms", 0}}}};
}

QJsonObject multipleSnapshot() {
  auto snapshot = stateSnapshot(QStringLiteral("multiple"));
  snapshot.insert("devices", QJsonArray{
      QJsonObject{{"serial", "fixture-usb-A"}, {"state", "device"}, {"model", "Modelo A"}},
      QJsonObject{{"serial", "fixture /B?&+á"}, {"state", "device"}, {"model", "Modelo B"}}});
  return snapshot;
}

class FixtureServer {
public:
  struct Request {
    QPointer<QTcpSocket> socket;
    QByteArray method;
    QByteArray target;
  };
  QTcpServer server;
  QList<Request> requests;
  QList<QPointer<QTcpSocket>> appCheckRequests;
  QList<QPointer<QTcpSocket>> recheckRequests;
  bool deferRecheck{};
  bool deferAppCheck{};
  QJsonObject automaticAppCheck{{"configured", false}, {"token_available", false}};
  bool automatic{};
  int automaticDelayMs{};
  QJsonObject automaticSnapshot{connectedSnapshot()};

  FixtureServer() {
    server.listen(QHostAddress::LocalHost, 0);
    QObject::connect(&server, &QTcpServer::newConnection, &server, [this] {
      while (server.hasPendingConnections()) {
        auto* socket = server.nextPendingConnection();
        QObject::connect(socket, &QTcpSocket::readyRead, &server, [this, socket] {
          const QByteArray bytes = socket->property("fixtureBytes").toByteArray() + socket->readAll();
          socket->setProperty("fixtureBytes", bytes);
          if (!bytes.contains("\r\n\r\n") || socket->property("fixtureCounted").toBool()) return;
          socket->setProperty("fixtureCounted", true);
          const QByteArray line = bytes.left(bytes.indexOf("\r\n"));
          const QList<QByteArray> fields = line.split(' ');
          if (fields.value(0) == "POST" && fields.value(1) == "/api/integrations/appcheck/recheck") {
            recheckRequests.append(socket);
            if (!deferRecheck) respondRecheck(recheckRequests.size() - 1,
                {{"ok", false}, {"message", "Rechecagem indisponível na fixture."},
                 {"appcheck", automaticAppCheck}});
            return;
          }
          if (fields.value(0) == "GET" && fields.value(1) == "/api/integrations/appcheck/status") {
            appCheckRequests.append(socket);
            if (!deferAppCheck) respondAppCheck(appCheckRequests.size() - 1,
                automaticAppCheck);
            return;
          }
          requests.append({socket, fields.value(0), fields.value(1)});
          if (automatic) {
            const int request = requests.size() - 1;
            QTimer::singleShot(automaticDelayMs, &server, [this, request] { respond(request, automaticSnapshot); });
          }
        });
        QObject::connect(socket, &QTcpSocket::disconnected, socket, &QObject::deleteLater);
      }
    });
  }

  void configure(ApiClient& api) const {
    api.setBaseUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
  }

  void respond(int index, const QJsonObject& snapshot, int status = 200) {
    if (index < 0 || index >= requests.size() || !requests.at(index).socket) return;
    auto* socket = requests.at(index).socket.data();
    const QByteArray body = QJsonDocument(snapshot).toJson(QJsonDocument::Compact);
    socket->write("HTTP/1.1 " + QByteArray::number(status)
        + " Fixture\r\nContent-Type: application/json\r\nContent-Length: " + QByteArray::number(body.size())
        + "\r\nConnection: close\r\n\r\n" + body);
    socket->disconnectFromHost();
  }

  void respondAppCheck(int index, const QJsonObject& snapshot, int status = 200) {
    if (index < 0 || index >= appCheckRequests.size() || !appCheckRequests.at(index)) return;
    auto* socket = appCheckRequests.at(index).data();
    const QByteArray body = QJsonDocument([&] { auto status = snapshot; status.insert("device", status.value("device").toObject()); return status; }()).toJson(QJsonDocument::Compact);
    socket->write("HTTP/1.1 " + QByteArray::number(status)
        + " Fixture\r\nContent-Type: application/json\r\nContent-Length: " + QByteArray::number(body.size())
        + "\r\nConnection: close\r\n\r\n" + body);
    socket->disconnectFromHost();
  }

  bool onlyDiagnosticReads() const {
    for (const auto& request : requests) {
      if (request.method != "GET" || request.target.split('?').first() != "/api/integrations/android/device")
        return false;
    }
    return true;
  }

  void respondRecheck(int index, const QJsonObject& result, int status = 200) {
    if (index < 0 || index >= recheckRequests.size() || !recheckRequests.at(index)) return;
    auto* socket = recheckRequests.at(index).data();
    const QByteArray body = QJsonDocument(result).toJson(QJsonDocument::Compact);
    socket->write("HTTP/1.1 " + QByteArray::number(status)
        + " Fixture\r\nContent-Type: application/json\r\nContent-Length: " + QByteArray::number(body.size())
        + "\r\nConnection: close\r\n\r\n" + body);
    socket->disconnectFromHost();
  }
};

template<class Widget> Widget* child(AndroidDevicePanel& panel, const char* name) {
  return panel.findChild<Widget*>(QString::fromLatin1(name));
}

bool require(bool condition, const char* message) {
  if (!condition) std::cerr << message << '\n';
  return condition;
}

bool invokeUsbEvent(AndroidDevicePanel& panel) {
  auto* watcher = child<UsbDeviceWatcher>(panel, "androidUsbWatcher");
  return watcher && QMetaObject::invokeMethod(watcher, "topologyChanged", Qt::DirectConnection);
}

bool appCheckStates() {
  AndroidDevicePanel panel(nullptr);
  auto* status = child<QLabel>(panel, "androidAppCheckStatus");
  auto* provider = child<QLabel>(panel, "androidAppCheckProvider");
  auto* validity = child<QLabel>(panel, "androidAppCheckValidity");
  auto* token = child<QLabel>(panel, "androidAppCheckTokenPrefix");
  QJsonObject device{{"enabled", true}, {"provider", "fixture-provider"},
                     {"serial", "fixture-usb-A"},
                     {"left_s", 1200}, {"token_len", 800}, {"token_available", true}};
  QJsonObject snapshot{{"configured", true}, {"token_available", true},
                       {"source", "device"}, {"device", device}};
  panel.renderAppCheckStatus(snapshot);
  if (!require(status->property("appCheckState").toString() == "selection_required"
      && !provider->text().contains("fixture-provider") && !token->text().contains("800"),
      "Global cache appeared without a selected USB device")) return false;
  panel.renderSnapshot(connectedSnapshot());
  if (!require(provider->text() == "fixture-provider" && validity->text() == "20m 0s"
      && token->text().contains("800"), "Missing device ID suppressed known token metadata")) return false;
  for (int left : {0, -1}) {
    device.insert("left_s", left);
    device.insert("token_available", false);
    snapshot.insert("token_available", false);
    snapshot.insert("device", device);
    panel.renderAppCheckStatus(snapshot);
    if (!require(status->property("appCheckState").toString() == "expired"
        && validity->text().contains("Expirado") && token->text().contains("indisponível para uso"),
        "Expired cached token was presented as usable")) return false;
  }
  device.insert("left_s", QJsonValue());
  snapshot.insert("device", device);
  panel.renderAppCheckStatus(snapshot);
  if (!require(validity->text() == QStringLiteral("Não disponível"), "Unknown expiry treated as expiration")) return false;
  snapshot.insert("paused", true);
  panel.renderAppCheckStatus(snapshot);
  if (!require(status->property("appCheckState").toString() == "paused", "Paused issuance not shown")) return false;
  device.insert("enabled", false);
  device.insert("device_id", "android.ssaid:fixture-device");
  snapshot.insert("device", device);
  panel.renderAppCheckStatus(snapshot);
  if (!require(provider->text() == "fixture-provider" && token->text().contains("800")
      && status->property("appCheckState").toString() == "cached",
      "Disabled capture hid already collected metadata")) return false;
  device.insert("serial", "fixture-usb-B");
  snapshot.insert("device", device);
  panel.renderAppCheckStatus(snapshot);
  if (!require(status->property("appCheckState").toString() == "other_device"
      && !provider->text().contains("fixture-provider") && !token->text().contains("800")
      && child<QLabel>(panel, "androidAppCheckDeviceId")->toolTip().isEmpty(),
      "Cached token silently attributed to a different USB phone")) return false;
  device.remove("serial");
  snapshot.insert("device", device);
  panel.renderAppCheckStatus(snapshot);
  if (!require(status->property("appCheckState").toString() == "unassociated"
      && !token->text().contains("800"), "Cache without identity attributed to selected phone")) return false;
  device.insert("serial", "fixture-usb-A");
  snapshot.insert("device", device);
  panel.renderAppCheckStatus(snapshot);
  auto disconnected = stateSnapshot(QStringLiteral("disconnected"));
  disconnected.insert("selected_serial", "fixture-usb-A");
  panel.renderSnapshot(disconnected);
  if (!require(status->property("appCheckState").toString() == "usb_unconfirmed"
      && !token->text().contains("800"), "Disconnected phone kept current token details")) return false;
  panel.renderSnapshot(connectedSnapshot());
  if (!require(token->text().contains("800") && status->text().contains("cache do aparelho selecionado"),
      "Matching cache did not return after confirmed reconnect")) return false;
  panel.renderAppCheckStatus({});
  return require(provider->text() == QStringLiteral("Não disponível")
      && token->text() == QStringLiteral("Não disponível"), "Old token details survived empty snapshot");
}

bool appCheckUsbObservationStates() {
  FixtureServer fixture;
  fixture.automaticAppCheck = {
      {"configured", true}, {"token_available", true}, {"source", "device"},
      {"device", QJsonObject{{"enabled", true}, {"serial", "fixture-usb-A"},
          {"device_id", "fixture-device-id"}, {"provider", "fixture-provider"},
          {"left_s", 1200}, {"token_len", 800}, {"token_available", true}}}};
  ApiClient api;
  fixture.configure(api);
  AndroidDevicePanel panel(&api);
  auto* status = child<QLabel>(panel, "androidAppCheckStatus");
  auto* deviceId = child<QLabel>(panel, "androidAppCheckDeviceId");
  auto* provider = child<QLabel>(panel, "androidAppCheckProvider");
  auto* validity = child<QLabel>(panel, "androidAppCheckValidity");
  auto* token = child<QLabel>(panel, "androidAppCheckTokenPrefix");
  auto* badge = child<QLabel>(panel, "androidConnectionBadge");
  auto* collection = child<QLabel>(panel, "androidCollectionStatus");
  auto* observation = child<QLabel>(panel, "androidLastObservation");
  auto* refresh = child<QPushButton>(panel, "androidRefreshButton");
  auto* timer = child<QTimer>(panel, "androidAppCheckTimer");
  const auto metadataVisible = [&] {
    return deviceId->text() == QStringLiteral("fixture-device-id")
        && provider->text() == QStringLiteral("fixture-provider")
        && validity->text() == QStringLiteral("20m 0s")
        && token->text().contains(QStringLiteral("800"));
  };
  const auto lastReadPreserved = [&] {
    return metadataVisible() && badge->property("observationStale").toBool()
        && status->property("appCheckState").toString() == QStringLiteral("usb_unconfirmed")
        && status->text().contains(QStringLiteral("última leitura USB preservada"))
        && !status->text().contains(QStringLiteral("confirmando USB"));
  };
  panel.setServiceAvailable(true);
  panel.show();
  if (!require(waitUntil([&] { return fixture.requests.size() == 1; }),
      "App Check USB fixture initial read missing")) return false;
  fixture.respond(0, connectedSnapshot());
  if (!require(waitUntil([&] { return refresh->isEnabled() && metadataVisible(); }),
      "Matching cache metadata was not initially rendered")) return false;
  const QString checkedAt = observation->property("checkedAt").toString();

  for (int attempt = 0; attempt < 2; ++attempt) {
    if (!require(invokeUsbEvent(panel)
        && waitUntil([&] { return fixture.requests.size() == attempt + 2; })
        && !refresh->isEnabled() && metadataVisible()
        && status->text().contains(QStringLiteral("confirmando USB"))
        && !status->text().contains(QStringLiteral("última leitura USB preservada")),
        "Pending USB read cleared matching metadata or lacked its pending indication")) return false;
    if (attempt == 0) {
      fixture.respond(1, {{"error", "Leitura USB indisponível na fixture."}}, 503);
    } else {
      auto failed = connectedSnapshot();
      failed.insert("device", QJsonObject{});
      failed.insert("collection", QJsonObject{{"state", "error"},
          {"message", "Tempo de leitura excedido."}});
      fixture.respond(2, failed);
    }
    if (!require(waitUntil([&] { return refresh->isEnabled(); }) && lastReadPreserved()
        && collection->property("collectionState").toString() == QStringLiteral("error")
        && observation->property("checkedAt").toString() == checkedAt,
        "Completed USB failure still claimed confirmation or erased the last matching metadata")) return false;
    const int readsBefore = fixture.appCheckRequests.size();
    QMetaObject::invokeMethod(timer, "timeout", Qt::DirectConnection);
    if (!require(waitUntil([&] { return fixture.appCheckRequests.size() == readsBefore + 1
        && refresh->isEnabled(); }) && lastReadPreserved(),
        "Status polling changed a completed USB failure back into a pending confirmation")) return false;
  }

  if (!require(invokeUsbEvent(panel) && waitUntil([&] { return fixture.requests.size() == 4; }),
      "USB recovery read missing")) return false;
  fixture.respond(3, connectedSnapshot());
  if (!require(waitUntil([&] { return refresh->isEnabled(); }) && metadataVisible()
      && !badge->property("observationStale").toBool()
      && status->property("appCheckState").toString() == QStringLiteral("device")
      && status->text().contains(QStringLiteral("cache do aparelho selecionado"))
      && !status->text().contains(QStringLiteral("preservada"))
      && !status->text().contains(QStringLiteral("confirmando USB")),
      "Successful USB recovery retained the stale or pending indication")) return false;

  if (!require(invokeUsbEvent(panel) && waitUntil([&] { return fixture.requests.size() == 5; }),
      "USB disconnect read missing")) return false;
  auto disconnected = stateSnapshot(QStringLiteral("disconnected"));
  disconnected.insert("selected_serial", "fixture-usb-A");
  fixture.respond(4, disconnected);
  if (!require(waitUntil([&] { return refresh->isEnabled(); })
      && badge->property("connectionState").toString() == QStringLiteral("disconnected"),
      "Confirmed USB disconnect was not rendered")) return false;
  const int readsBefore = fixture.appCheckRequests.size();
  QMetaObject::invokeMethod(timer, "timeout", Qt::DirectConnection);
  if (!require(waitUntil([&] { return fixture.appCheckRequests.size() == readsBefore + 1
      && refresh->isEnabled(); }), "Disconnected status read missing")) return false;
  for (auto* field : {deviceId, provider, validity, token}) {
    if (!require(field->text() == QStringLiteral("Sem dados confirmados deste aparelho")
        && field->toolTip().isEmpty(), "Confirmed disconnect retained or restored matching token metadata")) return false;
  }
  return require(!status->text().contains(QStringLiteral("confirmando USB"))
      && fixture.recheckRequests.isEmpty() && fixture.onlyDiagnosticReads(),
      "USB observation used token mutation or left a pending indication after disconnect");
}

bool appCheckReadFailurePreservesObservation() {
  FixtureServer fixture;
  fixture.deferAppCheck = true;
  ApiClient api;
  fixture.configure(api);
  AndroidDevicePanel panel(&api);
  auto* status = child<QLabel>(panel, "androidAppCheckStatus");
  auto* token = child<QLabel>(panel, "androidAppCheckTokenPrefix");
  auto* provider = child<QLabel>(panel, "androidAppCheckProvider");
  auto* refresh = child<QPushButton>(panel, "androidRefreshButton");
  auto* timer = child<QTimer>(panel, "androidAppCheckTimer");
  QJsonObject available{{"configured", true}, {"token_available", true}, {"source", "device"},
      {"device", QJsonObject{{"enabled", true}, {"serial", "fixture-usb-A"},
          {"provider", "fixture-provider"}, {"token_len", 800},
          {"left_s", 1200}, {"token_available", true}}}};
  panel.setServiceAvailable(true);
  panel.show();
  if (!require(waitUntil([&] { return fixture.requests.size() == 1
      && fixture.appCheckRequests.size() == 1; }), "Observation fixture reads missing")) return false;
  fixture.respond(0, connectedSnapshot());
  fixture.respondAppCheck(0, available);
  if (!require(waitUntil([&] { return refresh->isEnabled() && token->text().contains("800"); }),
      "Initial observation not rendered")) return false;

  for (int failure = 0; failure < 2; ++failure) {
    QMetaObject::invokeMethod(timer, "timeout", Qt::DirectConnection);
    if (!require(waitUntil([&] { return fixture.appCheckRequests.size() == failure + 2; }),
        "Failure fixture status read missing")) return false;
    // The second response is HTTP 200 with an incomplete schema, not a status.
    fixture.respondAppCheck(failure + 1, {}, failure == 0 ? 503 : 200);
    if (!require(waitUntil([&] { return refresh->isEnabled(); })
        && status->property("appCheckState").toString() == "error"
        && status->property("statusReadFailed").toBool()
        && status->text().contains(QStringLiteral("desatualizada"))
        && token->text().contains("800") && token->property("observationStale").toBool()
        && !token->text().contains(QStringLiteral("utilizável"))
        && child<QLabel>(panel, "androidAppCheckValidity")->text().contains(QStringLiteral("última consulta"))
        && provider->text() == "fixture-provider", "Status failure erased or endorsed cached metadata")) return false;
    const int usbReads = fixture.requests.size();
    if (!require(invokeUsbEvent(panel)
        && waitUntil([&] { return fixture.requests.size() == usbReads + 1; }),
        "Independent USB observation missing")) return false;
    fixture.respond(usbReads, connectedSnapshot());
    if (!require(waitUntil([&] { return refresh->isEnabled(); })
        && status->property("statusReadFailed").toBool()
        && status->property("appCheckState").toString() == "error"
        && token->text().contains("800"), "USB success hid the failed status observation")) return false;
  }

  auto disconnected = stateSnapshot(QStringLiteral("disconnected"));
  disconnected.insert("selected_serial", "fixture-usb-A");
  panel.renderSnapshot(disconnected);
  if (!require(!token->text().contains("800"), "Disconnection restored unconfirmed metadata")) return false;
  panel.renderSnapshot(connectedSnapshot(QStringLiteral("fixture-usb-B")));
  if (!require(!token->text().contains("800") && provider->text() != "fixture-provider",
      "Status failure attributed old metadata to another phone")) return false;
  panel.renderSnapshot(connectedSnapshot());
  QMetaObject::invokeMethod(timer, "timeout", Qt::DirectConnection);
  if (!require(waitUntil([&] { return fixture.appCheckRequests.size() == 4; }),
      "Recovery status read missing")) return false;
  fixture.respondAppCheck(3, available);
  if (!require(waitUntil([&] { return refresh->isEnabled(); })
      && !status->property("statusReadFailed").toBool()
      && !status->toolTip().contains(QStringLiteral("Não foi possível confirmar"))
      && !token->property("observationStale").toBool()
      && status->property("appCheckState").toString() == "device",
      "Fresh status failed to clear observation warning")) return false;
  QMetaObject::invokeMethod(timer, "timeout", Qt::DirectConnection);
  if (!require(waitUntil([&] { return fixture.appCheckRequests.size() == 5; }),
      "Confirmed absence read missing")) return false;
  fixture.respondAppCheck(4, {{"configured", true}, {"token_available", false}});
  return require(waitUntil([&] { return refresh->isEnabled(); }) && !token->text().contains("800")
      && !status->property("statusReadFailed").toBool()
      && fixture.recheckRequests.isEmpty() && fixture.onlyDiagnosticReads(),
      "Fresh absence was ignored or the read-only scenario emitted a mutation");
}

bool appCheckSelectionRefresh() {
  FixtureServer fixture;
  fixture.deferAppCheck = true;
  ApiClient api;
  fixture.configure(api);
  AndroidDevicePanel panel(&api);
  auto* selector = child<QComboBox>(panel, "androidDeviceSelector");
  auto* provider = child<QLabel>(panel, "androidAppCheckProvider");
  auto* token = child<QLabel>(panel, "androidAppCheckTokenPrefix");
  auto* status = child<QLabel>(panel, "androidAppCheckStatus");
  panel.setServiceAvailable(true);
  panel.show();
  if (!require(waitUntil([&] { return fixture.requests.size() == 1
      && fixture.appCheckRequests.size() == 1; }), "Initial reads missing")) return false;
  fixture.respond(0, multipleSnapshot());
  if (!require(waitUntil([&] { return selector->count() == 3; }), "Device choices missing")) return false;
  const QString selected = QStringLiteral("fixture /B?&+á");
  selector->setCurrentIndex(selector->findData(selected));
  QJsonObject metadata{{"enabled", true}, {"serial", "fixture-usb-A"},
      {"device_id", "old-device"}, {"provider", "old-provider"}, {"token_len", 123},
      {"left_s", 120}, {"token_available", true}};
  QJsonObject cached{{"configured", true}, {"source", "device"},
      {"token_available", true}, {"device", metadata}};
  fixture.respondAppCheck(0, cached);
  if (!require(waitUntil([&] { return fixture.appCheckRequests.size() == 2
      && fixture.requests.size() == 2; })
      && !provider->text().contains("old-provider"), "Selection accepted stale App Check reply")) return false;
  fixture.respond(1, connectedSnapshot(selected));
  metadata.insert("serial", selected);
  metadata.insert("provider", "selected-provider");
  cached.insert("device", metadata);
  fixture.respondAppCheck(1, cached);
  if (!require(waitUntil([&] { return provider->text() == "selected-provider"; })
      && token->text().contains("123") && status->text().contains("cache do aparelho selecionado"),
      "Selected-device status did not update dynamically")) return false;
  return require(fixture.recheckRequests.isEmpty(), "Selection triggered token mutation");
}

bool appCheckLifecycle() {
  FixtureServer fixture;
  fixture.deferAppCheck = true;
  fixture.automatic = true;
  ApiClient api;
  fixture.configure(api);
  AndroidDevicePanel panel(&api);
  auto* status = child<QLabel>(panel, "androidAppCheckStatus");
  auto* timer = child<QTimer>(panel, "androidAppCheckTimer");
  panel.setServiceAvailable(true);
  panel.show();
  if (!require(waitUntil([&] { return fixture.appCheckRequests.size() == 1; }), "App Check read missing")) return false;
  QMetaObject::invokeMethod(timer, "timeout", Qt::DirectConnection);
  processFor(30);
  if (!require(fixture.appCheckRequests.size() == 1, "Concurrent App Check reads")) return false;
  panel.hide();
  panel.show();
  fixture.respondAppCheck(0, {{"configured", true}, {"token_available", true}, {"source", "bearer"}});
  if (!require(waitUntil([&] { return fixture.appCheckRequests.size() == 2; })
      && status->property("appCheckState").toString() != "bearer", "Late App Check response rendered after navigation")) return false;
  fixture.respondAppCheck(1, {{"configured", true}, {"token_available", true}, {"source", "bearer"}});
  if (!require(waitUntil([&] { return status->property("appCheckState").toString() == "bearer"; }), "Fresh App Check state missing")) return false;
  QMetaObject::invokeMethod(timer, "timeout", Qt::DirectConnection);
  if (!require(waitUntil([&] { return fixture.appCheckRequests.size() == 3; }), "App Check periodic read missing")) return false;
  fixture.respondAppCheck(2, {}, 500);
  if (!require(waitUntil([&] { return status->property("appCheckState").toString() == "error"; }), "App Check error concealed")) return false;
  QMetaObject::invokeMethod(timer, "timeout", Qt::DirectConnection);
  if (!require(waitUntil([&] { return fixture.appCheckRequests.size() == 4; }), "App Check did not retry after failure")) return false;
  panel.setServiceAvailable(false);
  fixture.respondAppCheck(3, {{"configured", true}, {"token_available", true}, {"source", "bearer"}});
  processFor(30);
  return require(!timer->isActive() && status->property("appCheckState").toString() == "unavailable"
      && fixture.onlyDiagnosticReads() && fixture.recheckRequests.isEmpty(),
      "App Check outlived service availability or used a mutating route");
}

bool appCheckActionFeedback() {
  FixtureServer fixture;
  fixture.automatic = true;
  fixture.deferAppCheck = true;
  fixture.deferRecheck = true;
  ApiClient api;
  fixture.configure(api);
  AndroidDevicePanel panel(&api);
  auto* refresh = child<QPushButton>(panel, "androidRefreshButton");
  auto* feedback = child<QLabel>(panel, "androidAppCheckActionStatus");
  auto* timer = child<QTimer>(panel, "androidAppCheckTimer");
  panel.setServiceAvailable(true);
  panel.show();
  if (!require(waitUntil([&] { return fixture.appCheckRequests.size() == 1; })
      && !refresh->isEnabled(), "Refresh enabled during status read")) return false;
  refresh->click();
  processFor(20);
  if (!require(fixture.recheckRequests.isEmpty(), "Busy button submitted an action")) return false;
  fixture.respondAppCheck(0, fixture.automaticAppCheck);
  if (!require(waitUntil([&] { return refresh->isEnabled(); }), "Refresh stayed disabled after read")) return false;

  const QJsonObject unavailable{{"configured", true}, {"token_available", false}, {"device", QJsonObject{}}};
  const QJsonObject available{{"configured", true}, {"token_available", true},
                            {"source", "bearer"}, {"device", QJsonObject{}}};
  const QList<QJsonObject> replies{
      {{"ok", false}, {"message", "Aguarde 30 segundos."}, {"appcheck", unavailable}},
      {},
      {{"ok", true}, {"appcheck", QJsonObject{}}},
      {{"ok", true}, {"appcheck", unavailable}},
      {{"ok", true}, {"message", "Token disponível; aceitação depende do serviço."}, {"appcheck", available}}};
  for (int i = 0; i < replies.size(); ++i) {
    refresh->click();
    if (!require(waitUntil([&] { return fixture.recheckRequests.size() == i + 1; }), "Explicit action missing")) return false;
    processFor(20); // Let the independent USB response complete first.
    if (!require(!refresh->isEnabled(), "USB response enabled button while action pending")) return false;
    QMetaObject::invokeMethod(timer, "timeout", Qt::DirectConnection);
    refresh->click();
    processFor(20);
    if (!require(fixture.appCheckRequests.size() == 1 && fixture.recheckRequests.size() == i + 1,
        "Polling or second click overlapped pending action")) return false;
    fixture.respondRecheck(i, replies.at(i));
    if (!require(waitUntil([&] { return refresh->isEnabled(); }), "Action did not release button")) return false;
    const QString expected = i == replies.size() - 1 ? "available" : "error";
    if (!require(feedback->property("actionState").toString() == expected
        && !feedback->text().contains("renovado com sucesso"), "HTTP success was mistaken for renewal")) return false;
    if (i == 0 && !require(feedback->text().contains("Aguarde 30 segundos"), "Backend reason hidden")) return false;
    if (i == 2 && !require(child<QLabel>(panel, "androidAppCheckStatus")
        ->property("statusReadFailed").toBool(), "Partial action status cleared the observation warning")) return false;
  }
  const int actionCount = replies.size();
  refresh->click();
  if (!require(waitUntil([&] { return fixture.recheckRequests.size() == actionCount + 1; }), "Error action missing")) return false;
  fixture.respondRecheck(actionCount, {{"error", "Serviço temporariamente indisponível."}}, 503);
  if (!require(waitUntil([&] { return refresh->isEnabled(); })
      && feedback->text().contains("Serviço temporariamente indisponível"), "HTTP error detail hidden")) return false;
  const QString failure = feedback->text();
  QMetaObject::invokeMethod(timer, "timeout", Qt::DirectConnection);
  if (!require(waitUntil([&] { return fixture.appCheckRequests.size() == 2; }), "Polling did not resume")) return false;
  fixture.respondAppCheck(1, available);
  if (!require(waitUntil([&] { return refresh->isEnabled(); }) && feedback->text() == failure,
      "Polling erased the last action failure")) return false;
  refresh->click();
  if (!require(waitUntil([&] { return fixture.recheckRequests.size() == actionCount + 2; }), "Navigation fixture action missing")) return false;
  panel.hide();
  panel.show();
  fixture.respondRecheck(actionCount + 1, replies.last());
  if (!require(waitUntil([&] { return fixture.appCheckRequests.size() == 3; })
      && feedback->isHidden(), "Late action response survived navigation")) return false;
  fixture.respondAppCheck(2, available);
  return require(waitUntil([&] { return refresh->isEnabled(); }), "Navigation left refresh disabled");
}

bool snapshotStates() {
  AndroidDevicePanel panel(nullptr);
  auto* badge = child<QLabel>(panel, "androidConnectionBadge");
  auto* model = child<QLabel>(panel, "androidModel");
  auto* serial = child<QLabel>(panel, "androidSerial");
  auto* version = child<QLabel>(panel, "androidAndroidVersion");
  auto* root = child<QLabel>(panel, "androidRootStatus");
  auto* collection = child<QLabel>(panel, "androidCollectionStatus");
  auto* observation = child<QLabel>(panel, "androidLastObservation");
  auto* selector = child<QComboBox>(panel, "androidDeviceSelector");
  auto* progress = child<QProgressBar>(panel, "androidReadProgress");
  auto* refresh = child<QPushButton>(panel, "androidRefreshButton");
  auto* phone = child<QWidget>(panel, "androidPhoneIllustration");
  if (!require(badge && model && serial && version && root && collection && observation && selector && progress && refresh && phone,
               "Diagnostic widgets lack stable object names")) return false;
  if (!require(!refresh->isEnabled() && progress->isHidden(), "Unavailable service allowed a read")) return false;
  const QList<QPair<QString, QString>> states{
      {QStringLiteral("error"), QStringLiteral("Verificação indisponível")},
      {QStringLiteral("adb_missing"), QStringLiteral("ADB não disponível")},
      {QStringLiteral("disconnected"), QStringLiteral("Desconectado")},
      {QStringLiteral("unauthorized"), QStringLiteral("Autorização pendente")},
      {QStringLiteral("offline"), QStringLiteral("Offline")}};
  for (const auto& state : states) {
    panel.renderSnapshot(stateSnapshot(state.first));
    if (!require(badge->text() == state.second && badge->property("connectionState").toString() == state.first
                     && !badge->styleSheet().isEmpty() && root->text().contains(QStringLiteral("Não verificado"))
                     && progress->isHidden() && !phone->property("screenOn").toBool()
                     && model->text() == QStringLiteral("Não extraído")
                     && serial->text() == QStringLiteral("Não extraído")
                     && version->text() == QStringLiteral("Não extraído"), "Snapshot status or non-pending progress is misleading")) return false;
  }
  panel.renderSnapshot(multipleSnapshot());
  if (!require(badge->text() == QStringLiteral("Escolha um dispositivo") && !selector->isHidden()
                   && selector->count() == 3 && selector->currentData().toString().isEmpty(),
               "Multiple devices were silently selected")) return false;
  panel.renderSnapshot(connectedSnapshot());
  const QString checkedAt = observation->property("checkedAt").toString();
  if (!require(badge->text() == QStringLiteral("Conectado") && model->text().contains(QStringLiteral("Celular de teste"))
                   && version->text() == QStringLiteral("14 (SDK 34)")
                   && root->text().contains(QStringLiteral("su disponível"))
                   && root->accessibleDescription().contains(QStringLiteral("não foi verificado"))
                   && root->text().contains(QStringLiteral("acesso não testado"))
                   && !root->text().contains(QStringLiteral("ON"))
                   && phone->property("screenOn").toBool() && phone->accessibleName().contains(QStringLiteral("tela acesa"))
                   && serial->text() == QStringLiteral("fixture-usb-A")
                   && collection->text().contains(QStringLiteral("42 ms"))
                   && checkedAt == QStringLiteral("2026-10-12T15:20:30.123Z"),
               "Connected public information or honest su/time rendering failed")) return false;
  for (const auto& state : {QStringLiteral("root_shell"), QStringLiteral("not_detected"), QStringLiteral("unknown")}) {
    panel.renderSnapshot(connectedSnapshot(QStringLiteral("fixture-usb-A"), QStringLiteral("Celular de teste"), state));
    const QString expected = state == QStringLiteral("root_shell") ? QStringLiteral("ON · shell root")
        : state == QStringLiteral("not_detected") ? QStringLiteral("OFF · não detectado") : QStringLiteral("Não verificado");
    if (!require(root->text().contains(expected), "Root state did not have an explicit word label")) return false;
  }
  auto partial = connectedSnapshot();
  partial.insert("collection", QJsonObject{{"state", "error"}, {"message", "Tempo de leitura excedido."},
                                           {"checked_at", "2026-10-12T16:00:00Z"}});
  partial.insert("device", QJsonObject{});
  partial.insert("root", QJsonObject{{"state", "unknown"}});
  panel.renderSnapshot(partial);
  if (!require(badge->text().contains(QStringLiteral("Conectado")) && collection->text().contains(QStringLiteral("excedido"))
                   && observation->property("checkedAt").toString() == checkedAt && phone->property("screenOn").toBool()
                   && phone->property("observationStale").toBool() && observation->property("observationStale").toBool()
                   && model->text().contains(QStringLiteral("Celular de teste")) && version->text() == QStringLiteral("14 (SDK 34)")
                   && phone->accessibleName().contains(QStringLiteral("não atualizada")),
               "Collection failure changed confirmed USB presence or erased the last public observation")) return false;
  for (const QString& state : {QStringLiteral("error"), QStringLiteral("unknown"), QStringLiteral("adb_missing")}) {
    auto failed = stateSnapshot(state);
    failed.insert("collection", QJsonObject{{"state", "error"}, {"message", "Inventário não pôde ser atualizado."}});
    panel.renderSnapshot(failed);
    if (!require(badge->property("connectionState").toString() == QStringLiteral("connected")
                     && badge->property("observationStale").toBool() && phone->property("screenOn").toBool()
                     && model->text().contains(QStringLiteral("Celular de teste"))
                     && selector->currentData().toString() == QStringLiteral("fixture-usb-A")
                     && collection->text().contains(QStringLiteral("não foi reavaliada"))
                     && observation->property("checkedAt").toString() == checkedAt,
                 "Failed inventory erased or replaced a previously confirmed USB observation")) return false;
  }
  auto discordant = connectedSnapshot(QStringLiteral("fixture-usb-B"), QStringLiteral("Outro aparelho"));
  panel.renderSnapshot(discordant);
  if (!require(phone->property("screenOn").toBool() && badge->property("observationStale").toBool()
                   && !model->text().contains(QStringLiteral("Outro aparelho"))
                   && selector->currentData().toString() == QStringLiteral("fixture-usb-A")
                   && collection->text().contains(QStringLiteral("não corresponde")),
               "Discordant snapshot rebound selection or rendered another device")) return false;
  auto inconsistent = connectedSnapshot();
  inconsistent.insert("devices", QJsonArray{QJsonObject{{"serial", "fixture-usb-A"}, {"state", "offline"}}});
  panel.renderSnapshot(inconsistent);
  if (!require(phone->property("screenOn").toBool() && badge->property("observationStale").toBool()
                   && collection->text().contains(QStringLiteral("não corresponde")),
               "Contradictory connected/offline inventory was accepted as a fresh USB connection")) return false;
  inconsistent = connectedSnapshot();
  inconsistent.insert("selected_serial", QString{});
  panel.renderSnapshot(inconsistent);
  if (!require(selector->currentData().toString() == QStringLiteral("fixture-usb-A")
                   && badge->property("observationStale").toBool() && collection->text().contains(QStringLiteral("não corresponde")),
               "Connected snapshot without an identity silently rebound device selection")) return false;
  for (const QString& state : {QStringLiteral("disconnected"), QStringLiteral("offline"), QStringLiteral("unauthorized")}) {
    panel.renderSnapshot(connectedSnapshot());
    auto confirmed = stateSnapshot(state);
    confirmed.insert("selected_serial", QStringLiteral("fixture-usb-A"));
    if (state != QStringLiteral("disconnected"))
      confirmed.insert("devices", QJsonArray{QJsonObject{{"serial", "fixture-usb-A"}, {"state", state}}});
    panel.renderSnapshot(confirmed);
    if (!require(badge->property("connectionState").toString() == state && !phone->property("screenOn").toBool()
                     && !badge->property("observationStale").toBool() && model->text() == QStringLiteral("Não extraído")
                     && root->text() == QStringLiteral("Não verificado"),
                 "Confirmed USB disconnect/offline/authorization state was delayed or retained connected details")) return false;
  }
  auto textSnapshot = connectedSnapshot(QStringLiteral("fixture-usb-A"), QStringLiteral("<b>Texto público</b>"));
  panel.renderSnapshot(textSnapshot);
  return require(model->textFormat() == Qt::PlainText && model->text().contains(QStringLiteral("<b>"))
                     && badge->wordWrap() && model->wordWrap() && root->wordWrap() && collection->wordWrap()
                     && !root->accessibleName().isEmpty(), "Public values were interpreted as markup or lacked accessibility");
}

bool refreshAndLifecycle() {
  FixtureServer fixture;
  ApiClient api;
  fixture.configure(api);
  AndroidDevicePanel panel(&api);
  panel.resize(640, 420);
  auto* refresh = child<QPushButton>(panel, "androidRefreshButton");
  auto* progress = child<QProgressBar>(panel, "androidReadProgress");
  auto* timer = child<QTimer>(panel, "androidRefreshTimer");
  auto* watcher = child<UsbDeviceWatcher>(panel, "androidUsbWatcher");
  auto* eventStatus = child<QLabel>(panel, "androidUsbEventStatus");
  auto* model = child<QLabel>(panel, "androidModel");
  auto* badge = child<QLabel>(panel, "androidConnectionBadge");
  auto* collection = child<QLabel>(panel, "androidCollectionStatus");
  auto* phone = child<QWidget>(panel, "androidPhoneIllustration");
  auto* observation = child<QLabel>(panel, "androidLastObservation");
  panel.setServiceAvailable(true);
  processFor(30);
  if (!require(watcher && eventStatus && fixture.requests.isEmpty() && !timer->isActive() && !watcher->isActive(),
               "Hidden panel started USB monitoring or automatic reads")) return false;
  panel.show();
  if (!require(waitUntil([&] { return fixture.requests.size() == 1; }) && !timer->isActive()
                   && timer->isSingleShot() && timer->interval() == 200
                   && !progress->isHidden() && progress->minimum() == 0 && progress->maximum() == 0
                   && !refresh->isEnabled(), "Visible pending read did not show its actual progress")) return false;
  if (!require(panel.property("usbEventsActive").toBool() == watcher->isActive()
                   && eventStatus->isHidden() == watcher->isActive()
                   && (watcher->isActive() || eventStatus->text().contains(QStringLiteral("Use Atualizar"))),
               "Unavailable native USB monitoring was silent or incorrectly described")) return false;
  refresh->click();
  processFor(30);
  if (!require(fixture.requests.size() == 1, "Pending refresh overlapped another HTTP read")) return false;
  fixture.respond(0, connectedSnapshot());
  if (!require(waitUntil([&] { return refresh->isEnabled(); }) && progress->isHidden()
                   && badge->text() == QStringLiteral("Conectado"), "Completed read stayed pending")) return false;
  processFor(20);
  const QRect phoneGeometry = phone->geometry();
  const QRect modelGeometry = model->geometry();
  if (!require(progress->sizePolicy().retainSizeWhenHidden(), "Hidden read progress did not reserve its layout space")) return false;
  processFor(6500);
  if (!require(fixture.requests.size() == 1 && !timer->isActive() && phone->property("screenOn").toBool(),
               "Idle USB panel still performed periodic six-second reads")) return false;
  for (int event = 0; event < 8; ++event)
    if (!require(invokeUsbEvent(panel), "USB topology signal fixture missing")) return false;
  processFor(80);
  if (!require(fixture.requests.size() == 1 && timer->isActive(), "USB event burst bypassed its coalescing delay")) return false;
  if (!require(waitUntil([&] { return fixture.requests.size() == 2; }) && phone->property("screenOn").toBool()
                   && badge->property("connectionState").toString() == QStringLiteral("connected")
                   && badge->property("observationStale").toBool() && observation->property("observationStale").toBool()
                   && model->text().contains(QStringLiteral("Celular de teste"))
                   && phone->accessibleName().contains(QStringLiteral("não atualizada")),
               "Pending USB-event refresh turned off or cleared the connected phone")) return false;
  processFor(20);
  if (!require(phone->geometry() == phoneGeometry && model->geometry() == modelGeometry,
               "Pending refresh moved the phone or public-details layout")) return false;
  fixture.respond(1, {{"error", "Falha de fixture do serviço local."}}, 503);
  if (!require(waitUntil([&] { return refresh->isEnabled(); })
                   && badge->text().contains(QStringLiteral("Conectado"))
                   && badge->property("observationStale").toBool()
                   && phone->property("screenOn").toBool()
                   && !badge->text().contains(QStringLiteral("Desconectado"))
                   && collection->text().contains(QStringLiteral("não foi reavaliada"))
                   && model->text().contains(QStringLiteral("Celular de teste")),
               "Service request failure implied phone disconnection or cleared valid data")) return false;
  refresh->click();
  if (!require(waitUntil([&] { return fixture.requests.size() == 3; }), "Manual retry was not allowed after an error")) return false;
  panel.setServiceAvailable(false);
  if (!require(!timer->isActive() && !watcher->isActive() && !panel.property("usbEventsActive").toBool()
                   && !refresh->isEnabled() && progress->isHidden()
                   && collection->text().contains(QStringLiteral("Serviço local indisponível"))
                   && phone->property("screenOn").toBool() && observation->property("observationStale").toBool()
                   && !badge->text().contains(QStringLiteral("Desconectado")), "Service outage was presented as phone disconnection")) return false;
  fixture.respond(2, connectedSnapshot(QStringLiteral("fixture-usb-A"), QStringLiteral("Resposta obsoleta")));
  processFor(50);
  if (!require(!model->text().contains(QStringLiteral("obsoleta")) && !refresh->isEnabled(),
               "Service reset accepted an obsolete callback")) return false;
  panel.setServiceAvailable(true);
  if (!require(waitUntil([&] { return fixture.requests.size() == 4; }), "Recovered service did not refresh visible panel")) return false;
  panel.hide();
  if (!require(!timer->isActive() && !watcher->isActive() && progress->isHidden(),
               "Hidden panel kept its watcher, coalescing timer or spinner active")) return false;
  fixture.respond(3, connectedSnapshot(QStringLiteral("fixture-usb-A"), QStringLiteral("Resposta oculta")));
  processFor(50);
  if (!require(invokeUsbEvent(panel), "Hidden USB topology signal fixture missing")) return false;
  processFor(20);
  if (!require(fixture.requests.size() == 4 && !model->text().contains(QStringLiteral("oculta")),
               "Hidden panel read again or accepted a stale callback")) return false;
  panel.show();
  if (!require(waitUntil([&] { return fixture.requests.size() == 5; }), "Showing panel again did not refresh")) return false;
  fixture.respond(4, connectedSnapshot());
  if (!require(waitUntil([&] { return refresh->isEnabled(); }), "Shown panel did not recover its read")) return false;
  processFor(20);
  if (!require(phone->geometry() == phoneGeometry && model->geometry() == modelGeometry,
               "Completed refresh did not restore the stable public-details layout")) return false;
  return require(fixture.onlyDiagnosticReads(), "Panel accessed a route beyond the public diagnostic endpoint");
}

bool selectionAndServiceGeneration() {
  FixtureServer fixture;
  ApiClient api;
  fixture.configure(api);
  AndroidDevicePanel panel(&api);
  auto* refresh = child<QPushButton>(panel, "androidRefreshButton");
  auto* selector = child<QComboBox>(panel, "androidDeviceSelector");
  auto* model = child<QLabel>(panel, "androidModel");
  auto* observation = child<QLabel>(panel, "androidLastObservation");
  panel.setServiceAvailable(true);
  panel.show();
  if (!require(waitUntil([&] { return fixture.requests.size() == 1; }), "Initial fixture request missing")) return false;
  fixture.respond(0, multipleSnapshot());
  if (!require(waitUntil([&] { return refresh->isEnabled(); }) && selector->isEnabled()
                   && selector->currentData().toString().isEmpty(), "Multiple-device selector missing")) return false;
  refresh->click();
  if (!require(waitUntil([&] { return fixture.requests.size() == 2; }), "Pre-selection read missing")) return false;
  const QString serial = QStringLiteral("fixture /B?&+á");
  selector->setCurrentIndex(selector->findData(serial));
  processFor(30);
  if (!require(fixture.requests.size() == 2 && model->text() == QStringLiteral("Não extraído"),
               "Selecting during a read overlapped requests or kept the previous device details")) return false;
  fixture.respond(1, connectedSnapshot(QStringLiteral("fixture-usb-A"), QStringLiteral("Modelo antigo")));
  if (!require(waitUntil([&] { return fixture.requests.size() == 3; })
                   && !model->text().contains(QStringLiteral("antigo"))
                   && observation->property("checkedAt").toString().isEmpty(), "Old selection callback overwrote new device")) return false;
  const QByteArray expected = "/api/integrations/android/device?serial=" + QUrl::toPercentEncoding(serial);
  const QUrl requestUrl(QStringLiteral("http://127.0.0.1") + QString::fromLatin1(fixture.requests.at(2).target));
  if (!require(fixture.requests.at(2).target == expected
                   && QUrlQuery(requestUrl).queryItemValue(QStringLiteral("serial"), QUrl::FullyDecoded) == serial,
               "Selected serial did not stay in its encoded query parameter")) return false;
  auto selected = connectedSnapshot(serial, QStringLiteral("Modelo escolhido"));
  selected.insert("devices", multipleSnapshot().value("devices"));
  fixture.respond(2, selected);
  if (!require(waitUntil([&] { return refresh->isEnabled(); })
                   && model->text().contains(QStringLiteral("escolhido"))
                   && selector->currentData().toString() == serial, "Selected-device result was not rendered")) return false;
  refresh->click();
  if (!require(waitUntil([&] { return fixture.requests.size() == 4; }), "Read before service reset missing")) return false;
  panel.setServiceAvailable(false);
  panel.setServiceAvailable(true);
  if (!require(invokeUsbEvent(panel), "Service-generation USB topology signal fixture missing")) return false;
  processFor(30);
  if (!require(fixture.requests.size() == 4, "Service reset overlapped a transport request still pending")) return false;
  fixture.respond(3, connectedSnapshot(QStringLiteral("fixture-usb-A"), QStringLiteral("Geração antiga")));
  if (!require(waitUntil([&] { return fixture.requests.size() == 5; })
                   && !model->text().contains(QStringLiteral("Geração antiga"))
                   && selector->currentData().toString() == serial, "Service recovery accepted an old generation")) return false;
  fixture.respond(4, selected);
  if (!require(waitUntil([&] { return refresh->isEnabled(); }), "Queued recovery read stayed pending")) return false;
  return require(fixture.onlyDiagnosticReads(), "Selection used a non-diagnostic operation");
}

bool usbEventsDuringPendingRead() {
  FixtureServer fixture;
  ApiClient api;
  fixture.configure(api);
  AndroidDevicePanel panel(&api);
  auto* refresh = child<QPushButton>(panel, "androidRefreshButton");
  auto* timer = child<QTimer>(panel, "androidRefreshTimer");
  auto* model = child<QLabel>(panel, "androidModel");
  auto* phone = child<QWidget>(panel, "androidPhoneIllustration");
  panel.setServiceAvailable(true);
  panel.show();
  if (!require(waitUntil([&] { return fixture.requests.size() == 1; }), "Pending-event initial read missing")) return false;
  fixture.respond(0, connectedSnapshot());
  if (!require(waitUntil([&] { return refresh->isEnabled(); }), "Pending-event initial read did not finish")) return false;
  refresh->click();
  if (!require(waitUntil([&] { return fixture.requests.size() == 2; }), "Pending-event manual read missing")) return false;
  for (int event = 0; event < 5; ++event)
    if (!require(invokeUsbEvent(panel), "Pending-event topology signal fixture missing")) return false;
  fixture.respond(1, connectedSnapshot(QStringLiteral("fixture-usb-A"), QStringLiteral("Resposta anterior ao evento")));
  processFor(40);
  if (!require(fixture.requests.size() == 2 && timer->isActive()
                   && !model->text().contains(QStringLiteral("anterior ao evento")) && phone->property("screenOn").toBool(),
               "Old callback bypassed an active event delay or erased the connected phone")) return false;
  if (!require(waitUntil([&] { return fixture.requests.size() == 3; }) && !timer->isActive(),
               "Coalesced USB event did not launch one deferred read")) return false;
  for (int event = 0; event < 5; ++event)
    if (!require(invokeUsbEvent(panel), "Second pending-event topology signal fixture missing")) return false;
  processFor(250);
  if (!require(fixture.requests.size() == 3 && !timer->isActive() && phone->property("screenOn").toBool(),
               "Event delay expiration overlapped an unfinished diagnostic read")) return false;
  fixture.respond(2, connectedSnapshot(QStringLiteral("fixture-usb-A"), QStringLiteral("Segunda resposta obsoleta")));
  if (!require(waitUntil([&] { return fixture.requests.size() == 4; })
                   && !model->text().contains(QStringLiteral("obsoleta")),
               "Queued USB event accepted an obsolete read or failed to resume after it")) return false;
  fixture.respond(3, connectedSnapshot(QStringLiteral("fixture-usb-A"), QStringLiteral("Dados após evento USB")));
  if (!require(waitUntil([&] { return refresh->isEnabled(); }) && phone->property("screenOn").toBool()
                   && !phone->property("observationStale").toBool()
                   && model->text().contains(QStringLiteral("após evento")),
               "Final USB-event generation did not render its own observation")) return false;
  processFor(350);
  if (!require(fixture.requests.size() == 4 && !timer->isActive(), "Completed USB-event queue launched an extra read")) return false;
  panel.hide();
  if (!require(invokeUsbEvent(panel), "Hidden pending-event topology signal fixture missing")) return false;
  processFor(250);
  return require(fixture.requests.size() == 4 && !timer->isActive() && fixture.onlyDiagnosticReads(),
                 "Hidden USB-event panel kept reading or accessed a non-diagnostic route");
}

bool selectedPhysicalDeviceRemainsSelected() {
  FixtureServer fixture;
  ApiClient api;
  fixture.configure(api);
  AndroidDevicePanel panel(&api);
  auto* refresh = child<QPushButton>(panel, "androidRefreshButton");
  auto* selector = child<QComboBox>(panel, "androidDeviceSelector");
  auto* phone = child<QWidget>(panel, "androidPhoneIllustration");
  auto* model = child<QLabel>(panel, "androidModel");
  auto* observation = child<QLabel>(panel, "androidLastObservation");
  const QString physical = QStringLiteral("fixture-usb-A"), emulator = QStringLiteral("fixture-emulator");
  const QByteArray physicalTarget = "/api/integrations/android/device?serial=" + QUrl::toPercentEncoding(physical);
  panel.setServiceAvailable(true);
  panel.show();
  if (!require(waitUntil([&] { return fixture.requests.size() == 1; }), "Physical-selection fixture read missing")) return false;
  fixture.respond(0, connectedSnapshot());
  if (!require(waitUntil([&] { return refresh->isEnabled(); }) && phone->property("screenOn").toBool(),
               "Physical-selection fixture was not initially connected")) return false;
  refresh->click();
  if (!require(waitUntil([&] { return fixture.requests.size() == 2; }), "ADB-unavailable fixture read missing")) return false;
  fixture.respond(1, stateSnapshot(QStringLiteral("adb_missing")));
  if (!require(waitUntil([&] { return refresh->isEnabled(); }) && phone->property("screenOn").toBool()
                   && selector->currentData().toString() == physical && model->text().contains(QStringLiteral("Celular de teste")),
               "Unavailable ADB erased the physical selection or its last observation")) return false;
  auto missing = stateSnapshot(QStringLiteral("disconnected"));
  missing.insert("selected_serial", physical);
  missing.insert("devices", QJsonArray{QJsonObject{{"serial", emulator}, {"state", "device"}, {"model", "Emulador QA"}}});
  for (int request = 2; request < 5; ++request) {
    refresh->click();
    if (!require(waitUntil([&] { return fixture.requests.size() == request + 1; })
                     && fixture.requests.at(request).target == physicalTarget,
                 "Missing physical device caused an unselected query or automatic emulator fallback")) return false;
    fixture.respond(request, missing);
    if (!require(waitUntil([&] { return refresh->isEnabled(); }) && !phone->property("screenOn").toBool()
                     && selector->currentData().toString() == physical && selector->isEnabled()
                     && selector->findData(emulator) >= 0 && !selector->isHidden()
                     && model->text() == QStringLiteral("Não extraído"),
                 "Confirmed missing physical device rebound to the emulator or kept connected details")) return false;
  }
  refresh->click();
  if (!require(waitUntil([&] { return fixture.requests.size() == 6; }) && fixture.requests.at(5).target == physicalTarget,
               "Physical reconnect query lost its selected serial")) return false;
  auto reconnected = connectedSnapshot();
  reconnected.insert("devices", QJsonArray{QJsonObject{{"serial", physical}, {"state", "device"}},
      QJsonObject{{"serial", emulator}, {"state", "device"}}});
  fixture.respond(5, reconnected);
  if (!require(waitUntil([&] { return refresh->isEnabled(); }) && phone->property("screenOn").toBool()
                   && !phone->property("observationStale").toBool() && selector->currentData().toString() == physical,
               "Reconnecting the same physical device did not restore its observation")) return false;
  selector->setCurrentIndex(selector->findData(emulator));
  if (!require(waitUntil([&] { return fixture.requests.size() == 7; })
                   && fixture.requests.at(6).target == "/api/integrations/android/device?serial=" + QUrl::toPercentEncoding(emulator)
                   && !phone->property("screenOn").toBool() && model->text() == QStringLiteral("Não extraído")
                   && observation->property("checkedAt").toString().isEmpty(),
               "Explicit device switch retained physical-device details or used the wrong query")) return false;
  fixture.respond(6, connectedSnapshot(emulator, QStringLiteral("Emulador escolhido")));
  return require(waitUntil([&] { return refresh->isEnabled(); }) && phone->property("screenOn").toBool()
                     && model->text().contains(QStringLiteral("Emulador escolhido")) && fixture.onlyDiagnosticReads(),
                 "Explicit emulator selection did not complete its own diagnostic read");
}

bool destroyedPanelAndClient() {
  FixtureServer fixture;
  ApiClient api;
  fixture.configure(api);
  auto* panel = new AndroidDevicePanel(&api);
  panel->setServiceAvailable(true);
  panel->show();
  if (!require(waitUntil([&] { return fixture.requests.size() == 1; }), "Destruction fixture request missing")) return false;
  const QPointer<AndroidDevicePanel> guard(panel);
  delete panel;
  fixture.respond(0, connectedSnapshot());
  processFor(60);
  if (!require(guard.isNull(), "Panel ownership was not released")) return false;
  auto* scopedApi = new ApiClient;
  fixture.configure(*scopedApi);
  AndroidDevicePanel surviving(scopedApi);
  surviving.setServiceAvailable(true);
  surviving.show();
  if (!require(waitUntil([&] { return fixture.requests.size() == 2; }), "Client destruction fixture missing")) return false;
  delete scopedApi;
  processFor(20);
  return require(!child<QPushButton>(surviving, "androidRefreshButton")->isEnabled()
                     && child<QProgressBar>(surviving, "androidReadProgress")->isHidden()
                     && !child<QTimer>(surviving, "androidRefreshTimer")->isActive(),
                 "Destroyed API client left polling or pending progress active");
}

bool realRequestTimeout() {
  FixtureServer fixture;
  ApiClient api;
  fixture.configure(api);
  AndroidDevicePanel panel(&api);
  auto* refresh = child<QPushButton>(panel, "androidRefreshButton");
  auto* badge = child<QLabel>(panel, "androidConnectionBadge");
  auto* collection = child<QLabel>(panel, "androidCollectionStatus");
  auto* observation = child<QLabel>(panel, "androidLastObservation");
  auto* progress = child<QProgressBar>(panel, "androidReadProgress");
  auto* phone = child<QWidget>(panel, "androidPhoneIllustration");
  auto* model = child<QLabel>(panel, "androidModel");
  panel.renderSnapshot(connectedSnapshot());
  const QString checkedAt = observation->property("checkedAt").toString();
  QElapsedTimer elapsed;
  elapsed.start();
  panel.setServiceAvailable(true);
  panel.show();
  if (!require(waitUntil([&] { return fixture.requests.size() == 1; }) && phone->property("screenOn").toBool()
                   && badge->property("observationStale").toBool()
                   && observation->property("checkedAt").toString() == checkedAt,
               "Pending timeout fixture turned off a previously connected phone")) return false;
  // Hold the real loopback request open. This exercises ApiClient's production
  // deadline, not a simulated device or a shortened test-only timeout.
  if (!require(waitUntil([&] { return refresh->isEnabled(); }, 18000), "Diagnostic GET did not release its pending state on timeout")) return false;
  if (!require(elapsed.elapsed() >= 10000 && elapsed.elapsed() < 18000
                   && fixture.requests.size() == 1 && progress->isHidden()
                   && collection->text().contains(QStringLiteral("não respondeu a tempo"))
                   && badge->text().contains(QStringLiteral("Conectado"))
                   && phone->property("screenOn").toBool() && phone->property("observationStale").toBool()
                   && !badge->text().contains(QStringLiteral("Desconectado"))
                   && observation->property("checkedAt").toString() == checkedAt,
               "Timeout changed the phone state, timestamp, or allowed overlapping requests")) return false;
  refresh->click();
  if (!require(waitUntil([&] { return fixture.requests.size() == 2; }), "Timed out read could not be retried")) return false;
  fixture.respond(1, connectedSnapshot());
  if (!require(waitUntil([&] { return refresh->isEnabled(); }) && phone->property("screenOn").toBool()
                   && !badge->property("observationStale").toBool() && !observation->property("observationStale").toBool(),
               "Retry after timeout did not restore a fresh USB observation")) return false;
  refresh->click();
  if (!require(waitUntil([&] { return fixture.requests.size() == 3; }), "Confirmed disconnect request missing")) return false;
  auto disconnected = stateSnapshot(QStringLiteral("disconnected"));
  disconnected.insert("selected_serial", QStringLiteral("fixture-usb-A"));
  fixture.respond(2, disconnected);
  return require(waitUntil([&] { return refresh->isEnabled(); }) && !phone->property("screenOn").toBool()
                     && badge->text() == QStringLiteral("Desconectado") && model->text() == QStringLiteral("Não extraído")
                     && fixture.onlyDiagnosticReads(), "Confirmed disconnect after timeout/recovery kept the phone connected");
}

int preview(QApplication& app, const QStringList& arguments) {
  FixtureServer fixture;
  if (!fixture.server.isListening()) return 2;
  fixture.automatic = true;
  fixture.automaticAppCheck = {{"configured", true}, {"token_available", true}, {"source", "device"},
      {"device", QJsonObject{{"enabled", true}, {"device_id", "android.ssaid:fixture-device"},
          {"provider", "play_integrity"}, {"serial", "fixture-usb-A"},
          {"left_s", 1200}, {"token_len", 800}, {"token_available", true}}}};
  const int delayIndex = arguments.indexOf(QStringLiteral("--delay-ms"));
  bool validDelay = false;
  const int delay = delayIndex >= 0 ? arguments.value(delayIndex + 1).toInt(&validDelay) : 0;
  fixture.automaticDelayMs = validDelay ? qBound(0, delay, 12000) : 0;
  const int snapshotIndex = arguments.indexOf(QStringLiteral("--snapshot"));
  if (snapshotIndex >= 0) {
    QFile source(arguments.value(snapshotIndex + 1));
    if (!source.open(QIODevice::ReadOnly) || source.size() > 256 * 1024) return 2;
    const auto document = QJsonDocument::fromJson(source.readAll());
    if (!document.isObject()) return 2;
    fixture.automaticSnapshot = document.object();
  }
  ApiClient api;
  fixture.configure(api);
  QWidget window;
  window.setWindowTitle(QStringLiteral("QFar — Celular USB — QA"));
  auto* layout = new QVBoxLayout(&window);
  layout->setContentsMargins(24, 24, 24, 24);
  auto* heading = new QLabel(QStringLiteral("Celular USB"), &window);
  QFont headingFont = heading->font();
  headingFont.setPointSize(18);
  headingFont.setBold(true);
  heading->setFont(headingFont);
  layout->addWidget(heading);
  auto* panel = new AndroidDevicePanel(&api, &window);
  layout->addWidget(panel);
  layout->addStretch();
  window.resize(820, 560);
  panel->setServiceAvailable(true);
  app.setQuitOnLastWindowClosed(true);
  QTimer::singleShot(180000, &app, &QApplication::quit);
  window.show();
  const int outputIndex = arguments.indexOf(QStringLiteral("--output"));
  if (outputIndex >= 0) {
    QTimer::singleShot(500, &app, [&] {
      app.exit(window.grab().save(arguments.value(outputIndex + 1)) ? 0 : 2);
    });
  }
  return app.exec();
}
} // namespace

int main(int argc, char** argv) {
  QApplication app(argc, argv);
  app.setQuitOnLastWindowClosed(false);
  QNetworkProxy::setApplicationProxy(QNetworkProxy::NoProxy);
  if (app.arguments().contains(QStringLiteral("--preview"))) return preview(app, app.arguments());
  if (!appCheckStates()) return 8;
  if (!appCheckUsbObservationStates()) return 12;
  if (!appCheckReadFailurePreservesObservation()) return 13;
  if (!appCheckSelectionRefresh()) return 11;
  if (!appCheckLifecycle()) return 9;
  if (!appCheckActionFeedback()) return 10;
  if (!snapshotStates()) return 1;
  if (!refreshAndLifecycle()) return 2;
  if (!selectionAndServiceGeneration()) return 3;
  if (!usbEventsDuringPendingRead()) return 7;
  if (!selectedPhysicalDeviceRemainsSelected()) return 6;
  if (!destroyedPanelAndClient()) return 4;
  if (!realRequestTimeout()) return 5;
  std::cout << "Android public USB diagnostic panel contracts passed\n";
  return 0;
}
