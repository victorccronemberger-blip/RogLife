#pragma once

#include <QDir>
#include <QFileInfo>
#include <QString>
#include <QStringList>

inline QString selectLibraryRoot(const QString& savedRoot, const QString& appDir,
                                 const QString& userRoot) {
  if (!savedRoot.isEmpty() && QFileInfo(savedRoot).isDir())
    return QDir::cleanPath(QFileInfo(savedRoot).absoluteFilePath());
  const QStringList candidates{appDir, QDir(appDir).absoluteFilePath(QStringLiteral("../.."))};
  for (const auto& candidate : candidates) {
    const QDir root(candidate);
    if (QFileInfo::exists(root.filePath(QStringLiteral("data/ego4d/timed_narrations.jsonl"))) ||
        QFileInfo::exists(root.filePath(QStringLiteral("data/ego4d/clip_narrations.json"))) ||
        QFileInfo::exists(root.filePath(QStringLiteral("data/holoassist"))) ||
        QFileInfo::exists(root.filePath(QStringLiteral("data/nymeria/_catalog/download_urls.json"))) ||
        QFileInfo(root.filePath(QStringLiteral("nymeria_plus_download_urls.json"))).isFile())
      return QDir::cleanPath(QFileInfo(candidate).absoluteFilePath());
  }
  return QDir::cleanPath(userRoot);
}
