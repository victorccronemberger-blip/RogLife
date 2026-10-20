// Native Windows fixtures only. No ADB commands, device I/O or real USB writes.
#include "../../desktop/src/UsbDeviceWatcher.hpp"

#include <QApplication>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QTimer>
#include <QWidget>
#include <cstddef>
#include <functional>
#include <iostream>
#include <memory>

#ifdef Q_OS_WIN
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <dbt.h>

namespace {
const GUID usbGuid{
    0xa5dcbf10, 0x6530, 0x11d2, {0x90, 0x1f, 0x00, 0xc0, 0x4f, 0xb9, 0x51, 0xed}};

bool require(bool condition, const char* message) {
  if (!condition) std::cerr << message << '\n';
  return condition;
}

void processFor(int milliseconds) {
  QEventLoop loop;
  QTimer::singleShot(milliseconds, &loop, &QEventLoop::quit);
  loop.exec();
}

bool waitUntil(const std::function<bool()>& predicate, int maximumMs = 1000) {
  QElapsedTimer elapsed;
  elapsed.start();
  while (!predicate() && elapsed.elapsed() < maximumMs) processFor(5);
  return predicate();
}

HWND nativeWindow(QWidget& owner) {
  owner.setAttribute(Qt::WA_NativeWindow);
  return reinterpret_cast<HWND>(quintptr(owner.winId()));
}

DEV_BROADCAST_DEVICEINTERFACE_W usbNotification() {
  DEV_BROADCAST_DEVICEINTERFACE_W notification{};
  notification.dbcc_size = sizeof(notification);
  notification.dbcc_devicetype = DBT_DEVTYP_DEVICEINTERFACE;
  notification.dbcc_classguid = usbGuid;
  return notification;
}

MSG nativeMessage(HWND window, WPARAM event, const void* payload,
                  UINT message = WM_DEVICECHANGE) {
  MSG notification{};
  notification.hwnd = window;
  notification.message = message;
  notification.wParam = event;
  notification.lParam = reinterpret_cast<LPARAM>(payload);
  return notification;
}

bool deliver(UsbDeviceWatcher& watcher, MSG message, int& changes, int expectedChange,
             const char* failure, const QByteArray& eventType = "windows_generic_MSG") {
  const int before = changes;
  qintptr result = 197;
  const bool consumed = watcher.nativeEventFilter(eventType, &message, &result);
  return require(!consumed && result == 197 && changes == before + expectedChange, failure);
}

bool registrationAndFiltering() {
  UsbDeviceWatcher watcher;
  int changes = 0;
  QObject::connect(&watcher, &UsbDeviceWatcher::topologyChanged, &watcher, [&] { ++changes; });
  QWidget owner, other;
  const HWND window = nativeWindow(owner), otherWindow = nativeWindow(other);
  if (!require(window && otherWindow && IsWindow(window) && IsWindow(otherWindow)
                   && !owner.isVisible() && !other.isVisible(), "Fixture windows must exist and stay hidden")) return false;
  if (!require(!watcher.isActive() && !watcher.start(nullptr) && !watcher.isActive(),
               "Null owner activated USB notifications")) return false;
  if (!require(watcher.start(&owner) && watcher.isActive() && watcher.start(&owner),
               "Native USB registration or repeated start failed")) return false;

  auto usb = usbNotification();
  if (!deliver(watcher, nativeMessage(window, DBT_DEVICEARRIVAL, &usb), changes, 1,
               "USB arrival was not emitted exactly once without consuming its message")) return false;
  if (!deliver(watcher, nativeMessage(window, DBT_DEVICEREMOVECOMPLETE, &usb), changes, 1,
               "USB removal was not emitted exactly once", "windows_dispatcher_MSG")) return false;
  if (!deliver(watcher, nativeMessage(otherWindow, DBT_DEVICEARRIVAL, &usb), changes, 0,
               "Another window's USB event reached the watcher")) return false;
  if (!deliver(watcher, nativeMessage(window, DBT_DEVNODES_CHANGED, &usb), changes, 0,
               "A generic hardware notification was accepted as a USB interface event")) return false;
  if (!deliver(watcher, nativeMessage(window, DBT_DEVICEARRIVAL, &usb, WM_APP + 31), changes, 0,
               "A non-device message emitted a USB event")) return false;
  if (!deliver(watcher, nativeMessage(window, DBT_DEVICEARRIVAL, &usb), changes, 0,
               "A non-Windows native event type was interpreted as MSG", "fixture_non_windows")) return false;
  if (!deliver(watcher, nativeMessage(window, DBT_DEVICEARRIVAL, nullptr), changes, 0,
               "An empty device payload emitted a USB event")) return false;
  qintptr result = 197;
  const int beforeNull = changes;
  if (!require(!watcher.nativeEventFilter("windows_generic_MSG", nullptr, &result)
                   && result == 197 && changes == beforeNull, "Null native message was not ignored safely")) return false;

  auto wrongClass = usb;
  wrongClass.dbcc_classguid.Data1 ^= 1;
  if (!deliver(watcher, nativeMessage(window, DBT_DEVICEARRIVAL, &wrongClass), changes, 0,
               "A different device-interface class emitted a USB event")) return false;
  auto wrongType = usb;
  wrongType.dbcc_devicetype = DBT_DEVTYP_VOLUME;
  if (!deliver(watcher, nativeMessage(window, DBT_DEVICEARRIVAL, &wrongType), changes, 0,
               "A volume notification emitted a USB interface event")) return false;
  auto truncated = usb;
  truncated.dbcc_size = offsetof(DEV_BROADCAST_DEVICEINTERFACE_W, dbcc_name) - 1;
  if (!deliver(watcher, nativeMessage(window, DBT_DEVICEARRIVAL, &truncated), changes, 0,
               "An incomplete GUID payload emitted a USB event")) return false;
  auto minimum = usb;
  minimum.dbcc_size = offsetof(DEV_BROADCAST_DEVICEINTERFACE_W, dbcc_name);
  if (!deliver(watcher, nativeMessage(window, DBT_DEVICEARRIVAL, &minimum), changes, 1,
               "A complete minimum-size USB GUID payload was rejected")) return false;

  if (!require(watcher.start(&other) && watcher.isActive() && watcher.start(&other),
               "Changing the native owner failed")) return false;
  if (!deliver(watcher, nativeMessage(window, DBT_DEVICEARRIVAL, &usb), changes, 0,
               "The previous native owner remained subscribed")) return false;
  if (!deliver(watcher, nativeMessage(otherWindow, DBT_DEVICEARRIVAL, &usb), changes, 1,
               "The replacement native owner was not subscribed exactly once")) return false;
  watcher.stop();
  watcher.stop();
  if (!require(!watcher.isActive(), "Repeated stop kept USB notifications active")) return false;
  if (!deliver(watcher, nativeMessage(otherWindow, DBT_DEVICEARRIVAL, &usb), changes, 0,
               "A stopped watcher emitted a USB event")) return false;
  if (!require(watcher.start(&owner) && !watcher.start(nullptr) && !watcher.isActive(),
               "Starting with a null owner did not stop the existing subscription")) return false;
  return require(!owner.isVisible() && !other.isVisible(), "USB registration showed a fixture window");
}

bool nativeQtDelivery() {
  UsbDeviceWatcher watcher;
  int changes = 0;
  QObject::connect(&watcher, &UsbDeviceWatcher::topologyChanged, &watcher, [&] { ++changes; });
  QWidget owner, other;
  const HWND window = nativeWindow(owner), otherWindow = nativeWindow(other);
  if (!require(watcher.start(&owner) && watcher.start(&owner), "Native delivery registration failed")) return false;
  auto usb = usbNotification();

  // WM_DEVICECHANGE is below WM_USER and carries a pointer. PostMessage cannot
  // safely post such system-message payloads; deliver synchronously from a Qt
  // queued callback while this typed fixture remains alive on the stack.
  QTimer::singleShot(0, &owner, [&] {
    SendMessageW(window, WM_DEVICECHANGE, DBT_DEVICEARRIVAL, reinterpret_cast<LPARAM>(&usb));
  });
  if (!require(waitUntil([&] { return changes > 0; }), "Installed native filter missed a real HWND USB arrival")) return false;
  processFor(30);
  if (!require(changes == 1, "Repeated start installed duplicate native filters")) return false;
  QTimer::singleShot(0, &owner, [&] {
    SendMessageW(window, WM_DEVICECHANGE, DBT_DEVICEREMOVECOMPLETE, reinterpret_cast<LPARAM>(&usb));
  });
  if (!require(waitUntil([&] { return changes > 1; }) && changes == 2,
               "Installed native filter missed or duplicated a real HWND USB removal")) return false;

  auto wrongClass = usb;
  wrongClass.dbcc_classguid.Data1 ^= 1;
  QTimer::singleShot(0, &owner, [&] {
    SendMessageW(window, WM_DEVICECHANGE, DBT_DEVICEARRIVAL, reinterpret_cast<LPARAM>(&wrongClass));
    SendMessageW(otherWindow, WM_DEVICECHANGE, DBT_DEVICEARRIVAL, reinterpret_cast<LPARAM>(&usb));
  });
  if (!require(PostMessageW(window, WM_DEVICECHANGE, DBT_DEVNODES_CHANGED, 0)
                   && PostMessageW(window, WM_APP + 31, 0, 0),
               "Pointer-free native fixture messages could not be queued")) return false;
  processFor(50);
  if (!require(changes == 2, "Unrelated or pointer-free queued messages emitted USB changes")) return false;

  QTimer::singleShot(0, &owner, [&] {
    SendMessageW(window, WM_DEVICECHANGE, DBT_DEVICEARRIVAL, reinterpret_cast<LPARAM>(&usb));
  });
  watcher.stop();
  processFor(50);
  if (!require(changes == 2 && !watcher.isActive(), "A queued notification survived stop")) return false;
  if (!require(watcher.start(&owner), "Native watcher did not restart after stop")) return false;
  QTimer::singleShot(0, &owner, [&] {
    SendMessageW(window, WM_DEVICECHANGE, DBT_DEVICEARRIVAL, reinterpret_cast<LPARAM>(&usb));
  });
  if (!require(waitUntil([&] { return changes > 2; }) && changes == 3,
               "Restarted watcher missed or duplicated a USB event")) return false;
  watcher.stop();
  return require(!owner.isVisible() && !other.isVisible(), "Native message delivery showed a fixture window");
}

bool ownerAndWatcherDestruction() {
  UsbDeviceWatcher watcher;
  int changes = 0;
  QObject::connect(&watcher, &UsbDeviceWatcher::topologyChanged, &watcher, [&] { ++changes; });
  auto owner = std::make_unique<QWidget>();
  const HWND expiredWindow = nativeWindow(*owner);
  if (!require(watcher.start(owner.get()) && watcher.isActive(), "Owner destruction fixture registration failed")) return false;
  owner.reset();
  if (!require(!watcher.isActive(), "Destroyed owner left USB registration active")) return false;
  auto usb = usbNotification();
  if (!deliver(watcher, nativeMessage(expiredWindow, DBT_DEVICEARRIVAL, &usb), changes, 0,
               "Destroyed owner's HWND still emitted USB events")) return false;
  watcher.stop();

  QWidget replacement;
  const HWND replacementWindow = nativeWindow(replacement);
  if (!require(watcher.start(&replacement) && watcher.isActive(), "Watcher did not recover after owner destruction")) return false;
  watcher.stop();
  auto temporaryWatcher = std::make_unique<UsbDeviceWatcher>();
  QObject::connect(temporaryWatcher.get(), &UsbDeviceWatcher::topologyChanged, &replacement, [&] { ++changes; });
  if (!require(temporaryWatcher->start(&replacement), "Watcher destruction fixture registration failed")) return false;
  temporaryWatcher.reset();
  QTimer::singleShot(0, &replacement, [&] {
    SendMessageW(replacementWindow, WM_DEVICECHANGE, DBT_DEVICEARRIVAL, reinterpret_cast<LPARAM>(&usb));
  });
  processFor(50);
  return require(changes == 0 && !replacement.isVisible(), "Destroyed watcher left a native filter installed");
}
} // namespace
#endif

int main(int argc, char** argv) {
  QApplication app(argc, argv);
  app.setQuitOnLastWindowClosed(false);
#ifdef Q_OS_WIN
  if (!registrationAndFiltering()) return 1;
  if (!nativeQtDelivery()) return 2;
  if (!ownerAndWatcherDestruction()) return 3;
  std::cout << "Native USB topology watcher contracts passed\n";
  return 0;
#else
  std::cerr << "The native USB watcher contract requires Windows\n";
  return 1;
#endif
}
