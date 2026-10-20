#include "ApiClient.hpp"

#include <QJsonObject>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QUrl>
#include <QUrlQuery>
#include <QTimer>
#include <QUuid>

ApiClient::ApiClient(QObject* parent) : QObject(parent), _network(this) {}

void ApiClient::setBaseUrl(const QString& baseUrl) {
  _baseUrl = baseUrl;
  while (_baseUrl.endsWith('/')) _baseUrl.chop(1);
}

void ApiClient::get(const QString& path, Callback callback, ProgressCallback progress) {
  if (path.section(QLatin1Char('?'), 0, 0) == QStringLiteral("/api/tasks")) ++_taskRequestEpoch;
  QString selected = path;
#ifdef QMONEY_LOCAL_ONLY
  if (path.startsWith(QStringLiteral("/api/tasks?")) && !path.contains(QStringLiteral("async=")))
    selected += QStringLiteral("&async=1");
#endif
  request("GET", selected, nullptr, std::move(callback), 0, {}, std::move(progress));
}

void ApiClient::post(const QString& path, const QJsonObject& body, Callback callback, ProgressCallback progress) {
  QString selected = path;
#ifdef QMONEY_LOCAL_ONLY
  if (path == QStringLiteral("/api/campaigns/preflight"))
    selected += QStringLiteral("?async=1&request_id=") + QUuid::createUuid().toString(QUuid::Id128);
#endif
  request("POST", selected, &body, std::move(callback), 0, {}, std::move(progress));
}

void ApiClient::put(const QString& path, const QJsonObject& body, Callback callback) {
  request("PUT", path, &body, std::move(callback));
}

void ApiClient::remove(const QString& path, Callback callback) {
  request("DELETE", path, nullptr, std::move(callback));
}

void ApiClient::request(const QByteArray& method, const QString& path,
                        const QJsonObject* body, Callback callback, int polls,
                        std::shared_ptr<PollState> state, ProgressCallback progress) {
  if (!state) {
    state = std::make_shared<PollState>();
    state->total.start(); state->idle.start();
  }
  const QJsonObject savedBody = body ? *body : QJsonObject{};
  QNetworkRequest req(QUrl(_baseUrl + path));
  req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
  req.setRawHeader("Accept", "application/json");
  if (!_sessionToken.isEmpty()) req.setRawHeader("X-QMoney-Session", _sessionToken);
  // Consultas sem resposta precisam liberar o polling para uma nova tentativa.
  // Escritas produtivas não são repetidas. Só a prévia idempotente pode
  // recuperar uma consulta interrompida, mantendo identificador e corpo.
  if (method == "GET")
    req.setTransferTimeout(path == QStringLiteral("/api/health") ? 3000
        : (path == QStringLiteral("/api/campaigns/current") || path == QStringLiteral("/api/accounts")
           || path == QStringLiteral("/api/accounts/bulk-register/status")) ? 10000 : 60000);
  if (method == "GET" && path.section(QLatin1Char('?'), 0, 0)
                            == QStringLiteral("/api/integrations/android/device"))
    req.setTransferTimeout(12000);
  const bool startsRegistration = path == QStringLiteral("/api/accounts/register?async=1")
      || path == QStringLiteral("/api/accounts/bulk-register") || (path.startsWith(QStringLiteral("/api/accounts/")) && path.endsWith(QStringLiteral("/resume")));
  if (method == "POST" && (startsRegistration || path == QStringLiteral("/api/accounts/bulk-register/stop")))
    req.setTransferTimeout(15000);
  if (method == "POST" && (path == QStringLiteral("/api/accounts/password")
                           || path == QStringLiteral("/api/accounts/banned/password")))
    req.setTransferTimeout(60000);
  if (method == "POST" && path == QStringLiteral("/api/campaigns"))
    req.setTransferTimeout(60000);
  if (method == "POST" && path == QStringLiteral("/api/library/ego4d/index"))
    req.setTransferTimeout(15000);
  if (method == "POST" && path.section(QLatin1Char('?'), 0, 0) == QStringLiteral("/api/catalogs/providers/prepare"))
    req.setTransferTimeout(15000);

  if (method == "POST" && path == QStringLiteral("/api/campaigns/preflight"))
    req.setTransferTimeout(180000);
  else if (method == "POST" && path.startsWith(QStringLiteral("/api/campaigns/preflight?")))
    req.setTransferTimeout(15000);
  else if (method == "POST" && path == QStringLiteral("/api/integrations/appcheck/recheck"))
    // Soft ADB is seconds; hard Minute restart can approach ~6 min. Bound the
    // wait so Atualizar cannot leave the panel pending forever.
    req.setTransferTimeout(420000);
  else if (method == "POST" && (path.startsWith(QStringLiteral("/api/campaigns/"))
                               || path == QStringLiteral("/api/campaign/reset")
                               || path == QStringLiteral("/api/sent/reset")))
    req.setTransferTimeout(60000);

  QNetworkReply* reply = nullptr;
  const QByteArray payload = body ? QJsonDocument(*body).toJson(QJsonDocument::Compact) : QByteArray();
  if (method == "GET") reply = _network.get(req);
  else if (method == "POST") reply = _network.post(req, payload);
  else if (method == "PUT") reply = _network.put(req, payload);
  else reply = _network.sendCustomRequest(req, method, payload);

  const QString route = path.section(QLatin1Char('?'), 0, 0);
  const bool providerPreparation = method == "POST" && route == QStringLiteral("/api/catalogs/providers/prepare");
  const bool campaignRequest = route == QStringLiteral("/api/tasks")
      || route == QStringLiteral("/api/logs") || route.startsWith(QStringLiteral("/api/logs/"))
      || route == QStringLiteral("/api/recovery") || route.startsWith(QStringLiteral("/api/recovery/"))
      || route == QStringLiteral("/api/campaigns") || route.startsWith(QStringLiteral("/api/campaigns/"))
      || route == QStringLiteral("/api/campaign/reset") || route == QStringLiteral("/api/sent/reset");
  const quint64 campaignEpoch = _campaignRequestEpoch;
  const bool taskRequest = method == "GET" && route == QStringLiteral("/api/tasks");
  const quint64 taskEpoch = _taskRequestEpoch;
  connect(reply, &QNetworkReply::finished, this, [this, reply, method, path, startsRegistration, providerPreparation, campaignRequest, campaignEpoch, taskRequest, taskEpoch, savedBody, polls, state, progress, callback = std::move(callback)]() mutable {
    if ((campaignRequest && campaignEpoch != _campaignRequestEpoch)
        || (taskRequest && taskEpoch != _taskRequestEpoch)) {
      reply->deleteLater();
      return;
    }
    const QByteArray bytes = reply->readAll();
    QJsonParseError parseError;
    QJsonDocument doc = QJsonDocument::fromJson(bytes, &parseError);
    const int status = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
    const bool ok = reply->error() == QNetworkReply::NoError && status >= 200 && status < 300;
#ifdef QMONEY_LOCAL_ONLY
    const QString route = path.section(QLatin1Char('?'), 0, 0);
    const bool controlledRead = route == QStringLiteral("/api/tasks")
        || route == QStringLiteral("/api/catalogs/providers")
        || route == QStringLiteral("/api/catalogs/providers/prepare")
        || route == QStringLiteral("/api/integrations/ego4d/catalog")
        || route == QStringLiteral("/api/recovery")
        || route == QStringLiteral("/api/campaigns/preflight");
    if (ok && doc.isObject() && controlledRead && doc.object().value(QStringLiteral("loading")).toBool()) {
      reply->deleteLater();
      const QString signature = doc.object().value("phase").toString()
          + QLatin1Char('\n') + doc.object().value("message").toString()
          + QLatin1Char('\n') + QString::fromUtf8(QJsonDocument(QJsonObject{
              {"completed", doc.object().value("completed")},
              {"total", doc.object().value("total")}}).toJson(QJsonDocument::Compact));
      if (signature != state->progress) {
        state->progress = signature;
        state->idlePolls = 0; state->idle.restart();
      }
      ++state->idlePolls;
      if (progress) progress(doc.object());
      const bool progressBounded = route == QStringLiteral("/api/tasks")
          || route == QStringLiteral("/api/campaigns/preflight") || route.startsWith(QStringLiteral("/api/catalogs/providers"));
      const bool queued = doc.object().value(QStringLiteral("state")).toString() == QStringLiteral("queued");
      if (progressBounded ? (queued ? (polls >= 3600 || state->total.elapsed() >= 1800000)
                                   : pollLimitReached(polls, state->idlePolls, state->total.elapsed(), state->idle.elapsed()))
                          : polls >= 1800) {
        callback(false, doc, QStringLiteral("A consulta continua no serviço. Recarregue para recuperar o resultado; nenhum envio foi iniciado."));
        return;
      }
      QString nextPath = path;
      QByteArray nextMethod = method;
      const QString jobId = doc.object().value(QStringLiteral("job_id")).toString();
      if (route == QStringLiteral("/api/catalogs/providers/prepare")) {
        nextMethod = "GET";
        nextPath.replace(QStringLiteral("/api/catalogs/providers/prepare"), QStringLiteral("/api/catalogs/providers"));
      }
      if (nextMethod == "GET" && !jobId.isEmpty()) {
        QUrl url(nextPath); QUrlQuery query(url);
        query.removeAllQueryItems(QStringLiteral("job_id"));
        query.addQueryItem(QStringLiteral("job_id"), jobId); url.setQuery(query);
        nextPath = url.toString();
      }
      QTimer::singleShot(_pollDelayMs, this, [this, nextMethod, nextPath, savedBody, polls, state, progress, campaignRequest, campaignEpoch, taskRequest, taskEpoch,
                                   callback = std::move(callback)]() mutable {
        if ((campaignRequest && campaignEpoch != _campaignRequestEpoch)
            || (taskRequest && taskEpoch != _taskRequestEpoch)) return;
        request(nextMethod, nextPath, nextMethod == "GET" ? nullptr : &savedBody, std::move(callback), polls + 1, state, progress);
      });
      return;
    }
    const bool preflight = method == "POST" && route == QStringLiteral("/api/campaigns/preflight");
    const bool interrupted = status == 0 && (reply->error() == QNetworkReply::TimeoutError
        || reply->error() == QNetworkReply::OperationCanceledError
        || reply->error() == QNetworkReply::RemoteHostClosedError
        || reply->error() == QNetworkReply::ConnectionRefusedError);
    const bool pending = doc.object().value("loading") == QJsonValue(true)
        && doc.object().value("code") == QJsonValue(QStringLiteral("catalog_work_pending"));
    if (!ok && preflight && (interrupted || pending) && state->recoveries < 4
        && !pollLimitReached(polls, state->idlePolls, state->total.elapsed(), state->idle.elapsed())) {
      ++state->recoveries;
      reply->deleteLater();
      if (progress) progress(QJsonObject{{"message", QStringLiteral("Recuperando a mesma verificação (%1/4). Nenhum envio iniciado.").arg(state->recoveries)}});
      QTimer::singleShot(_pollDelayMs, this, [this, method, path, savedBody, polls, state, progress,
                                           campaignRequest, campaignEpoch, callback = std::move(callback)]() mutable {
        if (campaignRequest && campaignEpoch != _campaignRequestEpoch) return;
        request(method, path, &savedBody, std::move(callback), polls + 1, state, progress);
      });
      return;
    }
#endif
    QString error;
    if (!ok) {
      if (doc.isObject()) error = doc.object().value(QStringLiteral("error")).toString();
      const bool timedOut = status == 0 && (reply->error() == QNetworkReply::TimeoutError
          || reply->error() == QNetworkReply::OperationCanceledError);
      if (error.isEmpty()) error = timedOut
          ? QStringLiteral("O serviço local não respondeu a tempo. Confira se o QMoney está aberto e tente novamente. "
                           "Se o problema persistir, use Corrigir instalação.")
          : status == 0 ? QStringLiteral("Não foi possível comunicar com o serviço local: %1. "
                                        "Use Corrigir instalação se o problema persistir.").arg(reply->errorString())
                        : reply->errorString();
      if (status) error = QStringLiteral("%1 (HTTP %2)").arg(error).arg(status);
      else doc = QJsonDocument(QJsonObject{{"error_code", "request_outcome_unknown"},
                                           {"transport_error", timedOut ? "timeout" : "connection"}});
    } else if (parseError.error != QJsonParseError::NoError || !doc.isObject()) {
      error = QStringLiteral("O serviço retornou uma resposta JSON inválida ou incompleta.");
      if (method == "POST" && (path == QStringLiteral("/api/campaigns") || startsRegistration || providerPreparation))
        doc = QJsonDocument(QJsonObject{{"error_code", "request_outcome_unknown"}});
    } else if (method == "POST" && path == QStringLiteral("/api/campaign/reset")
               && doc.object().value(QStringLiteral("ok")) != QJsonValue(true)) {
      error = doc.object().value(QStringLiteral("error")).toString(
          QStringLiteral("O serviço não confirmou o reset completo. Os dados exibidos foram preservados."));
    } else if (method == "POST" && path == QStringLiteral("/api/campaigns")
               && !doc.object().value(QStringLiteral("ok")).toBool()) {
      error = doc.object().value(QStringLiteral("error")).toString(
          QStringLiteral("O serviço não confirmou o início da campanha."));
      if (!doc.object().contains(QStringLiteral("ok")))
        doc = QJsonDocument(QJsonObject{{"error_code", "request_outcome_unknown"}});
    }
    if (!ok && method == "POST" && (path == QStringLiteral("/api/campaigns") || startsRegistration || providerPreparation)
        && (status == 0 || status >= 500) && doc.object().value("error_code").toString().isEmpty()) {
      auto uncertain = doc.object();
      uncertain.insert(QStringLiteral("error_code"), QStringLiteral("request_outcome_unknown"));
      doc = QJsonDocument(uncertain);
    }
    reply->deleteLater();
    callback(ok && error.isEmpty(), doc, error);
  });
}
