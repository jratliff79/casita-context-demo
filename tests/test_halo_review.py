import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import authentication as auth
import demo
import git_review as review
from git_review_example import make_repository
import halo_review as halo


class HaloReviewChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repository = self.root / "repository"
        self.commit = make_repository(self.repository)
        self.input = self.root / "input"
        self.input.mkdir()
        spec = {"schema": review.SPEC_SCHEMA, "source_label": "synthetic unit fixture",
                "commit": self.commit, "sensitivity": "synthetic", "purpose": "Synthetic static review",
                "selections": [{"id": "classifier", "path": "classifier.py", "start": 3, "end": 6}]}
        spec_path = self.root / "spec.json"
        spec_path.write_bytes(demo.canonical(spec))
        ident = review.create_context(self.input / "context", self.repository, spec_path)
        self.pins = {"schema": "casita-context-demo.review-input-pins.v1", "content_id": ident,
                     "directory_key": "casita.directory.v1:" + "a" * 43, "archive_sha256": "b" * 64}
        self.pin_path = self.root / "pins.json"
        self.pin_path.write_bytes(demo.canonical(self.pins))
        (self.input / "input-pins.json").write_bytes(demo.canonical(self.pins))
        auth.make_demo_key(self.root / "key")
        auth.sign_demo_pins(self.pin_path, self.root / "key", self.root / "pins.sig", "review-input")
        auth.provision_demo_signer(self.root / "key", self.root / "allowed", "unit-sender", "review-input")
        self.args = SimpleNamespace(context=self.input, pins=self.pin_path, signature=self.root / "pins.sig",
                                    allowed_signers=self.root / "allowed", signer="unit-sender",
                                    source=self.repository, model="unit-model", output=self.root / "request")
        def output(path):
            path.mkdir()
            return path
        output_patch = patch.object(halo, "new_output", side_effect=output)
        self.create = output_patch.start()
        self.addCleanup(output_patch.stop)
        halo.prepare(self.args)
        self.bundle = self.args.output
        self.request_hash = (self.bundle / "request-sha256.txt").read_text().strip()
        self.request = review.read_json(self.bundle / "request.json")
        payload = json.loads(self.request["messages"][1]["content"])
        source = payload["sources"][0]
        self.report = payload["report_template"]
        self.report["limitations"] = ["Synthetic test report, no inference or execution."]
        self.report["findings"] = [{"title": "Synthetic citation", "body": "Synthetic evidence binding test",
                                   "priority": "P3", "citations": [{"selection_id": source["id"],
                                    "path": source["path"], "blob_sha256": source["blob_sha256"],
                                    "line_start": 3, "line_end": 3, "excerpt": source["lines"][0]["text"]}]}]

    def validate_args(self, value, name="validated"):
        path = self.root / (name + "-raw.json")
        path.write_bytes(demo.canonical(value))
        return SimpleNamespace(request=self.bundle, request_sha256=self.request_hash,
                               response=path, output=self.root / name)

    def test_numbering_preserves_original_non_one_start_and_dirty_checkout_is_excluded(self):
        payload = json.loads(self.request["messages"][1]["content"])
        self.assertEqual([line["line"] for line in payload["sources"][0]["lines"]], [3, 4, 5, 6])
        self.assertNotIn("DIRTY_CHECKOUT", json.dumps(payload))
        self.assertNotIn(str(self.root), json.dumps(payload))
        self.assertNotIn("tools", self.request)
        self.assertFalse(self.request["allow_download"])

    def test_forged_signed_pins_and_restored_pin_mismatch_fail_before_request_creation(self):
        self.args.output = self.root / "not-created"
        self.args.signer = "other-sender"
        with self.assertRaisesRegex(ValueError, "signature rejected"):
            halo.prepare(self.args)
        self.args.signer = "unit-sender"
        (self.input / "input-pins.json").write_bytes(demo.canonical(dict(self.pins, directory_key="casita.directory.v1:" + "c" * 43)))
        with self.assertRaisesRegex(ValueError, "restored input pins"):
            halo.prepare(self.args)
        self.assertFalse(self.args.output.exists())

    def test_mcp_http_and_direct_reports_validate_without_executing_response_text(self):
        sentinel = self.root / "never-executed"
        self.report["findings"][0]["body"] = f"open({str(sentinel)!r}, 'w').write('bad')"
        raw = demo.canonical(self.report).decode()
        envelopes = [self.report, {"content": [{"type": "text", "text": raw}], "isError": False},
                     {"choices": [{"finish_reason": "stop", "message": {"content": raw}}]}]
        for i, value in enumerate(envelopes):
            args = self.validate_args(value, f"valid-{i}")
            halo.validate(args)
            self.assertEqual(review.read_json(args.output / "report.json"), self.report)
            self.assertTrue(review.read_json(args.output / "receipt.json")["accepted"])
        self.assertFalse(sentinel.exists())

    def test_bad_citation_is_retained_and_no_accepted_report_written(self):
        self.report["findings"][0]["citations"][0]["excerpt"] = "invented source"
        args = self.validate_args(self.report)
        with self.assertRaisesRegex(ValueError, "excerpt differs"):
            halo.validate(args)
        self.assertEqual((args.output / "raw-response.json").read_bytes(), args.response.read_bytes())
        self.assertFalse((args.output / "report.json").exists())
        self.assertFalse(review.read_json(args.output / "receipt.json")["accepted"])

    def test_wrong_context_truncated_tools_and_error_envelopes_reject(self):
        wrong = dict(self.report, context_id="0" * 64)
        values = [wrong, {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
                  {"choices": [{"finish_reason": "stop", "message": {"tool_calls": [1], "content": "{}"}}]},
                  {"content": [{"type": "text", "text": "{}"}], "isError": True}]
        for i, value in enumerate(values):
            args = self.validate_args(value, f"invalid-{i}")
            with self.assertRaises(ValueError):
                halo.validate(args)
            self.assertFalse((args.output / "report.json").exists())

    def test_changed_request_hash_and_added_model_tools_reject(self):
        self.request["tools"] = [{"type": "function", "function": {"name": "unapproved"}}]
        raw = demo.canonical(self.request)
        (self.bundle / "request.json").write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "reviewed request hash"):
            halo.verified_request(self.bundle, self.request_hash)
        with self.assertRaisesRegex(ValueError, "frozen verified context"):
            halo.verified_request(self.bundle, demo.digest(raw))

    def test_send_requires_opt_in_and_only_loopback_without_redirects_or_proxies(self):
        args = SimpleNamespace(allow_inference=False, endpoint="http://127.0.0.1:12345/v1/chat/completions",
                               request=self.bundle, request_sha256=self.request_hash, output=self.root / "sent")
        with patch.object(halo, "build_opener") as opener:
            with self.assertRaisesRegex(ValueError, "allow-inference"):
                halo.send(args)
            args.allow_inference = True
            for endpoint in ["http://example.com/v1/chat/completions", "http://localhost/v1/chat/completions",
                             "http://user:secret@127.0.0.1:12/chat/completions", "http://127.0.0.1:12/chat/completions?q=secret"]:
                args.endpoint = endpoint
                with self.assertRaises(ValueError):
                    halo.send(args)
            opener.assert_not_called()
        self.assertFalse(args.output.exists())

    def test_one_send_captures_exact_bytes_and_http_error_body_without_retry(self):
        args = SimpleNamespace(allow_inference=True, endpoint="http://127.0.0.1:12345/v1/chat/completions",
                               request=self.bundle, request_sha256=self.request_hash, output=self.root / "sent")
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"choices":[]}'
        opener = Mock()
        opener.open.return_value = response
        with patch.object(halo, "build_opener", return_value=opener) as build:
            halo.send(args)
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(opener.open.call_args.args[0].data, (self.bundle / "request.json").read_bytes())
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], halo.NoRedirect)
        args.output = self.root / "http-error"
        error = HTTPError(args.endpoint, 500, "synthetic error", {}, io.BytesIO(b'{"error":"synthetic"}'))
        with patch.object(halo, "build_opener") as build:
            build.return_value.open.side_effect = error
            with self.assertRaises(HTTPError):
                halo.send(args)
            self.assertEqual(build.return_value.open.call_count, 1)
        self.assertEqual((args.output / "response.json").read_bytes(), b'{"error":"synthetic"}')
        self.assertFalse(review.read_json(args.output / "receipt.json")["ok"])

    def test_redirect_is_never_followed_and_oversized_response_retains_only_bounded_prefix(self):
        with self.assertRaisesRegex(ValueError, "redirects"):
            halo.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "http://example.com")
        args = SimpleNamespace(allow_inference=True, endpoint="http://127.0.0.1:12345/v1/chat/completions",
                               request=self.bundle, request_sha256=self.request_hash, output=self.root / "oversized")
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"x" * (halo.RESPONSE_LIMIT + 1)
        with patch.object(halo, "build_opener") as build:
            build.return_value.open.return_value = response
            with self.assertRaisesRegex(ValueError, "response exceeds"):
                halo.send(args)
        self.assertEqual((args.output / "response.json").stat().st_size, halo.RESPONSE_LIMIT)

    def test_loopback_http_round_trip_sends_only_reviewed_request_and_validates_exact_return(self):
        calls = []
        reply = demo.canonical({"choices": [{"finish_reason": "stop", "message": {
            "content": demo.canonical(self.report).decode()}}]})
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append((self.path, self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            sending = SimpleNamespace(allow_inference=True,
                endpoint=f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                request=self.bundle, request_sha256=self.request_hash, output=self.root / "http-trial")
            halo.send(sending)
            args = SimpleNamespace(request=self.bundle, request_sha256=self.request_hash,
                response=sending.output / "response.json", output=self.root / "http-validated")
            halo.validate(args)
            self.assertEqual(calls, [("/v1/chat/completions", (self.bundle / "request.json").read_bytes())])
            self.assertEqual(review.read_json(args.output / "report.json"), self.report)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
