#include "AndroidDevicePanel.hpp"
#include "ApiClient.hpp"
#include "ComboBox.hpp"
#include "UsbDeviceWatcher.hpp"

#include <QComboBox>
#include <QFormLayout>
#include <QHideEvent>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QLabel>
#include <QLocale>
#include <QPainter>
#include <QProgressBar>
#include <QPushButton>
#include <QShowEvent>
#include <QSignalBlocker>
#include <QTimer>
#include <QUrl>
#include <QVBoxLayout>

namespace {
bool validAppCheckObservation(const QJsonObject& status) {
  return status.value(QStringLiteral("device")).isObject()
      && status.value(QStringLiteral("configured")).isBool()
      && status.value(QStringLiteral("token_available")).isBool();
}

class PhoneIllustration final : public QWidget {
public:
  explicit PhoneIllustration(QWidget* parent) : QWidget(parent) {
    setObjectName(QStringLiteral("androidPhoneIllustration"));
    setMinimumSize(82, 150);
    setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Preferred);
  }
  QSize sizeHint() const override { return {94, 172}; }

protected:
  void paintEvent(QPaintEvent*) override {
    QPainter painter(this);
    painter.setRenderHint(QPainter::Antialiasing);
    const qreal scale = qMin(width() / 120.0, height() / 220.0);
    painter.translate((width() - 120 * scale) / 2, (height() - 220 * scale) / 2);
    painter.scale(scale, scale);
    painter.setPen(Qt::NoPen);
    painter.setBrush(QColor(QStringLiteral("#111827")));
    painter.drawRoundedRect(QRectF(7, 2, 106, 216), 18, 18);
    painter.setPen(QPen(QColor(QStringLiteral("#64748b")), 1.5));
    painter.setBrush(Qt::NoBrush);
    painter.drawRoundedRect(QRectF(7, 2, 106, 216), 18, 18);
    painter.setPen(Qt::NoPen);
    const bool on = property("screenOn").toBool();
    QLinearGradient screen(16, 18, 104, 202);
    screen.setColorAt(0, QColor(on ? QStringLiteral("#0369a1") : QStringLiteral("#0f172a")));
    screen.setColorAt(1, QColor(on ? QStringLiteral("#0891b2") : QStringLiteral("#111827")));
    painter.setBrush(screen);
    painter.drawRoundedRect(QRectF(14, 11, 92, 198), 12, 12);
    painter.setBrush(QColor(QStringLiteral("#111827")));
    painter.drawRoundedRect(QRectF(43, 15, 34, 7), 3, 3);
    painter.setPen(QColor(on ? QStringLiteral("#ffffff") : QStringLiteral("#94a3b8")));
    QFont statusFont = font();
    statusFont.setPixelSize(22);
    statusFont.setBold(true);
    painter.setFont(statusFont);
    painter.drawText(QRectF(18, 82, 84, 36), Qt::AlignCenter, on ? QStringLiteral("ON") : QStringLiteral("OFF"));
    statusFont.setPixelSize(10);
    statusFont.setBold(false);
    painter.setFont(statusFont);
    painter.drawText(QRectF(19, 118, 82, 34), Qt::AlignCenter | Qt::TextWordWrap,
                     on ? QStringLiteral("USB conectado") : QStringLiteral("Sem leitura atual"));
    painter.setPen(QPen(QColor(on ? QStringLiteral("#e0f2fe") : QStringLiteral("#475569")), 3, Qt::SolidLine, Qt::RoundCap));
    painter.drawLine(QPointF(46, 198), QPointF(74, 198));
  }
};

QLabel* wrappedLabel(const QString& name, const QString& accessible, QWidget* parent) {
  auto* label = new QLabel(parent);
  label->setObjectName(name);
  label->setAccessibleName(accessible);
  label->setTextFormat(Qt::PlainText);
  label->setWordWrap(true);
  label->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Preferred);
  label->setMinimumWidth(0);
  return label;
}

QString publicValue(const QJsonValue& value) {
  if (value.isString()) return value.toString().trimmed();
  if (value.isDouble()) return QString::number(value.toInt());
  return {};
}

QString deviceStateLabel(const QString& state) {
  if (state == QStringLiteral("device")) return QStringLiteral("disponível");
  if (state == QStringLiteral("unauthorized")) return QStringLiteral("autorização pendente");
  if (state == QStringLiteral("offline")) return QStringLiteral("offline");
  return QStringLiteral("estado desconhecido");
}
} // namespace

AndroidDevicePanel::AndroidDevicePanel(ApiClient* api, QWidget* parent)
    : QWidget(parent), _api(api) {
  setObjectName(QStringLiteral("androidDevicePanel"));
  setAccessibleName(QStringLiteral("Diagnóstico do celular por USB"));
  auto* layout = new QVBoxLayout(this);
  layout->setContentsMargins(0, 0, 0, 0);
  layout->setSpacing(10);

  auto* header = new QHBoxLayout;
  _connectionBadge = wrappedLabel(QStringLiteral("androidConnectionBadge"),
                                  QStringLiteral("Estado da conexão USB"), this);
  _connectionBadge->setMargin(7);
  header->addWidget(_connectionBadge, 1);
  _appCheckStatus = wrappedLabel(QStringLiteral("androidAppCheckStatus"),
                                 QStringLiteral("Estado do App Check"), this);
  _appCheckStatus->setMargin(7);
  _appCheckStatus->setText(QStringLiteral("App Check: verificando..."));
  header->addWidget(_appCheckStatus, 1);
  _refreshButton = new QPushButton(QStringLiteral("Atualizar"), this);
  _refreshButton->setObjectName(QStringLiteral("androidRefreshButton"));
  _refreshButton->setAccessibleName(
      QStringLiteral("Atualizar diagnóstico USB e renovar token App Check"));
  header->addWidget(_refreshButton, 0, Qt::AlignTop);
  layout->addLayout(header);
  _appCheckActionStatus = wrappedLabel(QStringLiteral("androidAppCheckActionStatus"),
      QStringLiteral("Resultado da última rechecagem do App Check"), this);
  _appCheckActionStatus->hide();
  layout->addWidget(_appCheckActionStatus);

  // Painel detalhado do App Check
  _appCheckDeviceId = wrappedLabel(QStringLiteral("androidAppCheckDeviceId"),
                                   QStringLiteral("Device ID do App Check"), this);
  _appCheckProvider = wrappedLabel(QStringLiteral("androidAppCheckProvider"),
                                   QStringLiteral("Provider do App Check"), this);
  _appCheckValidity = wrappedLabel(QStringLiteral("androidAppCheckValidity"),
                                   QStringLiteral("Validade do token App Check"), this);
  _appCheckTokenPrefix = wrappedLabel(QStringLiteral("androidAppCheckTokenPrefix"),
                                      QStringLiteral("Disponibilidade do token JWT em cache"), this);
  _appCheckDeviceId->setText(QStringLiteral("Não disponível"));
  _appCheckProvider->setText(QStringLiteral("Não disponível"));
  _appCheckValidity->setText(QStringLiteral("Não disponível"));
  _appCheckTokenPrefix->setText(QStringLiteral("Não disponível"));
  _usbEventStatus = wrappedLabel(QStringLiteral("androidUsbEventStatus"),
                                QStringLiteral("Disponibilidade do monitor USB"), this);
  _usbEventStatus->setText(QStringLiteral("Monitor USB indisponível. Use Atualizar."));
  auto eventStatusPolicy = _usbEventStatus->sizePolicy();
  eventStatusPolicy.setRetainSizeWhenHidden(true);
  _usbEventStatus->setSizePolicy(eventStatusPolicy);
  _usbEventStatus->hide();
  layout->addWidget(_usbEventStatus);

  auto* body = new QHBoxLayout;
  body->setSpacing(18);
  _phoneIllustration = new PhoneIllustration(this);
  body->addWidget(_phoneIllustration, 0, Qt::AlignTop);
  auto* form = new QFormLayout;
  form->setFieldGrowthPolicy(QFormLayout::AllNonFixedFieldsGrow);
  form->setRowWrapPolicy(QFormLayout::WrapLongRows);
  _selectionLabel = new QLabel(QStringLiteral("Dispositivo"), this);
  _deviceSelector = new ComboBox(this);
  _deviceSelector->setObjectName(QStringLiteral("androidDeviceSelector"));
  _deviceSelector->setAccessibleName(QStringLiteral("Escolher dispositivo USB"));
  _deviceSelector->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Fixed);
  _deviceSelector->setMinimumWidth(0);
  _deviceSelector->setMaxVisibleItems(8);
  _selectionLabel->setBuddy(_deviceSelector);
  form->addRow(_selectionLabel, _deviceSelector);
  _model = wrappedLabel(QStringLiteral("androidModel"), QStringLiteral("Modelo do celular"), this);
  _serial = wrappedLabel(QStringLiteral("androidSerial"), QStringLiteral("Serial do dispositivo"), this);
  _androidVersion = wrappedLabel(QStringLiteral("androidAndroidVersion"),
                                 QStringLiteral("Versão do Android"), this);
  _rootStatus = wrappedLabel(QStringLiteral("androidRootStatus"),
                             QStringLiteral("Disponibilidade de root"), this);
  form->addRow(QStringLiteral("Modelo"), _model);
  form->addRow(QStringLiteral("Serial"), _serial);
  form->addRow(QStringLiteral("Android"), _androidVersion);
  form->addRow(QStringLiteral("Root"), _rootStatus);
  form->addRow(QStringLiteral("Device ID"), _appCheckDeviceId);
  form->addRow(QStringLiteral("Provedor App Check"), _appCheckProvider);
  form->addRow(QStringLiteral("Validade (ADB ao vivo)"), _appCheckValidity);
  form->addRow(QStringLiteral("Token"), _appCheckTokenPrefix);
  auto* details = new QVBoxLayout;
  details->setSpacing(6);
  details->addLayout(form);
  body->addLayout(details, 1);
  layout->addLayout(body);

  _collectionStatus = wrappedLabel(QStringLiteral("androidCollectionStatus"),
                                   QStringLiteral("Estado da leitura de informações públicas"), this);
  _collectionStatus->setAccessibleDescription(
      QStringLiteral("Informa a leitura de dados públicos, sem acessar aplicativos do celular."));
  _collectionStatus->setMinimumHeight(2 * _collectionStatus->fontMetrics().height() + 8);
  details->addWidget(_collectionStatus);
  _readProgress = new QProgressBar(this);
  _readProgress->setObjectName(QStringLiteral("androidReadProgress"));
  _readProgress->setAccessibleName(QStringLiteral("Leitura de informações públicas em andamento"));
  _readProgress->setAccessibleDescription(QStringLiteral("Leitura em andamento, sem percentual estimado."));
  _readProgress->setRange(0, 0);
  _readProgress->setTextVisible(false);
  auto progressPolicy = _readProgress->sizePolicy();
  progressPolicy.setRetainSizeWhenHidden(true);
  _readProgress->setSizePolicy(progressPolicy);
  _readProgress->hide();
  layout->addWidget(_readProgress);
  _lastObservation = wrappedLabel(QStringLiteral("androidLastObservation"),
                                  QStringLiteral("Horário da última leitura concluída"), this);
  details->addWidget(_lastObservation);
  details->addStretch();
  auto* description = wrappedLabel(QStringLiteral("androidDiagnosticDescription"),
                                    QStringLiteral("Descrição do diagnóstico USB"), this);
  description->setText(QStringLiteral("Dados públicos via USB"));
  const QString diagnosticDescription = QStringLiteral(
      "Consulta somente a conexão USB, o modelo, a versão do Android e a disponibilidade de root.");
  description->setToolTip(diagnosticDescription);
  description->setAccessibleDescription(diagnosticDescription);
  description->hide();
  setToolTip(diagnosticDescription);

  _refreshTimer = new QTimer(this);
  _refreshTimer->setObjectName(QStringLiteral("androidRefreshTimer"));
  _refreshTimer->setSingleShot(true);
  _refreshTimer->setInterval(200);
  connect(_refreshTimer, &QTimer::timeout, this, [this] {
    if (!_serviceAvailable || !_api || !isVisible()) { _refreshQueued = false; return; }
    if (_requestPending) { _refreshQueued = true; return; }
    _refreshQueued = false;
    requestRefresh();
  });

  _appCheckTimer = new QTimer(this);
  _appCheckTimer->setObjectName(QStringLiteral("androidAppCheckTimer"));
  _appCheckTimer->setInterval(15000);
  connect(_appCheckTimer, &QTimer::timeout, this, &AndroidDevicePanel::requestAppCheckStatus);
  _usbWatcher = new UsbDeviceWatcher(this);
  _usbWatcher->setObjectName(QStringLiteral("androidUsbWatcher"));
  connect(_usbWatcher, &UsbDeviceWatcher::topologyChanged, this, [this] {
    if (!_serviceAvailable || !_api || !isVisible()) return;
    ++_generation;
    _refreshQueued = true;
    _refreshTimer->start();
  });
  connect(_refreshButton, &QPushButton::clicked, this, [this] {
    // App Check ADB first; USB diagnostics run when recheck finishes so both
    // do not fight over adb while Minute may be restarting.
    requestAppCheckRefresh();
  });
  connect(_deviceSelector, qOverload<int>(&QComboBox::currentIndexChanged),
          this, [this](int index) { selectDevice(index); });
  if (api) connect(api, &QObject::destroyed, this, [this] { setServiceAvailable(false); });

  _selectionLabel->hide();
  _deviceSelector->hide();
  setConnectionBadge(QStringLiteral("unknown"));
  clearDeviceDetails();
  _collectionStatus->setText(QStringLiteral("Aguardando o serviço local para verificar o celular."));
  updateLastObservation();
  updateRequestUi();
}

void AndroidDevicePanel::renderAppCheckStatus(const QJsonObject& status) {
  _appCheckSnapshot = status;
  const bool configured = status.value(QStringLiteral("configured")).toBool();
  const bool tokenAvailable = status.value(QStringLiteral("token_available")).toBool();
  const QString source = status.value(QStringLiteral("source")).toString();
  const int retryAfter = status.value(QStringLiteral("retry_after_seconds")).toInt();
  const QJsonObject device = status.value(QStringLiteral("device")).toObject();
  const bool hasExpiry = device.value(QStringLiteral("left_s")).isDouble();
  const int leftSeconds = device.value(QStringLiteral("left_s")).toInt();
  const bool deviceEnabled = device.value(QStringLiteral("enabled")).toBool();

  // Informações detalhadas do device
  const QString deviceId = device.value(QStringLiteral("device_id")).toString();
  const QString provider = device.value(QStringLiteral("provider")).toString();
  const QString model = device.value(QStringLiteral("model")).toString();
  const QString appVersion = device.value(QStringLiteral("app_version")).toString();
  const QString serial = device.value(QStringLiteral("serial")).toString();
  const int tokenLen = device.value(QStringLiteral("token_len")).toInt(0);

  // The status endpoint describes a global cache, not the selected USB device.
  // Only show its metadata when both identities are explicitly associated.
  QString associationMessage;
  QString associationState;
  if (!device.isEmpty() || source == QStringLiteral("device")) {
    if (_selectedSerial.isEmpty()) {
      associationMessage = QStringLiteral("App Check: selecione um dispositivo");
      associationState = QStringLiteral("selection_required");
    } else if (serial.isEmpty()) {
      associationMessage = QStringLiteral("App Check: cache sem identificação do aparelho");
      associationState = QStringLiteral("unassociated");
    } else if (_selectedSerial != serial) {
      associationMessage = QStringLiteral("App Check: cache de outro aparelho (%1)").arg(serial);
      associationState = QStringLiteral("other_device");
    } else if (_connectionState != QStringLiteral("connected")
               || _connectionBadge->property("observationStale").toBool()) {
      associationMessage = QStringLiteral("App Check: aguardando leitura USB atual do aparelho");
      associationState = QStringLiteral("usb_unconfirmed");
    }
  }
  // Keep matching cache metadata while a new USB read is pending or the last
  // read failed. A confirmed disconnection still clears these fields.
  const bool keepTokenForStaleUsb = associationState == QStringLiteral("usb_unconfirmed")
      && _connectionState == QStringLiteral("connected")
      && _connectionBadge->property("observationStale").toBool()
      && !serial.isEmpty() && serial == _selectedSerial
      && (tokenLen > 0 || hasExpiry || !deviceId.isEmpty() || tokenAvailable);
  if (!associationState.isEmpty() && !keepTokenForStaleUsb) {
    _appCheckStatus->setText(associationMessage);
    _appCheckStatus->setProperty("appCheckState", associationState);
    _appCheckStatus->setToolTip(QString{});
    for (auto* field : {_appCheckDeviceId, _appCheckProvider, _appCheckValidity, _appCheckTokenPrefix}) {
      field->setText(_selectedSerial.isEmpty() ? QStringLiteral("Selecione um dispositivo")
                                             : QStringLiteral("Sem dados confirmados deste aparelho"));
      field->setToolTip(QString{});
    }
    updateAppCheckObservationState();
    return;
  }

  QString text;
  QString state;
  if (!deviceEnabled && (tokenLen > 0 || !deviceId.isEmpty()) && !tokenAvailable) {
    text = QStringLiteral("App Check: dados coletados · modo celular desativado");
    state = QStringLiteral("cached");
  } else if (!configured) {
    text = QStringLiteral("App Check: não configurado");
    state = QStringLiteral("unconfigured");
  } else if (tokenAvailable) {
    if (source == QStringLiteral("device")) {
      const int minutes = leftSeconds / 60;
      const int seconds = leftSeconds % 60;
      text = QStringLiteral("App Check: ✓ device (%1m %2s restantes)").arg(minutes).arg(seconds);
      state = QStringLiteral("device");
    } else if (source == QStringLiteral("bearer")) {
      text = QStringLiteral("App Check: ✓ bearer configurado");
      state = QStringLiteral("bearer");
    } else if (source == QStringLiteral("debug_exchange")) {
      text = QStringLiteral("App Check: ✓ debug token");
      state = QStringLiteral("debug");
    } else {
      text = QStringLiteral("App Check: ✓ disponível");
      state = QStringLiteral("available");
    }
  } else if (status.value(QStringLiteral("paused")).toBool()) {
    text = QStringLiteral("App Check: emissão pausada");
    state = QStringLiteral("paused");
  } else if (retryAfter > 0) {
    text = QStringLiteral("App Check: aguarde %1s").arg(retryAfter);
    state = QStringLiteral("cooldown");
  } else if (deviceEnabled && hasExpiry && leftSeconds <= 0) {
    text = QStringLiteral("App Check: token expirado (renove)");
    state = QStringLiteral("expired");
  } else if (deviceEnabled && hasExpiry && leftSeconds > 0) {
    const int minutes = leftSeconds / 60;
    const int seconds = leftSeconds % 60;
    text = QStringLiteral("App Check: token abaixo do piso (%1m %2s)").arg(minutes).arg(seconds);
    state = QStringLiteral("renew_needed");
  } else {
    text = QStringLiteral("App Check: indisponível");
    state = QStringLiteral("unavailable");
  }

  if (keepTokenForStaleUsb)
    text += _requestPending ? QStringLiteral(" · confirmando USB")
                            : QStringLiteral(" · última leitura USB preservada");
  else if (!serial.isEmpty() && serial == _selectedSerial)
    text += QStringLiteral(" · cache do aparelho selecionado");
  _appCheckStatus->setText(text);
  _appCheckStatus->setProperty("appCheckState",
      keepTokenForStaleUsb ? associationState : state);
  _appCheckStatus->setToolTip(status.value(QStringLiteral("diagnostic")).toObject()
      .value(QStringLiteral("message")).toString());

  // Atualizar campos detalhados - mostrar dados do device se configurado
  if (deviceEnabled || !deviceId.isEmpty() || !provider.isEmpty() || tokenLen > 0) {
    // Device ID real
    _appCheckDeviceId->setText(deviceId.isEmpty() ? QStringLiteral("Não disponível") : deviceId);
    _appCheckDeviceId->setToolTip(deviceId);

    // Provider
    _appCheckProvider->setText(provider.isEmpty() ? QStringLiteral("Não disponível") : provider);

    // Validade
    if (leftSeconds > 0) {
      const int minutes = leftSeconds / 60;
      const int seconds = leftSeconds % 60;
      _appCheckValidity->setText(QStringLiteral("%1m %2s").arg(minutes).arg(seconds));
    } else if (hasExpiry && leftSeconds <= 0) {
      _appCheckValidity->setText(QStringLiteral("Expirado (renove)"));
    } else {
      _appCheckValidity->setText(QStringLiteral("Não disponível"));
    }

    // Token prefix
    if (tokenLen > 0) {
      _appCheckTokenPrefix->setText(QStringLiteral("JWT em cache (%1 caracteres) · %2").arg(tokenLen)
          .arg(deviceEnabled && device.value(QStringLiteral("token_available")).toBool()
              ? QStringLiteral("utilizável") : QStringLiteral("indisponível para uso")));
    } else {
      _appCheckTokenPrefix->setText(QStringLiteral("Não disponível"));
    }

    // Adicionar informações extras no tooltip
    QString tooltip = QStringLiteral("Device ID: %1\nProvider: %2\nModel: %3\nApp Version: %4\nSerial: %5")
        .arg(deviceId, provider, model, appVersion, serial);
    _appCheckDeviceId->setToolTip(tooltip);
  } else {
    _appCheckDeviceId->setText(QStringLiteral("Não disponível"));
    _appCheckProvider->setText(QStringLiteral("Não disponível"));
    _appCheckValidity->setText(QStringLiteral("Não disponível"));
    _appCheckTokenPrefix->setText(QStringLiteral("Não disponível"));
    _appCheckDeviceId->setToolTip(QString());
  }
  updateAppCheckObservationState();
}

void AndroidDevicePanel::updateAppCheckObservationState() {
  _appCheckStatus->setProperty("statusReadFailed", _appCheckStatusReadFailed);
  for (auto* field : {_appCheckDeviceId, _appCheckProvider, _appCheckValidity, _appCheckTokenPrefix})
    field->setProperty("observationStale", _appCheckStatusReadFailed);
  if (!_appCheckStatusReadFailed) return;

  const QString warning = QStringLiteral(
      "Não foi possível confirmar o estado atual. Os dados exibidos pertencem à última consulta recebida.");
  _appCheckStatus->setToolTip(warning);
  const QString association = _appCheckStatus->property("appCheckState").toString();
  // Keep selection/disconnection warnings and never restore another phone's data.
  if (association == QStringLiteral("selection_required")
      || association == QStringLiteral("unassociated")
      || association == QStringLiteral("other_device")
      || (association == QStringLiteral("usb_unconfirmed")
          && _connectionState != QStringLiteral("connected"))) return;
  const QJsonObject device = _appCheckSnapshot.value(QStringLiteral("device")).toObject();
  const int tokenLength = device.value(QStringLiteral("token_len")).toInt();
  if (tokenLength > 0)
    _appCheckTokenPrefix->setText(QStringLiteral(
        "JWT na última consulta (%1 caracteres) · uso atual não confirmado").arg(tokenLength));
  if (device.value(QStringLiteral("left_s")).isDouble())
    _appCheckValidity->setText(_appCheckValidity->text() + QStringLiteral(" (na última consulta)"));
  _appCheckStatus->setText(_appCheckSnapshot.isEmpty()
      ? QStringLiteral("App Check: não foi possível atualizar")
      : QStringLiteral("App Check: consulta indisponível · última consulta preservada (desatualizada)"));
  _appCheckStatus->setProperty("appCheckState", QStringLiteral("error"));
}

void AndroidDevicePanel::setServiceAvailable(bool available) {
  available = available && !_api.isNull();
  if (_serviceAvailable == available) return;
  _serviceAvailable = available;
  ++_generation;
  _refreshQueued = false;
  if (!available) {
    _appCheckStatusReadFailed = false;
    ++_appCheckRequest;
    _appCheckActionStatus->clear();
    _appCheckActionStatus->hide();
    _appCheckTimer->stop();
    renderAppCheckStatus({});
    _appCheckStatus->setText(QStringLiteral("App Check: serviço indisponível"));
    _appCheckStatus->setProperty("appCheckState", QStringLiteral("unavailable"));
    _refreshTimer->stop();
    _usbWatcher->stop();
    setProperty("usbEventsActive", false);
    _usbEventStatus->hide();
    setConnectionBadge(_connectionState, true);
    setPhoneScreen(_connectionState == QStringLiteral("connected"), true);
    _collectionStatus->setText(QStringLiteral("Serviço local indisponível. A conexão do celular "
                                            "não foi reavaliada."));
    _collectionStatus->setProperty("collectionState", QStringLiteral("unavailable"));
    updateLastObservation();
  } else if (isVisible()) {
    startUsbWatcher();
    if (_requestPending) {
      _refreshQueued = true;
      _collectionStatus->setText(QStringLiteral("Aguardando a consulta anterior terminar para atualizar."));
    } else {
      requestRefresh();
    }
    _appCheckTimer->start();
    requestAppCheckStatus();
  } else {
    _collectionStatus->setText(QStringLiteral("Abra esta tela para verificar o celular."));
  }
  updateRequestUi();
}

void AndroidDevicePanel::showEvent(QShowEvent* event) {
  QWidget::showEvent(event);
  if (_serviceAvailable && _api) {
    startUsbWatcher();
    if (_requestPending) {
      _refreshQueued = true;
      _collectionStatus->setText(QStringLiteral("Aguardando a consulta anterior terminar para atualizar."));
    } else {
      requestRefresh();
    }
    _appCheckTimer->start();
    requestAppCheckStatus();
  }
  updateRequestUi();
}

void AndroidDevicePanel::hideEvent(QHideEvent* event) {
  ++_appCheckRequest;
  _appCheckActionStatus->clear();
  _appCheckActionStatus->hide();
  _appCheckTimer->stop();
  _refreshTimer->stop();
  _usbWatcher->stop();
  setProperty("usbEventsActive", false);
  ++_generation;
  _refreshQueued = false;
  // Keep the pending flag until the real HTTP callback: invalidating the UI
  // cannot cancel ApiClient's request and must not allow an overlapping read.
  QWidget::hideEvent(event);
  updateRequestUi();
}

void AndroidDevicePanel::startUsbWatcher() {
  const bool active = _usbWatcher->start(window());
  setProperty("usbEventsActive", active);
  _usbEventStatus->setVisible(!active);
  const QString description = active
      ? QStringLiteral("A conexão USB é atualizada quando o Windows informa uma mudança de dispositivos. Não há consultas periódicas.")
      : QStringLiteral("Não foi possível registrar as notificações USB nesta janela. Use Atualizar para consultar o dispositivo; não há consultas periódicas.");
  _usbEventStatus->setToolTip(description);
  _usbEventStatus->setAccessibleDescription(description);
  _refreshButton->setToolTip(description);
  _refreshButton->setAccessibleDescription(description);
}

void AndroidDevicePanel::updateRequestUi() {
  const bool pending = _requestPending || _appCheckPending;
  _refreshButton->setEnabled(_serviceAvailable && _api && !pending);
  _refreshButton->setText(pending && _serviceAvailable
      ? QStringLiteral("Atualizando…") : QStringLiteral("Atualizar"));
  const bool selectedMissing = !_selectedSerial.isEmpty() && !_returnedSerials.contains(_selectedSerial);
  _deviceSelector->setEnabled(_serviceAvailable && _api
      && (_returnedSerials.size() > 1 || (selectedMissing && !_returnedSerials.isEmpty())));
  _readProgress->setVisible(_requestPending && _serviceAvailable && isVisible());
}

void AndroidDevicePanel::requestRefresh() {
  if (!_serviceAvailable || !_api || !isVisible() || _requestPending) return;
  _refreshTimer->stop();
  _refreshQueued = false;
  const quint64 generation = _generation;
  const quint64 request = ++_activeRequest;
  _requestPending = true;
  setConnectionBadge(_connectionState, true);
  renderAppCheckStatus(_appCheckSnapshot);
  setPhoneScreen(_connectionState == QStringLiteral("connected"), true);
  _collectionStatus->setText(QStringLiteral("Lendo informações públicas do dispositivo…"));
  _collectionStatus->setProperty("collectionState", QStringLiteral("pending"));
  updateLastObservation();
  updateRequestUi();
  QString path = QStringLiteral("/api/integrations/android/device");
  if (!_selectedSerial.isEmpty())
    path += QStringLiteral("?serial=") + QString::fromLatin1(QUrl::toPercentEncoding(_selectedSerial));
  const QPointer<AndroidDevicePanel> panel(this);
  _api->get(path, [panel, generation, request](bool ok, const QJsonDocument& document,
                                               const QString& error) {
    if (!panel || request != panel->_activeRequest) return;
    panel->_requestPending = false;
    const bool current = generation == panel->_generation
        && panel->_serviceAvailable && panel->isVisible();
    if (current) {
      if (ok && document.isObject()) panel->renderSnapshot(document.object());
      else panel->showRequestError(error);
    }
    panel->updateRequestUi();
    const bool refresh = panel->_refreshQueued && panel->_serviceAvailable && panel->isVisible();
    if (refresh && !panel->_refreshTimer->isActive()) {
      panel->_refreshQueued = false;
      QTimer::singleShot(0, panel.data(), [panel] {
        if (panel && !panel->_refreshTimer->isActive()) panel->requestRefresh();
      });
    } else if (!refresh) {
      panel->_refreshQueued = false;
    }
  });
}

void AndroidDevicePanel::requestAppCheckStatus() {
  if (!_serviceAvailable || !_api || !isVisible() || _appCheckPending) return;
  _appCheckPending = true;
  updateRequestUi();
  const quint64 request = ++_appCheckRequest;
  const QPointer<AndroidDevicePanel> panel(this);
  _api->get(QStringLiteral("/api/integrations/appcheck/status"),
            [panel, request](bool ok, const QJsonDocument& document, const QString&) {
    if (!panel) return;
    panel->_appCheckPending = false;
    panel->updateRequestUi();
    if (request != panel->_appCheckRequest) {
      panel->requestAppCheckStatus();
      return;
    }
    if (!panel->_serviceAvailable || !panel->isVisible()) return;
    const QJsonObject status = document.object();
    if (ok && document.isObject() && validAppCheckObservation(status)) {
      panel->_appCheckStatusReadFailed = false;
      panel->renderAppCheckStatus(document.object());
    } else {
      panel->_appCheckStatusReadFailed = true;
      panel->renderAppCheckStatus(panel->_appCheckSnapshot);
    }
  });
}

void AndroidDevicePanel::requestAppCheckRefresh() {
  if (!_serviceAvailable || !_api || !isVisible() || _appCheckPending) return;
  _appCheckPending = true;
  updateRequestUi();
  const quint64 request = ++_appCheckRequest;
  const QPointer<AndroidDevicePanel> panel(this);
  _appCheckActionStatus->setText(
      QStringLiteral("Rechecagem App Check via ADB… (reinício do Minute só se o token estiver inválido)"));
  _appCheckActionStatus->setProperty("actionState", QStringLiteral("pending"));
  _appCheckActionStatus->show();
  _api->post(QStringLiteral("/api/integrations/appcheck/recheck"), QJsonObject(),
            [panel, request](bool ok, const QJsonDocument& document, const QString& error) {
    if (!panel) return;
    panel->_appCheckPending = false;
    panel->updateRequestUi();
    if (request != panel->_appCheckRequest) {
      panel->requestAppCheckStatus();
      return;
    }
    if (!panel->_serviceAvailable || !panel->isVisible()) return;
    const QJsonObject result = document.object();
    const QJsonObject status = result.value(QStringLiteral("appcheck")).toObject();
    const bool validStatus = ok && result.value(QStringLiteral("appcheck")).isObject()
        && validAppCheckObservation(status);
    const bool confirmed = validStatus && result.value(QStringLiteral("ok")) == QJsonValue(true)
        && status.value(QStringLiteral("token_available")) == QJsonValue(true);
    if (validStatus) {
      panel->_appCheckStatusReadFailed = false;
      panel->renderAppCheckStatus(status);
    } else if (ok && result.value(QStringLiteral("appcheck")).isObject()) {
      panel->_appCheckStatusReadFailed = true;
      panel->renderAppCheckStatus(panel->_appCheckSnapshot);
    }
    QString message = result.value(QStringLiteral("message")).toString().trimmed();
    if (message.isEmpty() && !confirmed) message = error.trimmed();
    if (message.isEmpty()) message = confirmed
        ? QStringLiteral("Token disponível; a aceitação depende do serviço.")
        : QStringLiteral("O serviço não confirmou a disponibilidade do token.");
    // Keep the action result separate from periodically refreshed observations.
    panel->_appCheckActionStatus->setText(QStringLiteral("Última rechecagem: %1").arg(message));
    panel->_appCheckActionStatus->setProperty("actionState",
        confirmed ? QStringLiteral("available") : QStringLiteral("error"));
    // USB diagnostics after App Check so concurrent adb does not stall the mint.
    if (!panel->_requestPending) panel->requestRefresh();
    else panel->_refreshQueued = true;
  });
}

void AndroidDevicePanel::selectDevice(int index) {
  const QString serial = _deviceSelector->itemData(index).toString();
  if (!_returnedSerials.contains(serial) || serial == _selectedSerial) return;
  _refreshTimer->stop();
  _selectedSerial = serial;
  ++_appCheckRequest;
  _appCheckActionStatus->clear();
  _appCheckActionStatus->hide();
  ++_generation;
  _lastSuccessfulObservation = {};
  _lastObservationSerial.clear();
  clearDeviceDetails();
  updateLastObservation();
  setConnectionBadge(QStringLiteral("unknown"));
  renderAppCheckStatus(_appCheckSnapshot);
  _collectionStatus->setText(QStringLiteral("Aguardando a leitura do dispositivo escolhido."));
  if (_requestPending) {
    _refreshQueued = true;
    _collectionStatus->setText(QStringLiteral("Aguardando a consulta anterior terminar para ler "
                                            "o dispositivo escolhido."));
  } else {
    requestRefresh();
  }
  requestAppCheckStatus();
}

void AndroidDevicePanel::clearDeviceDetails() {
  _model->setText(QStringLiteral("Não extraído"));
  _serial->setText(_selectedSerial.isEmpty() ? QStringLiteral("Não extraído") : _selectedSerial);
  _androidVersion->setText(QStringLiteral("Não extraído"));
  _rootStatus->setText(QStringLiteral("Não verificado"));
  _rootStatus->setProperty("rootState", QStringLiteral("unknown"));
  _rootStatus->setToolTip(QString{});
  _rootStatus->setAccessibleDescription(QStringLiteral("O privilégio de root ainda não foi verificado."));
  setPhoneScreen(false);
}

void AndroidDevicePanel::setPhoneScreen(bool on, bool stale) {
  _phoneIllustration->setProperty("screenOn", on);
  _phoneIllustration->setProperty("observationStale", stale);
  _phoneIllustration->setAccessibleName(on
      ? stale ? QStringLiteral("Celular: tela acesa, última conexão USB observada; leitura não atualizada")
              : QStringLiteral("Celular: tela acesa, conexão USB confirmada")
      : stale ? QStringLiteral("Celular: tela apagada, última observação USB não atualizada")
              : QStringLiteral("Celular: tela apagada, conexão USB não confirmada"));
  _phoneIllustration->update();
}

void AndroidDevicePanel::setConnectionBadge(const QString& state, bool stale) {
  _connectionState = state;
  QString text = QStringLiteral("Não verificado");
  QString foreground = QStringLiteral("#374151"), background = QStringLiteral("#e5e7eb");
  if (state == QStringLiteral("connected")) {
    text = QStringLiteral("Conectado"); foreground = QStringLiteral("#14532d"); background = QStringLiteral("#dcfce7");
  } else if (state == QStringLiteral("disconnected")) {
    text = QStringLiteral("Desconectado"); foreground = QStringLiteral("#7f1d1d"); background = QStringLiteral("#fee2e2");
  } else if (state == QStringLiteral("adb_missing")) {
    text = QStringLiteral("ADB não disponível"); foreground = QStringLiteral("#7f1d1d"); background = QStringLiteral("#fee2e2");
  } else if (state == QStringLiteral("unauthorized")) {
    text = QStringLiteral("Autorização pendente"); foreground = QStringLiteral("#78350f"); background = QStringLiteral("#fef3c7");
  } else if (state == QStringLiteral("offline")) {
    text = QStringLiteral("Offline"); foreground = QStringLiteral("#78350f"); background = QStringLiteral("#fef3c7");
  } else if (state == QStringLiteral("multiple")) {
    text = QStringLiteral("Escolha um dispositivo"); foreground = QStringLiteral("#78350f"); background = QStringLiteral("#fef3c7");
  } else if (state == QStringLiteral("error")) {
    text = QStringLiteral("Verificação indisponível"); foreground = QStringLiteral("#7f1d1d"); background = QStringLiteral("#fee2e2");
  }
  if (stale && state != QStringLiteral("unknown")) text.prepend(QStringLiteral("Última observação: "));
  _connectionBadge->setText(text);
  _connectionBadge->setProperty("connectionState", state);
  _connectionBadge->setProperty("observationStale", stale);
  _connectionBadge->setStyleSheet(QStringLiteral("QLabel { color: %1; background-color: %2; border-radius: 5px; }")
                                  .arg(foreground, background));
}

void AndroidDevicePanel::showRequestError(const QString& error) {
  setConnectionBadge(_connectionState, true);
  renderAppCheckStatus(_appCheckSnapshot);
  setPhoneScreen(_connectionState == QStringLiteral("connected"), true);
  _collectionStatus->setText(QStringLiteral("Não foi possível atualizar a leitura. %1 "
                                           "A conexão do celular não foi reavaliada.")
      .arg(error.isEmpty() ? QStringLiteral("O serviço local não respondeu.") : error));
  _collectionStatus->setProperty("collectionState", QStringLiteral("error"));
  updateLastObservation();
}

void AndroidDevicePanel::updateLastObservation() {
  _lastObservation->setText(_lastSuccessfulObservation.isValid()
      ? QStringLiteral("Última leitura: %1 (local)")
          .arg(QLocale(QLocale::Portuguese, QLocale::Brazil)
              .toString(_lastSuccessfulObservation.toLocalTime(), QStringLiteral("dd/MM/yyyy HH:mm:ss")))
      : QStringLiteral("Sem leitura concluída nesta sessão"));
  const bool stale = _connectionBadge->property("observationStale").toBool();
  _lastObservation->setProperty("observationStale", stale);
  _lastObservation->setAccessibleDescription(stale
      ? QStringLiteral("Horário local da última leitura concluída; os dados não foram atualizados.")
      : QStringLiteral("Horário local da última leitura concluída nesta sessão."));
  _lastObservation->setToolTip(_lastObservationSerial.isEmpty() ? QString{}
      : QStringLiteral("Dispositivo: %1").arg(_lastObservationSerial));
  _lastObservation->setProperty("checkedAt", _lastSuccessfulObservation.isValid()
      ? _lastSuccessfulObservation.toUTC().toString(Qt::ISODateWithMs) : QString{});
}

void AndroidDevicePanel::renderSnapshot(const QJsonObject& snapshot) {
  const QString state = snapshot.value(QStringLiteral("connection_state")).toString(QStringLiteral("unknown"));
  const QString previousState = _connectionState;
  const QString previousSerial = _selectedSerial;
  const QJsonObject collection = snapshot.value(QStringLiteral("collection")).toObject();
  const QString collectionState = collection.value(QStringLiteral("state")).toString();
  const QString message = collection.value(QStringLiteral("message")).toString().trimmed();
  const QString responseSerial = snapshot.value(QStringLiteral("selected_serial")).toString();
  const QJsonArray devices = snapshot.value(QStringLiteral("devices")).toArray();
  bool selectedPresent = false;
  for (const auto& entry : devices) {
    const QJsonObject device = entry.toObject();
    selectedPresent |= device.value(QStringLiteral("serial")).toString() == responseSerial
        && device.value(QStringLiteral("state")).toString() == QStringLiteral("device");
  }
  if ((!previousSerial.isEmpty() && !responseSerial.isEmpty() && responseSerial != previousSerial)
      || (state == QStringLiteral("connected") && (responseSerial.isEmpty() || !selectedPresent))) {
    showRequestError(QStringLiteral("A resposta não corresponde ao dispositivo selecionado. Atualize a leitura."));
    updateRequestUi();
    return;
  }
  const bool sameObservation = responseSerial.isEmpty() || responseSerial == previousSerial;
  if ((state == QStringLiteral("error") || state == QStringLiteral("unknown"))
      && ((previousState != QStringLiteral("unknown") && previousState != QStringLiteral("error"))
          || !previousSerial.isEmpty())
      && sameObservation) {
    // An unavailable inventory/read cannot confirm that a previously observed
    // device disconnected. Keep that observation and expose the failed read.
    showRequestError(message);
    updateRequestUi();
    return;
  }
  if (state == QStringLiteral("adb_missing") && previousState == QStringLiteral("connected") && sameObservation) {
    showRequestError(message.isEmpty() ? QStringLiteral("A ferramenta ADB não está disponível no computador.") : message);
    updateRequestUi();
    return;
  }
  setConnectionBadge(state, state == QStringLiteral("connected") && collectionState != QStringLiteral("done"));
  const QString preferredSerial = responseSerial.isEmpty() ? _selectedSerial : responseSerial;
  {
    const QSignalBlocker blocker(_deviceSelector);
    _deviceSelector->clear();
    _returnedSerials.clear();
    if (state == QStringLiteral("multiple"))
      _deviceSelector->addItem(QStringLiteral("Escolha um dispositivo"), QString{});
    for (const auto& entry : devices) {
      const QJsonObject device = entry.toObject();
      const QString serial = device.value(QStringLiteral("serial")).toString();
      if (serial.isEmpty() || _returnedSerials.contains(serial)) continue;
      _returnedSerials.insert(serial);
      const QString model = publicValue(device.value(QStringLiteral("model")));
      _deviceSelector->addItem(QStringLiteral("%1 · %2 · %3")
          .arg(model.isEmpty() ? QStringLiteral("Dispositivo Android") : model, serial,
               deviceStateLabel(device.value(QStringLiteral("state")).toString())), serial);
    }
    if (!preferredSerial.isEmpty() && !_returnedSerials.contains(preferredSerial))
      _deviceSelector->insertItem(0, QStringLiteral("%1 · %2").arg(preferredSerial,
          state == QStringLiteral("disconnected") ? QStringLiteral("desconectado") : QStringLiteral("não disponível")), preferredSerial);
    int selected = _deviceSelector->findData(preferredSerial);
    if (selected < 0) selected = state == QStringLiteral("multiple") && _deviceSelector->count() ? 0 : -1;
    _deviceSelector->setCurrentIndex(selected);
    _selectedSerial = _deviceSelector->currentData().toString();
  }
  const bool selectedMissing = !_selectedSerial.isEmpty() && !_returnedSerials.contains(_selectedSerial);
  renderAppCheckStatus(_appCheckSnapshot);
  const bool showSelection = _returnedSerials.size() > 1 || state == QStringLiteral("multiple")
      || (selectedMissing && !_returnedSerials.isEmpty());
  _selectionLabel->setVisible(showSelection);
  _deviceSelector->setVisible(showSelection);

  if (previousSerial != _selectedSerial) {
    _lastSuccessfulObservation = {};
    _lastObservationSerial.clear();
    clearDeviceDetails();
  }
  if (state == QStringLiteral("connected") && collectionState != QStringLiteral("done")
      && previousState == QStringLiteral("connected") && previousSerial == _selectedSerial) {
    _collectionStatus->setText(QStringLiteral("Leitura pública não concluída. %1 Dados da última leitura preservados.")
        .arg(message));
    _collectionStatus->setProperty("collectionState", collectionState);
    setPhoneScreen(true, true);
    updateLastObservation();
    updateRequestUi();
    return;
  }

  const QJsonObject device = snapshot.value(QStringLiteral("device")).toObject();
  const QString model = publicValue(device.value(QStringLiteral("model")));
  const QString manufacturer = publicValue(device.value(QStringLiteral("manufacturer")));
  _model->setText(model.isEmpty() ? QStringLiteral("Não extraído")
      : manufacturer.isEmpty() ? model : QStringLiteral("%1 %2").arg(manufacturer, model));
  _serial->setText(_selectedSerial.isEmpty() ? QStringLiteral("Não extraído") : _selectedSerial);
  const QString version = publicValue(device.value(QStringLiteral("android_version")));
  const QString sdk = publicValue(device.value(QStringLiteral("sdk")));
  _androidVersion->setText(version.isEmpty() ? QStringLiteral("Não extraído")
      : sdk.isEmpty() ? version : QStringLiteral("%1 (SDK %2)").arg(version, sdk));

  const QJsonObject root = snapshot.value(QStringLiteral("root")).toObject();
  const QString rootState = root.value(QStringLiteral("state")).toString();
  QString rootText = QStringLiteral("Não verificado");
  QString rootDetail = QStringLiteral("O privilégio de root não foi verificado.");
  if (rootState == QStringLiteral("root_shell")) {
    rootText = QStringLiteral("ON · shell root");
    rootDetail = QStringLiteral("Privilégio de administrador confirmado no shell ADB.");
  } else if (rootState == QStringLiteral("su_available")) {
    rootText = QStringLiteral("su disponível · acesso não testado");
  } else if (rootState == QStringLiteral("not_detected")) {
    rootText = QStringLiteral("OFF · não detectado");
    rootDetail = QStringLiteral("Root não detectado nesta leitura.");
  }
  const QString rootMessage = root.value(QStringLiteral("message")).toString().trimmed();
  if (!rootMessage.isEmpty()) rootDetail += QLatin1Char(' ') + rootMessage;
  _rootStatus->setText(rootText);
  _rootStatus->setAccessibleDescription(rootDetail);
  _rootStatus->setToolTip(QStringLiteral("<qt>%1</qt>").arg(rootDetail.toHtmlEscaped()));
  _rootStatus->setProperty("rootState", rootState);

  QString collectionText;
  if (collectionState == QStringLiteral("done")) {
    collectionText = QStringLiteral("Leitura concluída");
    const QDateTime checkedAt = QDateTime::fromString(collection.value(QStringLiteral("checked_at")).toString(), Qt::ISODateWithMs);
    if (checkedAt.isValid()) {
      _lastSuccessfulObservation = checkedAt;
      _lastObservationSerial = _selectedSerial;
    }
    const QJsonValue elapsed = collection.value(QStringLiteral("elapsed_ms"));
    if (elapsed.isDouble() && elapsed.toDouble() >= 0)
      collectionText += QStringLiteral(" · %1 ms").arg(elapsed.toInt());
  } else if (collectionState == QStringLiteral("unavailable")) {
    collectionText = QStringLiteral("Leitura indisponível.");
    if (message.isEmpty()) {
      if (state == QStringLiteral("adb_missing")) collectionText += QStringLiteral(" A ferramenta ADB não está disponível no computador.");
      else if (state == QStringLiteral("unauthorized")) collectionText += QStringLiteral(" Autorize a depuração USB na tela do celular.");
      else if (state == QStringLiteral("multiple")) collectionText += QStringLiteral(" Escolha o dispositivo acima.");
      else if (state == QStringLiteral("offline")) collectionText += QStringLiteral(" O ADB encontrou o dispositivo offline.");
    }
  } else if (collectionState == QStringLiteral("error")) {
    collectionText = QStringLiteral("Não foi possível concluir a leitura.");
  } else {
    collectionText = QStringLiteral("Leitura ainda não concluída.");
  }
  if (!message.isEmpty()) collectionText += QLatin1Char(' ') + message;
  _collectionStatus->setText(collectionText);
  _collectionStatus->setProperty("collectionState", collectionState);
  setPhoneScreen(state == QStringLiteral("connected"), collectionState != QStringLiteral("done")
      && state == QStringLiteral("connected"));
  updateLastObservation();
  updateRequestUi();
}
