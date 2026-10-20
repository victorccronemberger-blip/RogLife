cmake_minimum_required(VERSION 3.21)

if(DEFINED ENV{QMONEY_VERSION} AND NOT "$ENV{QMONEY_VERSION}" STREQUAL "")
  set(QMONEY_VERSION "$ENV{QMONEY_VERSION}")
else()
  set(QMONEY_VERSION "1.0.63")
endif()
project(QMoney VERSION ${QMONEY_VERSION} LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)
set(CMAKE_AUTOUIC ON)

find_package(Qt6 6.4 REQUIRED COMPONENTS Core Gui Network Svg Widgets)

set(QLEMENTINE_SANDBOX OFF CACHE BOOL "" FORCE)
set(QLEMENTINE_SHOWCASE OFF CACHE BOOL "" FORCE)
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/../vendor/qlementine/CMakeLists.txt")
  add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../vendor/qlementine
                   ${CMAKE_CURRENT_BINARY_DIR}/qlementine)
else()
  include(FetchContent)
  FetchContent_Declare(qlementine
    GIT_REPOSITORY https://github.com/oclero/qlementine.git
    GIT_TAG v1.4.2
    GIT_SHALLOW TRUE
  )
  FetchContent_MakeAvailable(qlementine)
endif()

include(cmake/FixQlementineMenus.cmake)

set(QMONEY_EMBEDDED_SERVICE_RESOURCE)
set(QMONEY_HAS_EMBEDDED_SERVICE 0)
if(WIN32 AND DEFINED ENV{QMONEY_EMBEDDED_SERVICE}
   AND EXISTS "$ENV{QMONEY_EMBEDDED_SERVICE}")
  file(TO_CMAKE_PATH "$ENV{QMONEY_EMBEDDED_SERVICE}"
       QMONEY_EMBEDDED_SERVICE_PATH)
  configure_file(resources/embedded_service.rc.in
                 ${CMAKE_CURRENT_BINARY_DIR}/embedded_service.rc @ONLY)
  list(APPEND QMONEY_EMBEDDED_SERVICE_RESOURCE
       ${CMAKE_CURRENT_BINARY_DIR}/embedded_service.rc)
  set(QMONEY_HAS_EMBEDDED_SERVICE 1)
endif()

set(QMONEY_MAIN_WINDOW_SOURCES
  src/MainWindow.cpp
  src/MainWindowSupport.hpp
  src/DevelopmentProjectRoot.hpp
  src/MainWindowComponents.cpp
  src/MainWindowService.cpp
  src/MainWindowDashboard.cpp
  src/MainWindowMonitoring.cpp
  src/MainWindowIntegrations.cpp
  src/MainWindowMobile.cpp
  src/MainWindowCampaigns.cpp
  src/AndroidDevicePanel.cpp
  src/AndroidDevicePanel.hpp
  src/UsbDeviceWatcher.cpp
  src/UsbDeviceWatcher.hpp
  src/MainWindowCatalogs.cpp
  src/MainWindowAccounts.cpp
  src/MainWindowBalances.cpp
  src/MainWindowLibrary.cpp
)

set(QMONEY_WINDOWS_RESOURCES)
if(WIN32)
  configure_file(resources/version.rc.in ${CMAKE_CURRENT_BINARY_DIR}/version.rc @ONLY)
  # Icon/version resources need no Qt headers or C++ definitions. Compile them
  # independently so windres does not receive Qt include paths with spaces.
  add_library(qmoney_windows_resources OBJECT
    resources/qmoney.rc ${CMAKE_CURRENT_BINARY_DIR}/version.rc
    ${QMONEY_EMBEDDED_SERVICE_RESOURCE})
  set_target_properties(qmoney_windows_resources PROPERTIES
    AUTOMOC OFF AUTOUIC OFF AUTORCC OFF LINKER_LANGUAGE CXX)
  set(QMONEY_WINDOWS_RESOURCES $<TARGET_OBJECTS:qmoney_windows_resources>)
endif()

qt_add_executable(QMoney WIN32
  src/main.cpp
  src/ApiClient.hpp
  src/ApiClient.cpp
  src/MainWindow.hpp
  src/LibraryRootSelection.hpp
  src/PackagedServiceEnvironment.hpp
  src/ComboBox.hpp
  ${QMONEY_MAIN_WINDOW_SOURCES}
  src/UpdateManager.hpp
  src/UpdateManager.cpp
  resources/qmoney.qrc
  ${QMONEY_WINDOWS_RESOURCES}
)

add_executable(QMoneyUpdater WIN32
  src/updater_main.cpp
  resources/qmoney.rc
  $<$<BOOL:${WIN32}>:${CMAKE_CURRENT_BINARY_DIR}/version.rc>
)
if(MINGW)
  target_link_options(QMoneyUpdater PRIVATE -static -static-libgcc -static-libstdc++)
endif()
if(MSVC)
  set_property(TARGET QMoneyUpdater PROPERTY
               MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()
if(WIN32)
  target_link_libraries(QMoneyUpdater PRIVATE shell32 bcrypt)
  target_link_libraries(QMoney PRIVATE bcrypt crypt32 user32)
endif()

target_link_libraries(QMoney PRIVATE
  qlementine
  Qt6::Core
  Qt6::Gui
  Qt6::Network
  Qt6::Svg
  Qt6::Widgets
)

target_compile_definitions(QMoney PRIVATE
  QMONEY_LOCAL_ONLY=1
  QMONEY_PROJECT_ROOT="${CMAKE_CURRENT_SOURCE_DIR}/.."
  QMONEY_VERSION="${PROJECT_VERSION}"
  QMONEY_HAS_EMBEDDED_SERVICE=${QMONEY_HAS_EMBEDDED_SERVICE}
)

set_target_properties(QMoney PROPERTIES
  OUTPUT_NAME "QMoney"
  VS_DEBUGGER_WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/.."
)

install(TARGETS QMoney QMoneyUpdater RUNTIME DESTINATION .)

option(QMONEY_BUILD_LOCAL_TESTS "Build tests for the locally restored desktop" ON)
if(QMONEY_BUILD_LOCAL_TESTS)
  enable_testing()
  add_subdirectory(../test/native ${CMAKE_CURRENT_BINARY_DIR}/native_tests)
  foreach(contract api_responses update_download update_policy owned_service_processes)
    if(contract STREQUAL "api_responses")
      set(contract_sources src/ApiClient.cpp src/ApiClient.hpp)
    elseif(contract STREQUAL "owned_service_processes")
      set(contract_sources)
    else()
      set(contract_sources src/UpdateManager.cpp src/UpdateManager.hpp)
    endif()
    add_executable(${contract} ../test/qt/${contract}.cpp ${contract_sources})
    target_link_libraries(${contract} PRIVATE Qt6::Core Qt6::Network)
    if(WIN32)
      target_link_libraries(${contract} PRIVATE bcrypt crypt32)
    endif()
    add_test(NAME ${contract} COMMAND ${contract})
    set_tests_properties(${contract} PROPERTIES TIMEOUT 30)
  endforeach()
  add_executable(library_root_selection ../test/qt/library_root_selection.cpp)
  target_link_libraries(library_root_selection PRIVATE Qt6::Core)
  add_test(NAME library_root_selection COMMAND library_root_selection)
  set_tests_properties(library_root_selection PROPERTIES TIMEOUT 10)
  add_executable(development_project_root ../test/qt/development_project_root.cpp)
  target_link_libraries(development_project_root PRIVATE Qt6::Core)
  add_test(NAME development_project_root COMMAND development_project_root)
  set_tests_properties(development_project_root PROPERTIES TIMEOUT 10)
  # api_responses deliberately exercises the real 60-second POST timeout.
  set_tests_properties(api_responses PROPERTIES TIMEOUT 90)
  qt_add_executable(local_edition_tests ../test/qt/local_edition.cpp
      ${QMONEY_MAIN_WINDOW_SOURCES} src/MainWindow.hpp src/ApiClient.cpp src/ApiClient.hpp
      src/UpdateManager.cpp src/UpdateManager.hpp resources/qmoney.qrc)
  target_link_libraries(local_edition_tests PRIVATE qlementine Qt6::Core Qt6::Gui Qt6::Network Qt6::Svg Qt6::Widgets)
  target_compile_definitions(local_edition_tests PRIVATE QMONEY_LOCAL_ONLY=1
      QMONEY_PROJECT_ROOT="${CMAKE_CURRENT_SOURCE_DIR}/.."
      QMONEY_VERSION="${PROJECT_VERSION}" QMONEY_HAS_EMBEDDED_SERVICE=0)
  if(WIN32)
    target_link_libraries(local_edition_tests PRIVATE bcrypt crypt32 user32)
  endif()
  add_test(NAME local_edition_contracts COMMAND local_edition_tests)
  add_test(NAME local_backend_output_status_contract COMMAND local_edition_tests --backend-output-status-local)
  add_test(NAME local_registration_contract COMMAND local_edition_tests --registration-local)
  add_test(NAME local_registration_rate_limit_contract COMMAND local_edition_tests --registration-rate-limit-local)
  add_test(NAME local_registration_recovery_contract COMMAND local_edition_tests --registration-recovery-local)
  add_test(NAME local_claru_admission_recovery_contract COMMAND local_edition_tests --claru-admission-recovery-local)
  add_test(NAME local_claru_checkpoint_recovery_contract COMMAND local_edition_tests --claru-checkpoint-recovery-local)
  add_test(NAME local_claru_connection_generation_contract COMMAND local_edition_tests --claru-connection-generation-local)
  add_test(NAME local_campaign_start_recovery_contract COMMAND local_edition_tests --campaign-start-recovery-local)
  add_test(NAME local_campaign_hours_contract COMMAND local_edition_tests --campaign-hours-local)
  add_test(NAME local_campaign_authentication_contract COMMAND local_edition_tests --campaign-authentication-local)
  set_tests_properties(local_campaign_authentication_contract PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 15)
  add_test(NAME local_appcheck_integration_contract COMMAND local_edition_tests --appcheck-integration-local)
  add_test(NAME local_claru_contract COMMAND local_edition_tests --claru-local)
  add_test(NAME local_balances_reading_lifecycle_contract COMMAND local_edition_tests --balances-local)
  set_tests_properties(local_balances_reading_lifecycle_contract PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 30)
  set_tests_properties(local_claru_contract PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 35)
  set_tests_properties(local_registration_contract PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 35)
  set_tests_properties(local_registration_rate_limit_contract PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 15)
  set_tests_properties(local_registration_recovery_contract local_campaign_start_recovery_contract
      PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 20)
  set_tests_properties(local_claru_admission_recovery_contract PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 25)
  set_tests_properties(local_claru_checkpoint_recovery_contract PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 25)
  set_tests_properties(local_claru_connection_generation_contract PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 25)
  set_tests_properties(local_campaign_hours_contract PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 15)
  set_tests_properties(local_appcheck_integration_contract PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 15)
  add_executable(android_device_panel_contract ../test/qt/android_device_panel.cpp
      src/AndroidDevicePanel.cpp src/AndroidDevicePanel.hpp src/UsbDeviceWatcher.cpp src/UsbDeviceWatcher.hpp
      src/ApiClient.cpp src/ApiClient.hpp)
  target_link_libraries(android_device_panel_contract PRIVATE Qt6::Core Qt6::Gui Qt6::Network Qt6::Widgets)
  if(WIN32)
    target_link_libraries(android_device_panel_contract PRIVATE user32)
    add_executable(usb_device_watcher_contract ../test/qt/usb_device_watcher.cpp
        src/UsbDeviceWatcher.cpp src/UsbDeviceWatcher.hpp)
    target_link_libraries(usb_device_watcher_contract PRIVATE Qt6::Core Qt6::Gui Qt6::Widgets user32)
    add_test(NAME usb_device_watcher_contract COMMAND usb_device_watcher_contract)
    set_tests_properties(usb_device_watcher_contract PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=windows" TIMEOUT 15)
  endif()
  add_test(NAME android_device_panel_contract COMMAND android_device_panel_contract)
  add_test(NAME local_android_device_campaign_contract COMMAND local_edition_tests --android-device-local)
  add_test(NAME frontend_layout_contract COMMAND local_edition_tests --frontend-layout-local)
  set_tests_properties(android_device_panel_contract PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 35)
  set_tests_properties(local_android_device_campaign_contract PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 15)
  set_tests_properties(frontend_layout_contract PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 120)
  add_test(NAME local_on_demand_contract COMMAND local_edition_tests --demand-local)
  set_tests_properties(local_on_demand_contract PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 30)
  set_tests_properties(local_edition_contracts PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 30)
  set_tests_properties(local_backend_output_status_contract PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 10)
  add_test(NAME local_catalog_progress COMMAND local_edition_tests --catalog-progress-local)
  add_test(NAME local_catalog_monitor COMMAND local_edition_tests --catalog-monitor-local)
  add_test(NAME local_provider_catalog COMMAND local_edition_tests --provider-catalog-local)
  add_test(NAME local_live_data COMMAND local_edition_tests --live-data-local)
  set_tests_properties(local_live_data PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 30)
  add_test(NAME local_passive_data_contract COMMAND local_edition_tests --passive-data-local)
  set_tests_properties(local_passive_data_contract PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 30)
  add_test(NAME local_campaign_verification_monitor COMMAND local_edition_tests --campaign-verification-monitor-local)
  set_tests_properties(local_provider_catalog PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 15)
  set_tests_properties(local_catalog_monitor PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 15)
  add_test(NAME local_catalog_idle_limit COMMAND local_edition_tests --catalog-progress-local --idle)
  add_test(NAME local_preflight_recovery COMMAND local_edition_tests --catalog-progress-local --preflight-recovery)
  add_test(NAME local_preflight_recovery_limit COMMAND local_edition_tests --catalog-progress-local --preflight-recovery --exhaust)
  set_tests_properties(local_catalog_progress local_catalog_idle_limit local_preflight_recovery
      local_preflight_recovery_limit PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 15)
endif()

if(QMONEY_BUILD_LOCAL_TESTS)
  foreach(case flow plan-malformed space-malformed space-insufficient)
    if(case STREQUAL "flow")
      add_test(NAME nymeria_local_${case} COMMAND local_edition_tests --nymeria-local)
    else()
      add_test(NAME nymeria_local_${case} COMMAND local_edition_tests --nymeria-local --nymeria-${case})
    endif()
    set_tests_properties(nymeria_local_${case} PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen" TIMEOUT 25)
  endforeach()
endif()
