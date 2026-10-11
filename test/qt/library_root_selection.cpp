#include "../../desktop/src/LibraryRootSelection.hpp"
#include "../../desktop/src/PackagedServiceEnvironment.hpp"
#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QProcessEnvironment>
#include <QTemporaryDir>

int main(int argc, char** argv) {
  QCoreApplication app(argc, argv);
  QTemporaryDir temporary;
  if (!temporary.isValid()) return 1;
  const QString root = temporary.path();
  const QString saved = root + QStringLiteral("/other-user/empty library");
  const QString installed = root + QStringLiteral("/portable");
  const QString user = root + QStringLiteral("/state");
  QDir().mkpath(saved);
  QDir().mkpath(installed + QStringLiteral("/data/nymeria/_catalog"));
  QFile manifest(installed + QStringLiteral("/data/nymeria/_catalog/download_urls.json"));
  if (!manifest.open(QIODevice::WriteOnly)) return 2;
  manifest.write("{}"); manifest.close();
  if (selectLibraryRoot(saved, installed, user) != saved) return 3;
  if (selectLibraryRoot(root + QStringLiteral("/missing"), installed, user) != installed) return 4;
  manifest.remove();
  if (selectLibraryRoot({}, installed, user) != user) return 5;
  QDir().mkpath(saved + QStringLiteral("/data/nymeria/_catalog"));
  if (selectLibraryRoot(saved, installed, user) != saved) return 6;
  QFile companion(installed + QStringLiteral("/nymeria_plus_download_urls.json"));
  if (!companion.open(QIODevice::WriteOnly)) return 7;
  companion.write("{}"); companion.close();
  if (selectLibraryRoot({}, installed, user) != installed) return 8;
  if (selectLibraryRoot(saved, installed, user) != saved) return 9;
  QProcessEnvironment inherited;
  inherited.insert(QStringLiteral("NYMERIA_ROOT"), QStringLiteral("C:/another-PC/old-library"));
  inherited.insert(QStringLiteral("nymeria_venv"), QStringLiteral("C:/another-PC/old-python"));
  inherited.insert(QStringLiteral("AWS_SECRET_ACCESS_KEY"), QStringLiteral("fixture-private"));
  inherited.insert(QStringLiteral("PATH"), QStringLiteral("runtime-system-path"));
  inherited.insert(QStringLiteral("LOCALAPPDATA"), user);
  const auto clean = packagedServiceEnvironment(inherited);
  if (clean.contains(QStringLiteral("NYMERIA_ROOT")) || clean.contains(QStringLiteral("nymeria_venv"))
      || clean.contains(QStringLiteral("AWS_SECRET_ACCESS_KEY"))) return 10;
  if (clean.value(QStringLiteral("PATH")) != QStringLiteral("runtime-system-path")
      || clean.value(QStringLiteral("LOCALAPPDATA")) != user) return 11;
  return 0;
}
