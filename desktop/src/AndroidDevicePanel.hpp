#pragma once

#include <QDateTime>
#include <QJsonObject>
#include <QPointer>
#include <QSet>
#include <QWidget>

class ApiClient;
class QComboBox;
class QHideEvent;
class QLabel;
class QProgressBar;
class QPushButton;
class QShowEvent;
class QTimer;
class UsbDeviceWatcher;

// Read-only public USB diagnostics. The API client remains owned by the shell.
class AndroidDevicePanel final : public QWidget {
  Q_OBJECT

public:
  explicit AndroidDevicePanel(ApiClient* api, QWidget* parent = nullptr);
  void setServiceAvailable(bool available);
  void renderSnapshot(const QJsonObject& snapshot);
  void renderAppCheckStatus(const QJsonObject& status);

protected:
  void showEvent(QShowEvent* event) override;
  void hideEvent(QHideEvent* event) override;

private:
  void requestRefresh();
  void requestAppCheckStatus();
  void requestAppCheckRefresh();
  void updateAppCheckObservationState();
  void startUsbWatcher();
  void selectDevice(int index);
  void updateRequestUi();
  void setConnectionBadge(const QString& state, bool stale = false);
  void showRequestError(const QString& error);
  void updateLastObservation();
  void clearDeviceDetails();
  void setPhoneScreen(bool on, bool stale = false);

  QPointer<ApiClient> _api;
  QJsonObject _appCheckSnapshot;
  QTimer* _refreshTimer{};
  QTimer* _appCheckTimer{};
  UsbDeviceWatcher* _usbWatcher{};
  QLabel* _usbEventStatus{};
  QLabel* _connectionBadge{};
  QLabel* _appCheckStatus{};
  QLabel* _appCheckActionStatus{};
  QLabel* _selectionLabel{};
  QComboBox* _deviceSelector{};
  QWidget* _phoneIllustration{};
  QLabel* _model{};
  QLabel* _serial{};
  QLabel* _androidVersion{};
  QLabel* _rootStatus{};
  QLabel* _collectionStatus{};
  QLabel* _lastObservation{};
  QLabel* _appCheckDeviceId{};
  QLabel* _appCheckProvider{};
  QLabel* _appCheckValidity{};
  QLabel* _appCheckTokenPrefix{};
  QProgressBar* _readProgress{};
  QPushButton* _refreshButton{};
  QSet<QString> _returnedSerials;
  QString _selectedSerial;
  QString _connectionState{QStringLiteral("unknown")};
  QString _lastObservationSerial;
  QDateTime _lastSuccessfulObservation;
  quint64 _generation{};
  quint64 _activeRequest{};
  quint64 _appCheckRequest{};
  bool _serviceAvailable{};
  bool _requestPending{};
  bool _appCheckPending{};
  bool _appCheckStatusReadFailed{};
  bool _refreshQueued{};
};
