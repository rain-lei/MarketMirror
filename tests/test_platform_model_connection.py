import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from design.model_connection import CHECK_SOURCE, ModelConnection, ModelCheckInProgress
from design.server import create_server
from design.text_analysis import analyze, ModelAnalysisError, model_request_error

KEY = 'unit-test-credential-never-returned'


class ModelConnectionTest(unittest.TestCase):
    def test_readiness_is_local_and_does_not_expose_key_or_fingerprint(self):
        connection = ModelConnection()
        with patch('design.model_connection.load_api_key', return_value=KEY), patch('design.model_connection.analyze') as remote:
            value = connection.configuration()
        remote.assert_not_called()
        self.assertEqual(value['credential_status'], 'configured')
        self.assertIsNone(value['last_check'])
        self.assertFalse(value['checking'])
        self.assertNotIn(KEY, json.dumps(value))
        self.assertNotIn('fingerprint', value)

    def test_missing_credentials_do_not_send_a_remote_request(self):
        with patch('design.model_connection.load_api_key', side_effect=FileNotFoundError(KEY)), patch('design.model_connection.analyze') as remote:
            value = ModelConnection().check()
        remote.assert_not_called()
        self.assertEqual(value['check_result']['status'], 'failed')
        self.assertEqual(value['check_result']['error_code'], 'credential_unavailable')
        self.assertNotIn(KEY, json.dumps(value))

    def test_check_uses_only_fixed_synthetic_text_and_retains_a_bound_result(self):
        connection = ModelConnection()
        with patch('design.model_connection.load_api_key', return_value=KEY), patch('design.model_connection.analyze', return_value={'facts': []}) as remote:
            value = connection.check()
            later = connection.configuration()
        remote.assert_called_once_with(CHECK_SOURCE, api_key=KEY)
        self.assertEqual(value['check_result']['status'], 'passed')
        self.assertEqual(value['check_result']['facts_count'], 0)
        self.assertTrue(value['check_result']['synthetic_text'])
        self.assertFalse(value['check_result']['semantic_truth_verified'])
        self.assertEqual(later['last_check'], value['check_result'])
        self.assertNotIn(KEY, json.dumps(value))

    def test_duplicate_checks_are_rejected_while_readiness_stays_available(self):
        connection, entered, release = ModelConnection(), threading.Event(), threading.Event()
        completed = []

        def remote(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise TimeoutError()
            return {'facts': []}

        with patch('design.model_connection.load_api_key', return_value=KEY), patch('design.model_connection.analyze', side_effect=remote) as call:
            worker = threading.Thread(target=lambda: completed.append(connection.check()))
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertTrue(connection.configuration()['checking'])
                with self.assertRaises(ModelCheckInProgress):
                    connection.check()
            finally:
                release.set()
                worker.join(timeout=10)
        self.assertEqual(call.call_count, 1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(completed[0]['check_result']['status'], 'passed')

    def test_credential_rotation_invalidates_a_previous_success(self):
        key = [KEY]
        connection = ModelConnection()
        with patch('design.model_connection.load_api_key', side_effect=lambda: key[0]), patch('design.model_connection.analyze', return_value={'facts': []}):
            connection.check()
            key[0] = 'different-unit-test-credential'
            self.assertIsNone(connection.configuration()['last_check'])

    def test_rotation_during_check_cannot_certify_the_new_configuration(self):
        key = [KEY]
        connection = ModelConnection()

        def remote(*args, **kwargs):
            self.assertEqual(kwargs['api_key'], KEY)
            key[0] = 'changed-during-test'
            return {'facts': []}

        with patch('design.model_connection.load_api_key', side_effect=lambda: key[0]), patch('design.model_connection.analyze', side_effect=remote):
            value = connection.check()
        self.assertEqual(value['check_result']['status'], 'configuration_changed')
        self.assertIsNone(value['last_check'])
        self.assertFalse(value['checking'])

    def test_unexpected_provider_details_are_not_returned_and_retry_is_possible(self):
        connection = ModelConnection()
        with patch('design.model_connection.load_api_key', return_value=KEY), patch('design.model_connection.analyze', side_effect=RuntimeError(KEY)):
            value = connection.check()
        self.assertEqual(value['check_result']['status'], 'failed')
        self.assertFalse(value['checking'])
        self.assertNotIn(KEY, json.dumps(value))
        with patch('design.model_connection.load_api_key', return_value=KEY), patch('design.model_connection.analyze', return_value={'facts': []}):
            self.assertEqual(connection.check()['check_result']['status'], 'passed')

    def test_request_failures_have_safe_specific_codes_even_through_wrapped_causes(self):
        errors = [
            (HTTPError('http://example.test/' + KEY, 401, KEY, {}, io.BytesIO()), 'authentication_failed'),
            (HTTPError('http://example.test/', 429, KEY, {}, io.BytesIO()), 'rate_limited'),
            (HTTPError('http://example.test/', 503, KEY, {}, io.BytesIO()), 'gateway_unavailable'),
            (HTTPError('http://example.test/', 404, KEY, {}, io.BytesIO()), 'request_rejected'),
            (URLError(TimeoutError(KEY)), 'timeout'), (URLError(KEY), 'network_error'),
            (ValueError(KEY), 'invalid_response')]
        for original, code in errors:
            wrapped = RuntimeError(KEY)
            wrapped.__cause__ = original
            error = model_request_error(wrapped)
            self.assertEqual(error.code, code)
            self.assertNotIn(KEY, str(error))

    def test_production_extraction_rejects_invalid_output_with_a_safe_typed_error(self):
        with patch('design.text_analysis.load_api_key', return_value=KEY), patch('design.text_analysis.request_completion', return_value='not JSON ' + KEY):
            with self.assertRaises(ModelAnalysisError) as failure:
                analyze(CHECK_SOURCE)
        self.assertEqual(failure.exception.code, 'invalid_response')
        self.assertNotIn(KEY, str(failure.exception))


class ModelConnectionHttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server = create_server(Path(self.tmp.name), 0)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def stop(self):
        self.server.shutdown()
        self.worker.join(timeout=10)
        self.server.server_close()

    def request(self, route, payload=None, origin=None):
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        request = Request(self.base + route, data=data, headers=headers)
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def test_read_and_check_are_distinct_and_do_not_create_experiments_or_analyses(self):
        with patch('design.model_connection.load_api_key', return_value=KEY), patch('design.model_connection.analyze', return_value={'facts': []}) as remote:
            status, profile = self.request('/api/platform/model')
            self.assertEqual(status, 200)
            remote.assert_not_called()
            status, checked = self.request('/api/platform/model/check', {})
            self.assertEqual(status, 200)
            self.assertEqual(checked['check_result']['status'], 'passed')
            self.assertEqual(self.request('/api/platform/model')[1]['last_check'], checked['check_result'])
        remote.assert_called_once_with(CHECK_SOURCE, api_key=KEY)
        self.assertNotIn(KEY, json.dumps(checked))
        self.assertFalse((Path(self.tmp.name) / 'analyses').exists())
        self.assertEqual(self.request('/api/platform/experiments')[1], [])

    def test_custom_payload_and_cross_origin_requests_never_reach_model(self):
        with patch('design.model_connection.analyze') as remote:
            self.assertEqual(self.request('/api/platform/model/check', {'source': 'user draft'})[0], 400)
            self.assertEqual(self.request('/api/platform/model/check', {}, origin='http://example.test')[0], 403)
        remote.assert_not_called()

    def test_extraction_failures_are_coded_without_saving_a_bad_analysis(self):
        error = ModelAnalysisError('authentication_failed', '网关未接受本机凭据。')
        with patch('design.server.analyze', side_effect=error):
            status, payload = self.request('/api/platform/analyze', {'source': CHECK_SOURCE})
        self.assertEqual(status, 502)
        self.assertEqual(payload['code'], 'authentication_failed')
        self.assertFalse((Path(self.tmp.name) / 'analyses').exists())


if __name__ == '__main__':
    unittest.main()
