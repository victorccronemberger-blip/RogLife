// Mobile App Check UI methods; widget ownership stays in MainWindow.
#include "MainWindowSupport.hpp"
#include "MainWindow.hpp"

void MainWindow::renderAppCheckSnapshot(const QJsonObject& snapshot) {
  if (!_appCheckStatus || !_appCheckRecheck) return;
  const bool configured = snapshot.value(QStringLiteral("configured")).toBool();
  const bool configurationPresent = snapshot.value(QStringLiteral("configuration_present")).toBool();
  const bool tokenAvailable = snapshot.value(QStringLiteral("token_available")).toBool();
  const bool paused = snapshot.value(QStringLiteral("paused")).toBool();
  const int retryAfter = qMax(0, snapshot.value(QStringLiteral("retry_after_seconds")).toInt());
  const auto diagnostic = snapshot.value(QStringLiteral("diagnostic")).toObject();
  QString status;
  if (!configurationPresent) {
    status = QStringLiteral("App Check não está configurado nesta instalação.");
  } else if (!configured) {
    status = QStringLiteral("Configuração presente, mas incompleta; nenhum token foi confirmado.");
  } else if (tokenAvailable) {
    status = QStringLiteral("Token App Check disponível em cache. O aceite pelo Minute ainda não foi confirmado.");
  } else {
    const QString detail = diagnostic.value(QStringLiteral("message")).toString();
    status = detail.isEmpty()
        ? QStringLiteral("Configuração presente; ainda não há token App Check disponível nesta sessão.")
        : QStringLiteral("Configuração presente, mas a última tentativa não emitiu token: %1").arg(detail);
    const QJsonValue httpStatusValue = diagnostic.value(QStringLiteral("http_status"));
    const QString firebaseStatus = diagnostic.value(QStringLiteral("firebase_status")).toString();
    static const QSet<QString> publicFirebaseStatuses{
        QStringLiteral("CANCELLED"), QStringLiteral("UNKNOWN"),
        QStringLiteral("INVALID_ARGUMENT"), QStringLiteral("DEADLINE_EXCEEDED"),
        QStringLiteral("NOT_FOUND"), QStringLiteral("ALREADY_EXISTS"),
        QStringLiteral("PERMISSION_DENIED"), QStringLiteral("RESOURCE_EXHAUSTED"),
        QStringLiteral("FAILED_PRECONDITION"), QStringLiteral("ABORTED"),
        QStringLiteral("OUT_OF_RANGE"), QStringLiteral("UNAUTHENTICATED"),
        QStringLiteral("INTERNAL"), QStringLiteral("UNAVAILABLE"),
        QStringLiteral("DATA_LOSS")};
    const QString safeFirebaseStatus = publicFirebaseStatuses.contains(firebaseStatus)
        ? firebaseStatus : QString{};
    if (httpStatusValue.isDouble()) {
      const int httpStatus = httpStatusValue.toInt();
      if (httpStatus >= 100 && httpStatus <= 599) {
        status += safeFirebaseStatus.isEmpty()
            ? QStringLiteral(" (Firebase HTTP %1)").arg(httpStatus)
            : QStringLiteral(" (Firebase HTTP %1 / %2)").arg(httpStatus).arg(safeFirebaseStatus);
      }
    } else if (!safeFirebaseStatus.isEmpty()) {
      status += QStringLiteral(" (Firebase %1)").arg(safeFirebaseStatus);
    }
  }
  if (paused)
    status += QStringLiteral(" Rechecagem pausada localmente.");
  else if (retryAfter > 0)
    status += QStringLiteral(" Aguarde %1 s; depois use Atualizar conexões.").arg(retryAfter);
  _appCheckStatus->setText(status);
  _appCheckRecheck->setEnabled(configurationPresent && configured && !paused && retryAfter == 0
                               && !_appCheckRecheckPending);
  _appCheckRecheck->setText(_appCheckRecheckPending
                                ? QStringLiteral("Rechecando…")
                                : QStringLiteral("Rechecar App Check"));
}

void MainWindow::recheckAppCheck() {
  if (!_appCheckRecheck || !_appCheckRecheck->isEnabled() || _appCheckRecheckPending) return;
  _appCheckRecheckPending = true;
  _appCheckRecheck->setEnabled(false);
  _appCheckRecheck->setText(QStringLiteral("Rechecando…"));
  const int generation = _operationBackendGeneration;
  _api.post(QStringLiteral("/api/integrations/appcheck/recheck"), QJsonObject{},
            [this, generation](bool ok, const QJsonDocument& doc, const QString&) {
    if (_closing) return;
    if (generation != _operationBackendGeneration) {
      _appCheckRecheckPending = false;
      if (_appCheckRecheck) {
        _appCheckRecheck->setText(QStringLiteral("Rechecar App Check"));
        _appCheckRecheck->setEnabled(false);
      }
      return;
    }
    _appCheckRecheckPending = false;
    if (!_appCheckStatus || !_appCheckRecheck) return;
    if (!ok) {
      _appCheckStatus->setText(QStringLiteral(
          "Não foi possível consultar o resultado da rechecagem. Atualize Integrações para conferir o estado."));
      _appCheckRecheck->setText(QStringLiteral("Rechecar App Check"));
      return;
    }
    const auto result = doc.object();
    renderAppCheckSnapshot(result.value(QStringLiteral("appcheck")).toObject());
    const QString message = result.value(QStringLiteral("message")).toString();
    if (!message.isEmpty()) setStatus(message);
  });
}

