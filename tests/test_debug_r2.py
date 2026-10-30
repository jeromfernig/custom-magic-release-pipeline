from mtg_pipeline.debug_r2 import _safe_headers


def test_r2_debug_output_excludes_authorization_header():
    headers = {
        "Authorization": "AWS4-HMAC-SHA256 Credential=secret",
        "Content-Length": "123",
        "X-Amz-Content-Sha256": "payload-hash",
    }

    assert _safe_headers(headers) == {"Content-Length": "123", "X-Amz-Content-Sha256": "payload-hash"}
