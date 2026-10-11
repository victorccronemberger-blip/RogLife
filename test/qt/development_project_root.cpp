#include "../../desktop/src/DevelopmentProjectRoot.hpp"

#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QTemporaryDir>

static bool makeProjectRoot(const QString& root) {
  const QString marker = QDir(root).filePath(QStringLiteral("moneymin/web/__main__.py"));
  if (!QDir().mkpath(QFileInfo(marker).absolutePath())) return false;
  QFile file(marker);
  return file.open(QIODevice::WriteOnly) && file.write("# fixture\n") > 0;
}

int main(int argc, char** argv) {
  QCoreApplication app(argc, argv);
  QTemporaryDir temporary;
  if (!temporary.isValid()) return 1;

  const QString copiedRoot = QDir(temporary.path()).filePath(QStringLiteral("Finally"));
  const QString configuredRoot = QDir(temporary.path()).filePath(QStringLiteral("original"));
  const QString appDir = QDir(copiedRoot).filePath(QStringLiteral("desktop/build/QMoney"));
  if (!makeProjectRoot(copiedRoot) || !makeProjectRoot(configuredRoot)) return 2;
  if (!QDir().mkpath(appDir)) return 3;

  if (resolveDevelopmentProjectRoot(configuredRoot, appDir)
      != QDir::cleanPath(copiedRoot)) return 4;

  const QString unrelatedAppDir = QDir(temporary.path()).filePath(QStringLiteral("other/build"));
  if (!QDir().mkpath(unrelatedAppDir)) return 5;
  if (resolveDevelopmentProjectRoot(configuredRoot, unrelatedAppDir)
      != QDir::cleanPath(configuredRoot)) return 6;
  return 0;
}
