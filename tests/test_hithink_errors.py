import json
import unittest

from trend_monitor.providers.hithink import ErrorCategory, HithinkProvider, HithinkProviderError
from trend_monitor.providers.hithink.errors import (
    category_for_business_code,
    category_for_http_status,
)
from trend_monitor.providers.hithink.transport import HttpResponse


class FakeTransport:
    def __init__(self, response: HttpResponse):
        self.response = response

    def get(self, url, headers, timeout):
        return self.response


class UnavailableTransport:
    def get(self, url, headers, timeout):
        raise HithinkProviderError(ErrorCategory.NETWORK_ERROR, "provider unavailable")


class ErrorMappingTests(unittest.TestCase):
    def test_business_error_mapping(self):
        self.assertEqual(category_for_business_code(2003), ErrorCategory.AUTH_ERROR)
        self.assertEqual(category_for_business_code(4001), ErrorCategory.RATE_LIMIT)
        self.assertEqual(category_for_business_code(3004), ErrorCategory.UNSUPPORTED)
        self.assertEqual(category_for_business_code(3002), ErrorCategory.EMPTY_DATA)
        self.assertEqual(category_for_business_code(1002), ErrorCategory.INVALID_DATA)
        self.assertEqual(category_for_business_code(5001), ErrorCategory.NETWORK_ERROR)

    def test_http_error_mapping(self):
        self.assertEqual(category_for_http_status(401), ErrorCategory.AUTH_ERROR)
        self.assertEqual(category_for_http_status(429), ErrorCategory.RATE_LIMIT)
        self.assertEqual(category_for_http_status(503), ErrorCategory.NETWORK_ERROR)

    def test_missing_key_is_explicit(self):
        provider = HithinkProvider(api_key=None, dotenv_path="/missing/.env")
        with self.assertRaises(HithinkProviderError) as raised:
            provider.search_symbols("600487")
        self.assertEqual(raised.exception.category, ErrorCategory.AUTH_ERROR)
        self.assertIn("BLOCKED_BY_API_KEY", str(raised.exception))

    def test_business_auth_response_is_mapped(self):
        body = json.dumps({"code": 2003, "message": "invalid", "data": None}).encode()
        provider = HithinkProvider(
            api_key="fake", transport=FakeTransport(HttpResponse(200, body))
        )
        with self.assertRaises(HithinkProviderError) as raised:
            provider.search_symbols("600487")
        self.assertEqual(raised.exception.category, ErrorCategory.AUTH_ERROR)

    def test_malformed_response_is_invalid_data(self):
        provider = HithinkProvider(
            api_key="fake", transport=FakeTransport(HttpResponse(200, b"not-json"))
        )
        with self.assertRaises(HithinkProviderError) as raised:
            provider.search_symbols("600487")
        self.assertEqual(raised.exception.category, ErrorCategory.INVALID_DATA)

    def test_provider_unavailable_is_not_swallowed(self):
        provider = HithinkProvider(api_key="fake", transport=UnavailableTransport())
        with self.assertRaises(HithinkProviderError) as raised:
            provider.search_symbols("600487")
        self.assertEqual(raised.exception.category, ErrorCategory.NETWORK_ERROR)


if __name__ == "__main__":
    unittest.main()
