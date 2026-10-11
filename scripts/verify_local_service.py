"""Boot the real local service with temporary data and external network blocked."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


def main():
    project = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('isolated_test_runner', project/'scripts/run_tests.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    with tempfile.TemporaryDirectory(prefix='qfar-local-smoke-') as temporary:
        root = Path(temporary)
        guard = root/'guard'
        guard.mkdir()
        (guard/'sitecustomize.py').write_text(runner.OFFLINE_GUARD, encoding='utf-8')
        with socket.socket() as free:
            free.bind(('127.0.0.1', 0))
            port = free.getsockname()[1]
        private_prefixes = ('QMONEY_', 'MINUTE_', 'AWS_', 'HOSTINGER_', 'EGO4D_',
                            'CROWTADO_', 'CLARU_', 'NYMERIA_')
        excluded = {'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                    'PYTHONPATH', 'PYTHONHOME', 'BOTO_CONFIG',
                    'GOOGLE_APPLICATION_CREDENTIALS'}
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(private_prefixes) and key.upper() not in excluded}
        for name in ('aws-credentials', 'aws-config', 'boto-config'):
            (root/name).write_text('', encoding='utf-8')
        env.update(AWS_SHARED_CREDENTIALS_FILE=str(root/'aws-credentials'),
                   AWS_CONFIG_FILE=str(root/'aws-config'), BOTO_CONFIG=str(root/'boto-config'),
                   AWS_EC2_METADATA_DISABLED='true')
        env.update(QMONEY_USER_ROOT=str(root/'state'), QMONEY_LIBRARY_ROOT=str(root/'library'),
                   QMONEY_RUNTIME_ROOT=str(project), QMONEY_APP_VERSION='1.0.63',
                   QMONEY_LOCAL_API_TOKEN='local-isolated-smoke-token',
                   PLAYWRIGHT_BROWSERS_PATH=str(project/'tools/ms-playwright'),
                   PYTHONPATH=os.pathsep.join([str(guard), str(project)]),
                   MINUTE_VPN_ENFORCE='0', MINUTE_REQUIRE_CURL='0', MINUTE_PUBLISH_APP_OPENED='0')
        log = (root/'service.log').open('w', encoding='utf-8')
        # Windows venv python.exe is a redirector; observe the actual process
        # so termination also releases its OS-held state lock before cleanup.
        interpreter = getattr(sys, '_base_executable', sys.executable)
        child = subprocess.Popen([interpreter, '-m', 'moneymin.web', '--no-browser', '--porta', str(port),
                                  '--parent-pid', str(os.getpid())], cwd=project, env=env,
                                 stdout=log, stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        def call(path, *, method='GET', token=True):
            headers={'X-QMoney-Session':env['QMONEY_LOCAL_API_TOKEN']} if token else {}
            if method=='POST': headers['Content-Type']='application/json'
            req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,
                                       data=b'{}' if method=='POST' else None, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.load(response)
        try:
            deadline=time.monotonic()+40
            health=None
            while time.monotonic()<deadline:
                if child.poll() is not None: raise AssertionError('Service exited during startup')
                try:
                    health=call('/api/health')
                    break
                except (OSError, urllib.error.URLError): time.sleep(.2)
            assert health is not None and health['service']['app_version']=='1.0.63'
            try:
                call('/api/health', token=False)
            except urllib.error.HTTPError as error:
                assert error.code==401
            else: raise AssertionError('Unauthenticated service access accepted')
            runtime=call('/api/runtime')
            assert runtime['ready'] is True, runtime.get('missing')
            deadline=time.monotonic()+40
            while time.monotonic()<deadline:
                if call('/api/local/shutdown',method='POST')['ready'] is True: break
                time.sleep(.2)
            else: raise AssertionError('Local service did not drain')
            print(json.dumps({'startup':'passed','authentication':'passed','runtime':runtime['components'],
                              'shutdown':'passed','external_network':'blocked'},ensure_ascii=False))
        finally:
            child.terminate()
            try: child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait(timeout=5)
            log.close()


if __name__=='__main__':
    main()
