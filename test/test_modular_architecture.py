"""Static boundary checks for the modular HTTP extraction.

These checks read source and the trusted pre-refactor JSON baseline. They do
not import the application or its original checkout and never contact services.
"""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "moneymin" / "web"
BASELINE = ROOT / "docs" / "modularizacao" / "arquitetura-baseline.json"
CATALOG_ADDITIONS = (
    ('provider_catalogs', 'get_provider_catalog', (('get', 'literal', '/api/catalogs/providers'),)),
    ('provider_catalogs', 'prepare_provider_catalog', (('post', 'literal', '/api/catalogs/providers/prepare'),)),
    ('integrations', 'recheck_appcheck_integration', (('post', 'literal', '/api/integrations/appcheck/recheck'),)),
    ('integrations', 'get_appcheck_status', (('get', 'literal', '/api/integrations/appcheck/status'),)),
    ('integrations', 'get_android_device', (('get', 'literal', '/api/integrations/android/device'),)),
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _baseline_routes() -> set[tuple[str, str, str]]:
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    http = {"GET", "POST", "PUT", "PATCH", "DELETE"}
    return {(row["method"], ast.literal_eval(row["path"]), row["handler"])
            for row in data["server"]["routes"] if row["method"] in http}


def _registry_routes() -> tuple[set[tuple[str, str, str]], tuple]:
    path = WEB / "routes" / "registry.py"
    tree = _tree(path)
    handlers_node = next(node.value for node in tree.body
                         if isinstance(node, ast.Assign)
                         and any(isinstance(target, ast.Name) and target.id == "HANDLERS"
                                 for target in node.targets))
    handlers = ast.literal_eval(handlers_node)
    routes = set()
    for _domain, endpoint, decorators in handlers:
        for method, kind, argument in decorators:
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if kind != "literal" or not isinstance(argument, str):
                raise AssertionError(f"Rota {endpoint} precisa declarar path literal no registry")
            routes.add((method.upper(), argument, endpoint))
    return routes, handlers


def _normalized_original_decorator(source: str) -> tuple[str, str, str | None]:
    node = ast.parse(f"@{source}\ndef _handler():\n    pass\n").body[0].decorator_list[0]
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "app":
        return node.attr, "handler", None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        method = node.func.attr
        if isinstance(node.func.value, ast.Name) and node.func.value.id == "app":
            if method in {"get", "post", "put", "patch", "delete"}:
                return method, "literal", ast.literal_eval(node.args[0])
            if method == "errorhandler":
                return method, "dependency", ast.unparse(node.args[0])
    if isinstance(node, ast.Name):
        return "custom", "dependency", node.id
    raise AssertionError(f"decorator original não reconhecido: {source}")


def _python_sources(*folders: Path) -> list[Path]:
    return sorted(path for folder in folders for path in folder.rglob("*.py")
                  if "__pycache__" not in path.parts and path.name != "__init__.py")


class ModularHttpArchitectureTests(unittest.TestCase):
    def test_registry_matches_original_http_route_contract(self):
        expected = _baseline_routes() | {
            (method.upper(), path, endpoint)
            for _domain, endpoint, decorators in CATALOG_ADDITIONS
            for method, _kind, path in decorators}
        actual, handlers = _registry_routes()
        self.assertEqual(len(handlers), len({(domain, name) for domain, name, _ in handlers}),
                         "cada implementação deve aparecer uma vez no registry")
        self.assertEqual(actual, expected,
                         f"faltando={sorted(expected - actual)}; extras={sorted(actual - expected)}")

    def test_registry_preserves_original_handler_and_decorator_order(self):
        original = json.loads((ROOT / "docs" / "modularizacao" / "http-extraction.json").read_text(encoding="utf-8"))
        _routes, actual = _registry_routes()
        expected = [(row["module"], row["function"],
                     tuple(_normalized_original_decorator(dec) for dec in row["decorators"]))
                    for row in original["handlers"]]
        self.assertEqual(actual, tuple(expected) + CATALOG_ADDITIONS,
                         "preserve todos os hooks e contratos originais; novas rotas de catálogo são acrescentadas ao final")

    def test_registry_features_and_handler_declarations_are_consistent(self):
        tree = _tree(WEB / "routes" / "registry.py")
        features_node = next(node.value for node in tree.body
                             if isinstance(node, ast.Assign)
                             and any(isinstance(target, ast.Name) and target.id == "FEATURES"
                                     for target in node.targets))
        self.assertIsInstance(features_node, ast.Dict)
        feature_domains = {ast.literal_eval(key) for key in features_node.keys}
        handler_rows = ast.literal_eval(next(node.value for node in tree.body
                                if isinstance(node, ast.Assign)
                                and any(isinstance(target, ast.Name) and target.id == "HANDLERS"
                                        for target in node.targets)))
        self.assertEqual(feature_domains, {domain for domain, _, _ in handler_rows})
        for domain, name, _decorators in handler_rows:
            module_path = WEB / "routes" / f"{domain}.py"
            self.assertTrue(module_path.is_file(), f"feature ausente: {domain}")
            module = _tree(module_path)
            functions = {node.name for node in module.body
                         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
            functions.update(alias.asname or alias.name for node in module.body
                             if isinstance(node, ast.ImportFrom) for alias in node.names)
            self.assertIn(name, functions, f"{domain}.{name} não existe no módulo declarado")

    def test_feature_dependency_declarations_cover_every_binding(self):
        modules = [path for path in _python_sources(WEB / "routes", WEB / "services")
                   if path.name != "registry.py"]
        self.assertTrue(modules)
        for path in modules:
            tree = _tree(path)
            requires_node = next((node.value for node in tree.body
                                  if isinstance(node, ast.Assign)
                                  and any(isinstance(target, ast.Name) and target.id == "REQUIRES"
                                          for target in node.targets)), None)
            self.assertIsNotNone(requires_node, f"{path.relative_to(ROOT)} sem REQUIRES")
            requires = ast.literal_eval(requires_node)
            self.assertEqual(len(requires), len(set(requires)), f"REQUIRES duplicado em {path.name}")
            bindings = {node.attr for node in ast.walk(tree)
                        if isinstance(node, ast.Attribute)
                        and isinstance(node.value, ast.Name) and node.value.id == "_services"}
            self.assertFalse(bindings - set(requires),
                             f"{path.relative_to(ROOT)} usa dependências não declaradas: {sorted(bindings - set(requires))}")

    def test_extracted_code_has_no_untranslated_global_or_nonlocal_declarations(self):
        for path in [p for p in _python_sources(WEB / "routes", WEB / "services")
                     if p.name != "registry.py"]:
            for node in ast.walk(_tree(path)):
                self.assertNotIsInstance(node, (ast.Global, ast.Nonlocal),
                                         f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', '?')} mantém declaração lexical antiga")

    def test_nested_route_callbacks_do_not_read_flask_request_proxy(self):
        proxy_reads = []
        for path in _python_sources(WEB / "routes"):
            if path.name == "registry.py":
                continue
            for top in _tree(path).body:
                if not isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for nested in ast.walk(top):
                    if nested is top or not isinstance(nested, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                        continue
                    for node in ast.walk(nested):
                        if ((isinstance(node, ast.Attribute) and node.attr == "request"
                             and isinstance(node.value, ast.Name) and node.value.id == "_services")
                                or isinstance(node, ast.Name) and node.id == "request"):
                            proxy_reads.append(f"{path.relative_to(ROOT)}:{node.lineno} in {nested.name if hasattr(nested, 'name') else 'lambda'}")
        self.assertEqual(proxy_reads, [], "callbacks nested under a route must snapshot request values before scheduling work")

    def test_factory_uses_per_app_bindings_and_starts_portable_worker_after_registration(self):
        tree = _tree(WEB / "server.py")
        factory = next(node for node in tree.body
                       if isinstance(node, ast.FunctionDef) and node.name == "create_app")
        source = (WEB / "server.py").read_text(encoding="utf-8")
        self.assertRegex(source, r"DependencyBindings\(globals\(\),\s*dict\(locals\(\)\)\)")
        self.assertRegex(source, r"app\.extensions\[['\"]feature_dependencies['\"]\]\s*=\s*dependencies")
        calls = [(node.lineno, node) for node in ast.walk(factory)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
        registration_lines = [line for line, node in calls
                              if isinstance(node.func.value, ast.Name)
                              and node.func.value.id == "route_registry" and node.func.attr == "register_routes"]
        worker_lines = [line for line, node in calls
                        if isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "nymeria_library_worker" and node.func.attr == "start"]
        self.assertEqual(len(registration_lines), 1)
        self.assertTrue(worker_lines)
        self.assertLess(registration_lines[0], min(worker_lines),
                        "startup worker must wait until all route dependencies and hooks are bound")

    def test_feature_modules_do_not_import_the_composition_root(self):
        roots = (WEB / "routes", WEB / "services")
        for path in _python_sources(*roots):
            for node in ast.walk(_tree(path)):
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imported = [node.module or ""]
                else:
                    continue
                self.assertFalse(any(name == "moneymin.web.server" or name.endswith(".web.server")
                                     for name in imported),
                                 f"{path.relative_to(ROOT)} importa a composition root")
                self.assertFalse(any(name in {"server", ".server", "..server"} for name in imported),
                                 f"{path.relative_to(ROOT)} importa server")

    def test_relative_imports_in_moneymin_resolve_to_source(self):
        # Extracted domain modules use function-local imports too, so scan the
        # whole package rather than only the new HTTP composition tree.
        sources = _python_sources(ROOT / "moneymin")
        for path in sources:
            relative_parts = path.relative_to(ROOT).with_suffix("").parts
            module_parts = relative_parts[:-1] if path.name == "__init__.py" else relative_parts
            package = list(module_parts) if path.name == "__init__.py" else list(module_parts[:-1])
            for node in ast.walk(_tree(path)):
                if not isinstance(node, ast.ImportFrom) or node.level == 0:
                    continue
                base = package[:len(package) - node.level + 1]
                prefix = ".".join(base)
                target = f"{prefix}.{node.module}" if node.module else prefix
                candidates = [ROOT.joinpath(*target.split(".")).with_suffix(".py"),
                              ROOT.joinpath(*target.split("."), "__init__.py")]
                if node.module is None:
                    candidates += [ROOT.joinpath(*(target + "." + alias.name).split(".")).with_suffix(".py")
                                   for alias in node.names]
                    candidates += [ROOT.joinpath(*(target + "." + alias.name).split("."), "__init__.py")
                                   for alias in node.names]
                self.assertTrue(any(candidate.is_file() for candidate in candidates),
                                f"import relativo não resolvido em {path.relative_to(ROOT)}:{node.lineno}: {node.module!r}")

    def test_dependency_bindings_enforce_declarations_live_patches_and_local_isolation(self):
        path = WEB / "dependencies.py"
        spec = importlib.util.spec_from_file_location("_qfar_dependency_bindings_audit", path)
        self.assertIsNotNone(spec and spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        namespace = {"target": lambda: "before"}
        first_local = {"state": {"owner": "first"}}
        second_local = {"state": {"owner": "second"}}
        first = module.DependencyBindings(namespace, first_local).scope(("target", "state"))
        second = module.DependencyBindings(namespace, second_local).scope(("target", "state"))

        def implementation(dependencies):
            return dependencies.target(), dependencies.state

        bound = first.bind(implementation, ("target", "state"))
        self.assertEqual(bound()[0], "before")
        namespace["target"] = lambda: "patched after bind"
        self.assertEqual(bound()[0], "patched after bind")
        self.assertIs(first.state, first_local["state"])
        first.state = {"owner": "updated first"}
        self.assertEqual(second.state, {"owner": "second"})
        self.assertEqual(first.state, {"owner": "updated first"})
        first.target = lambda: "global write"
        self.assertEqual(namespace["target"](), "global write")
        with self.assertRaisesRegex(AttributeError, "Undeclared feature dependency"):
            _ = first.forbidden
        missing = module.DependencyBindings({}, {}).scope(("declared_but_missing",))
        with self.assertRaisesRegex(AttributeError, "Missing feature dependency"):
            _ = missing.declared_but_missing

    def test_no_dynamic_namespace_injection_or_eval_in_feature_modules(self):
        for path in _python_sources(WEB / "routes", WEB / "services"):
            for node in ast.walk(_tree(path)):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"exec", "eval", "inject_globals"},
                                     f"{path.relative_to(ROOT)} usa {node.func.id}")

    def test_provider_registry_targets_real_facade_operations(self):
        registry = _tree(ROOT / "moneymin" / "integrations" / "registry.py")
        providers = next(node.value for node in registry.body
                         if isinstance(node, ast.AnnAssign)
                         and isinstance(node.target, ast.Name) and node.target.id == "_PROVIDERS")
        # Evaluate only the literal registry data, without importing providers.
        contracts = {}
        for row in providers.values:
            args = [ast.literal_eval(value) for value in row.args]
            kwargs = {kw.arg: ast.literal_eval(kw.value) for kw in row.keywords}
            provider_id, module, catalog, prepare = args[:4]
            contracts[provider_id] = (module, catalog, prepare,
                                      kwargs.get("prepare_module", "moneymin.campaign"))
        self.assertEqual(set(contracts), {"ego4d", "holoassist", "nymeria"})
        for provider_id, (module_name, catalog_name, prepare_name, prepare_module) in contracts.items():
            provider_path = ROOT.joinpath(*module_name.split("." )).with_suffix(".py")
            provider_tree = _tree(provider_path)
            provider_defs = {node.name for node in provider_tree.body
                             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
            campaign_path = ROOT.joinpath(*prepare_module.split(".")).with_suffix(".py")
            campaign_tree = _tree(campaign_path)
            campaign_defs = {node.name for node in campaign_tree.body
                             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
            self.assertIn(catalog_name, provider_defs,
                          f"{provider_id} catálogo não existe: {module_name}.{catalog_name}")
            self.assertIn(prepare_name, campaign_defs,
                          f"{provider_id} preparação não existe: {prepare_module}.{prepare_name}")

    def test_auditor_separates_deferred_cycles_from_import_time_cycles(self):
        path = ROOT / "scripts" / "audit_modularity.py"
        spec = importlib.util.spec_from_file_location("_qfar_modularity_audit", path)
        self.assertIsNotNone(spec and spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        report = module.build()
        self.assertEqual(report["eager_import_cycles"], [],
                         "direct module-body imports must not form cycles")
        self.assertEqual(report["summary"]["import_cycles"], len(report["import_cycles"]))
        self.assertIn("deferred function-local imports", report["import_cycle_scope"])

    def test_upload_facade_resolves_late_patches_for_extracted_services(self):
        # An offline call through a public facade proves the adapter does not
        # capture the original dependency when its service object is built.
        from moneymin import upload

        original = upload.probe_video
        try:
            upload.probe_video = lambda _path: {"duration_ms": 4321}
            self.assertEqual(upload._probe_duration_ms("not-read"), 4321)
        finally:
            upload.probe_video = original

    def test_campaign_facade_resolves_late_patches_for_extracted_services(self):
        from moneymin import campaign

        original = campaign.ffmpeg_bin
        try:
            campaign.ffmpeg_bin = lambda: "patched-ffmpeg-bin"
            self.assertEqual(campaign._ffmpeg_bin(), "patched-ffmpeg-bin")
        finally:
            campaign.ffmpeg_bin = original

    def test_factory_preserves_process_callback_and_separates_app_state(self):
        from moneymin.web import server

        callback = server.RUNNER.on_restriction
        self.assertIsNotNone(callback, "callback de restrição deve ser instalado uma vez na composição do módulo")
        # A factory can admit a background metadata sync when imported-state
        # is present. Keep this contract test fully offline and side-effect free.
        with patch.object(server.LibraryPreparationRunner, "start", return_value={}):
            first_app = server.create_app(for_testing=True)
            first_dependencies = first_app.extensions["feature_dependencies"]
            second_app = server.create_app(for_testing=True)
            second_dependencies = second_app.extensions["feature_dependencies"]

        self.assertIs(server.RUNNER.on_restriction, callback,
                      "create_app não deve substituir a callback process-level")
        self.assertIsNot(first_dependencies, second_dependencies)
        for name in ("task_catalog", "campaign_verifications", "preflights",
                     "original_preflights", "original_starts", "preflight_lock",
                     "campaign_drain", "local_shutdown", "portable_setup"):
            with self.subTest(app_state=name):
                self.assertIsNot(getattr(first_dependencies, name),
                                 getattr(second_dependencies, name))

        first_dependencies.campaign_drain["requested"] = True
        first_dependencies.preflights["only-first-app"] = {"state": "isolated"}
        self.assertFalse(second_dependencies.campaign_drain["requested"])
        self.assertNotIn("only-first-app", second_dependencies.preflights)

        # Prove the process callback still resolves the facade patch at call
        # time, without invoking account persistence or using a real account.
        calls = []
        original_ban_accounts = server._ban_accounts
        try:
            server._ban_accounts = lambda issues: calls.append(issues)
            callback("synthetic-confirmed@example.invalid")
        finally:
            server._ban_accounts = original_ban_accounts
        self.assertEqual(calls, [[{
            "email": "synthetic-confirmed@example.invalid",
            "restriction_confirmed": True,
            "stage": "Envio da campanha",
            "reason": "Restrição confirmada pela plataforma durante o envio.",
        }]])

    def test_original_library_index_callback_captures_handler_local_index(self):
        # The factory's registered endpoint is deliberately also named
        # ``index``. The handler-local SQLite path must win inside its callback.
        from moneymin.web.dependencies import DependencyBindings
        from moneymin.web.routes.library_ego4d import index_original_library
        import sqlite3

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("ego4d.json", "clips.csv"):
                (root / name).write_text("synthetic", encoding="utf-8")
            index_path = root / "original-library.sqlite3"
            indexed_with = []
            summary_calls = 0

            class Library:
                @staticmethod
                def library_summary(index, _root):
                    nonlocal summary_calls
                    summary_calls += 1
                    return {"needs_index": summary_calls == 1} if summary_calls == 1 else {"indexed": True}

                @staticmethod
                def index_library(_root, index, *, progress):
                    indexed_with.append(index)

            class Job:
                @staticmethod
                def get(_key, build, *, scope):
                    self.assertEqual(scope, "original-library")
                    return build(lambda _message: None)

            route_endpoint_named_index = lambda: "route endpoint, not a database path"
            dependencies = DependencyBindings({}, {
                "original_library_paths": lambda: (root, index_path),
                "ego4d_library": Library,
                "original_library_index": Job,
                "jsonify": lambda value: value,
                "sqlite3": sqlite3,
                "index": route_endpoint_named_index,
            })
            response, status = index_original_library(dependencies)

        self.assertEqual(status, 200)
        self.assertEqual(response, {"indexed": True})
        self.assertEqual(indexed_with, [index_path])

    def test_every_mainwindow_translation_unit_is_in_native_target(self):
        source_dir = ROOT / "desktop" / "src"
        units = {path.name for path in source_dir.glob("MainWindow*.cpp")}
        cmake = (ROOT / "desktop" / "CMakeLists.txt").read_text(encoding="utf-8")
        block_start = cmake.index("set(QMONEY_MAIN_WINDOW_SOURCES")
        block_end = cmake.index(")", block_start)
        declared = set(re.findall(r"src/(MainWindow[^\s)]*\.cpp)", cmake[block_start:block_end]))
        self.assertEqual(declared, units, f"ausente no CMake={sorted(units - declared)}; sem unidade={sorted(declared - units)}")
        self.assertIn("src/DevelopmentProjectRoot.hpp", cmake[block_start:block_end])
        support = (source_dir / "MainWindowSupport.hpp").read_text(encoding="utf-8")
        marker = (source_dir / "DevelopmentProjectRoot.hpp").read_text(encoding="utf-8")
        self.assertIn('"DevelopmentProjectRoot.hpp"', support)
        self.assertIn('moneymin/web/__main__.py', marker)
        self.assertIn("resolveDevelopmentProjectRoot", (source_dir / "MainWindowService.cpp").read_text(encoding="utf-8"))


    def test_feature_units_keep_foreign_widget_state_private(self):
        forbidden = {
            "MainWindowLibrary.cpp": (
                "_taskLoadGeneration", "_taskCatalogWatch", "_taskCatalogClock", "_taskReload",
                "_campaignTasks", "_campaignSelectedTaskIds", "_campaignTaskSelectionTouched"),
            "MainWindowCampaigns.cpp": (
                "_accountsTable", "_accountChecks", "_balancesTable", "_balancesSnapshot"),
            "MainWindowCatalogs.cpp": (
                "_accountsTable", "_accountChecks", "_balancesTable", "_balancesSnapshot"),
            "MainWindowAccounts.cpp": (
                "_hostingerToken", "_hostingerProfile", "_ego4dAccessKey",
                "_ego4dSecretKey", "_ego4dSessionToken"),
            "MainWindowBalances.cpp": ("_accountsTable", "_accountChecks"),
        }
        for name, fields in forbidden.items():
            source = (ROOT / "desktop/src" / name).read_text(encoding="utf-8")
            source = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)
            for field in fields:
                with self.subTest(unit=name, field=field):
                    self.assertNotRegex(source, rf"\b{field}\b",
                                        "use the owning feature's coordination method")

    def test_usb_components_are_shared_by_app_and_native_tests(self):
        cmake = (ROOT / "desktop/CMakeLists.txt").read_text(encoding="utf-8")
        start = cmake.index("set(QMONEY_MAIN_WINDOW_SOURCES")
        block = cmake[start:cmake.index(")", start)]
        for component in ("AndroidDevicePanel", "UsbDeviceWatcher"):
            for extension in ("cpp", "hpp"):
                with self.subTest(component=component, extension=extension):
                    self.assertIn(f"src/{component}.{extension}", block)
        watcher = (ROOT / "desktop/src/UsbDeviceWatcher.cpp").read_text(encoding="utf-8")
        watcher += (ROOT / "desktop/src/UsbDeviceWatcher.hpp").read_text(encoding="utf-8")
        watcher = re.sub(r"/\*.*?\*/|//[^\n]*", "", watcher, flags=re.S)
        self.assertNotRegex(watcher, r"\b(?:ApiClient|QNetworkAccessManager|QProcess|QTimer)\b")


if __name__ == "__main__":
    unittest.main()
