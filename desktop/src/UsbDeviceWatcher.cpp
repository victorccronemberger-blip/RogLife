#include "UsbDeviceWatcher.hpp"

#include <QCoreApplication>
#include <QWidget>

#ifdef Q_OS_WIN
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <dbt.h>
#include <cstddef>

namespace {
const GUID usbDeviceInterfaceGuid{
    0xa5dcbf10, 0x6530, 0x11d2, {0x90, 0x1f, 0x00, 0xc0, 0x4f, 0xb9, 0x51, 0xed}};
}
#endif

UsbDeviceWatcher::UsbDeviceWatcher(QObject* parent) : QObject(parent) {}

UsbDeviceWatcher::~UsbDeviceWatcher() {
  stop();
}

bool UsbDeviceWatcher::start(QWidget* owner) {
#ifdef Q_OS_WIN
  auto* application = QCoreApplication::instance();
  if (!owner || !application || owner->thread() != thread() || application->thread() != thread()) {
    stop();
    return false;
  }
  const auto windowId = quintptr(owner->winId());
  if (isActive() && _owner == owner && _application == application && _windowId == windowId)
    return true;
  stop();
  const auto window = reinterpret_cast<HWND>(windowId);
  if (!window || !IsWindow(window)) return false;

  DEV_BROADCAST_DEVICEINTERFACE_W filter{};
  filter.dbcc_size = sizeof(filter);
  filter.dbcc_devicetype = DBT_DEVTYP_DEVICEINTERFACE;
  filter.dbcc_classguid = usbDeviceInterfaceGuid;
  const auto notification = RegisterDeviceNotificationW(window, &filter, DEVICE_NOTIFY_WINDOW_HANDLE);
  if (!notification) return false;

  _notificationHandle = notification;
  _windowId = windowId;
  _owner = owner;
  _application = application;
  application->installNativeEventFilter(this);
  _ownerDestroyed = connect(owner, &QObject::destroyed, this, [this] { stop(); });
  _applicationDestroyed = connect(application, &QObject::destroyed, this, [this] { stop(); });
  return true;
#else
  Q_UNUSED(owner)
  return false;
#endif
}

void UsbDeviceWatcher::stop() {
  if (_application) _application->removeNativeEventFilter(this);
  disconnect(_ownerDestroyed);
  disconnect(_applicationDestroyed);
  _ownerDestroyed = {};
  _applicationDestroyed = {};
#ifdef Q_OS_WIN
  if (_notificationHandle)
    UnregisterDeviceNotification(static_cast<HDEVNOTIFY>(_notificationHandle));
#endif
  _notificationHandle = nullptr;
  _windowId = 0;
  _owner.clear();
  _application.clear();
}

bool UsbDeviceWatcher::isActive() const {
  return _notificationHandle && !_owner.isNull() && !_application.isNull();
}

bool UsbDeviceWatcher::nativeEventFilter(const QByteArray& eventType, void* message, qintptr* result) {
  Q_UNUSED(result)
#ifdef Q_OS_WIN
  if (!isActive() || !message
      || (eventType != "windows_generic_MSG" && eventType != "windows_dispatcher_MSG"))
    return false;
  const auto* nativeMessage = static_cast<const MSG*>(message);
  if (nativeMessage->message != WM_DEVICECHANGE
      || reinterpret_cast<quintptr>(nativeMessage->hwnd) != _windowId
      || (nativeMessage->wParam != DBT_DEVICEARRIVAL && nativeMessage->wParam != DBT_DEVICEREMOVECOMPLETE)
      || !nativeMessage->lParam)
    return false;

  const auto* header = reinterpret_cast<const DEV_BROADCAST_HDR*>(nativeMessage->lParam);
  if (header->dbch_size < offsetof(DEV_BROADCAST_DEVICEINTERFACE_W, dbcc_name)
      || header->dbch_devicetype != DBT_DEVTYP_DEVICEINTERFACE)
    return false;
  const auto* deviceInterface = reinterpret_cast<const DEV_BROADCAST_DEVICEINTERFACE_W*>(header);
  if (IsEqualGUID(deviceInterface->dbcc_classguid, usbDeviceInterfaceGuid)) emit topologyChanged();
#else
  Q_UNUSED(eventType)
  Q_UNUSED(message)
#endif
  // Keep the native event available to Qt and every other subscriber.
  return false;
}
