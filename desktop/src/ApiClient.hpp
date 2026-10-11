#pragma once

#include <QJsonDocument>
#include <QElapsedTimer>
#include <QNetworkAccessManager>
#include <QObject>
#include <functional>
#include <memory>

class ApiClient final : public QObject {
  Q_OBJECT

public:
  using Callback = std::function<void(bool, const QJsonDocument&, const QString&)>;
  using ProgressCallback = std::function<void(const QJsonObject&)>;

  explicit ApiClient(QObject* parent = nullptr);

  void setBaseUrl(const QString& baseUrl);
  void setSessionToken(const QByteArray& token) { _sessionToken = token; }
  void get(const QString& path, Callback callback, ProgressCallback progress = {});
  void post(const QString& path, const QJsonObject& body, Callback callback, ProgressCallback progress = {});
  void put(const QString& path, const QJsonObject& body, Callback callback);
  void remove(const QString& path, Callback callback);
  // Discard replies belonging to local campaign state from before a reset.
  void invalidateCampaignRequests() { ++_campaignRequestEpoch; }
  void cancelTaskCatalogRequests() { ++_taskRequestEpoch; }

private:
  friend class LocalEditionTests;
  struct PollState {
    QElapsedTimer total, idle;
    QString progress;
    int idlePolls{}, recoveries{};
  };
  static bool pollLimitReached(int polls, int idlePolls, qint64 elapsed, qint64 idle) {
    return polls >= 3600 || elapsed >= 1800000 || idlePolls >= 600 || idle >= 300000;
  }
  void request(const QByteArray& method, const QString& path,
               const QJsonObject* body, Callback callback, int polls = 0,
               std::shared_ptr<PollState> state = {}, ProgressCallback progress = {});
  int _pollDelayMs{500};

  QNetworkAccessManager _network;
  QByteArray _sessionToken;
  quint64 _campaignRequestEpoch{};
  quint64 _taskRequestEpoch{};
  QString _baseUrl{QStringLiteral("http://127.0.0.1:8876")};
};
