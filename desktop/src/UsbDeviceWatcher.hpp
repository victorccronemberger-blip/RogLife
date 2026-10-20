#pragma once

#include <QAbstractNativeEventFilter>
#include <QObject>
#include <QPointer>

class QCoreApplication;
class QWidget;

class UsbDeviceWatcher final : public QObject, public QAbstractNativeEventFilter {
  Q_OBJECT

public:
  explicit UsbDeviceWatcher(QObject* parent = nullptr);
  ~UsbDeviceWatcher() override;

  bool start(QWidget* owner);
  void stop();
  bool isActive() const;
  bool nativeEventFilter(const QByteArray& eventType, void* message, qintptr* result) override;

signals:
  void topologyChanged();

private:
  QPointer<QWidget> _owner;
  QPointer<QCoreApplication> _application;
  QMetaObject::Connection _ownerDestroyed;
  QMetaObject::Connection _applicationDestroyed;
  void* _notificationHandle{};
  quintptr _windowId{};
};
