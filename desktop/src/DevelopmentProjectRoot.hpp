#pragma once

#include <QDir>
#include <QFileInfo>
#include <QString>

// Resolve the checked-out project beside the executable before using the
// configure-time fallback. This keeps copied source trees independent.
inline QString resolveDevelopmentProjectRoot(const QString& configuredRoot,
                                            const QString& appDir) {
  auto isProjectRoot = [](const QString& candidate) {
    return QFileInfo::exists(QDir(candidate).filePath(
        QStringLiteral("moneymin/web/__main__.py")));
  };
  QDir candidate(appDir);
  for (int depth = 0; depth < 7; ++depth) {
    const QString path = candidate.absolutePath();
    if (isProjectRoot(path)) return QDir::cleanPath(path);
    if (!candidate.cdUp()) break;
  }
  return QDir::cleanPath(configuredRoot);
}
