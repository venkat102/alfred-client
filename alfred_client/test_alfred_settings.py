"""Tests for Alfred Settings validators.

Pure-Python tests for the URL validator helper. No Frappe bench needed -
the helper is a module-level function that does its own URL parsing with
urllib.parse, and the tests here exercise it directly.

Run via:
    bench --site dev.alfred execute alfred_client.test_alfred_settings.run_tests
"""

from alfred_client.alfred_settings.doctype.alfred_settings.alfred_settings import (
	_check_processing_app_url,
)


def _assert(condition, message):
	if not condition:
		raise AssertionError(message)


# Cases accepted by the validator (return None).
_ACCEPTED = [
	# Empty / unset: optional field, accepted until the admin configures it.
	"",
	"   ",
	# Production: HTTPS / WSS on any host.
	"https://processing.example.com",
	"https://processing.example.com:8001",
	"wss://processing.example.com:8001/ws",
	# Local dev: plaintext permitted against loopback.
	"http://localhost:8001",
	"http://localhost:8001/ws",
	"ws://localhost:8001/ws",
	"http://127.0.0.1:8001",
	"ws://127.0.0.1:8001/ws",
	# 127.x range: allowed because any 127.x.x.x address is loopback.
	"http://127.0.0.5:8001",
	"http://127.42.0.1",
	# IPv6 loopback.
	"http://[::1]",
	"http://[::1]/ws",
	# Surrounding whitespace: stripped before parsing.
	"  https://processing.example.com  ",
]


# Cases rejected by the validator (return an error message).
_REJECTED = [
	# Private network addresses: still sent over the wire; attackers on the
	# same VLAN or via ARP spoofing can sniff.
	"http://10.0.0.5:8001",
	"http://192.168.1.100:8001",
	"ws://10.0.0.5:8001/ws",
	# Public hostnames: plaintext is never OK off-host.
	"http://processing.example.com",
	"http://processing.example.com:8001",
	"ws://processing.example.com:8001/ws",
	# Unsupported schemes.
	"ftp://x.example.com",
	"gopher://example.com",
	"file:///etc/passwd",
]


def run_tests():
	print("\n=== Alfred Settings Validator Tests ===\n")

	# Test 1: accepted URLs pass without error.
	print(f"Test 1: {len(_ACCEPTED)} accepted URLs return None...")
	for url in _ACCEPTED:
		result = _check_processing_app_url(url)
		_assert(
			result is None,
			f"Expected None for accepted URL {url!r}, got: {result!r}",
		)
	print("  PASSED\n")

	# Test 2: rejected URLs return an actionable error message.
	print(f"Test 2: {len(_REJECTED)} rejected URLs return an error...")
	for url in _REJECTED:
		result = _check_processing_app_url(url)
		_assert(
			result is not None,
			f"Expected error for rejected URL {url!r}, got None",
		)
		_assert(
			len(result) > 50,
			f"Error message too short for {url!r}: {result!r}",
		)
	print("  PASSED\n")

	# Test 3: http on 10.x gives a fix-it hint that points at https.
	print("Test 3: rejection message includes a https:// remediation...")
	result = _check_processing_app_url("http://10.0.0.5:8001")
	_assert("https://" in result, f"Expected 'https://' in fix-it hint, got: {result!r}")
	_assert("10.0.0.5:8001" in result, f"Expected host:port echoed in hint, got: {result!r}")
	print("  PASSED\n")

	# Test 4: unsupported scheme names the scheme it saw.
	print("Test 4: unsupported scheme is named in the error...")
	result = _check_processing_app_url("ftp://example.com")
	_assert("ftp" in result.lower(), f"Expected 'ftp' in error, got: {result!r}")
	print("  PASSED\n")

	# Test 5: whitespace is trimmed, not passed to urlparse raw.
	print("Test 5: surrounding whitespace trimmed...")
	result = _check_processing_app_url("  https://ok.example.com  ")
	_assert(result is None, f"Expected None for padded https URL, got: {result!r}")
	print("  PASSED\n")

	print("=== All Alfred Settings validator tests passed ===\n")


if __name__ == "__main__":
	run_tests()
