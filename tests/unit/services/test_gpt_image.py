import asyncio
import base64
import json
import unittest
from email import policy
from email.parser import BytesParser
from io import BytesIO
from unittest.mock import AsyncMock, call, patch

import httpx
from openai import AsyncOpenAI
from PIL import Image

from aiqq.exceptions import ImageGenerationUnavailable
from aiqq.logic.models import ImageAction, ImageAsset
from aiqq.services.images.gpt import (
    GPTImageError,
    GPTImageService,
    MAX_ERROR_BODY_BYTES,
    SAFE_IMAGE_INSTRUCTION,
)


LOGGER = "aiqq.services.images.gpt"
API_KEY = "sk-test-image-private-key"
PROMPT = "a private red square prompt"


def encoded_png(size: tuple[int, int] = (20, 10)) -> str:
    output = BytesIO()
    Image.new("RGBA", size, (255, 0, 0, 128)).save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def image_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={"created": 1, "data": [{"b64_json": encoded_png()}]},
        headers={"x-request-id": "req-image-success"},
    )


def log_fields(record) -> dict[str, object]:
    return json.loads(record.getMessage().split(" ", 1)[1])


def multipart_parts(request: httpx.Request):
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
        + request.content
    )
    return {
        part.get_param("name", header="content-disposition"): part
        for part in message.iter_parts()
    }


class GPTImageServiceTests(unittest.IsolatedAsyncioTestCase):
    def service(self, handler, *, base_url="https://images.example/v1"):
        """Use the production constructor and a real SDK with only HTTP replaced."""
        requests = []

        async def record(request):
            await request.aread()
            requests.append(request)
            result = handler(request)
            return await result if asyncio.iscoroutine(result) else result

        def sdk_client(**kwargs):
            return AsyncOpenAI(
                **kwargs,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(record)),
            )

        with patch(f"{LOGGER}.AsyncOpenAI", side_effect=sdk_client):
            service = GPTImageService(api_key=API_KEY, base_url=base_url)
        self.addAsyncCleanup(service.close)
        return service, requests

    async def run_operation(self, service, operation="generate"):
        reference = None
        if operation == "edit":
            reference = ImageAsset(base64.b64decode(encoded_png()), "image/png", 20, 10)
        return await service.generate(
            ImageAction(operation, PROMPT, source_record_id=7 if operation == "edit" else None),
            reference_image=reference,
        )

    async def test_generation_json_contract_and_jpeg_normalization(self):
        service, requests = self.service(lambda _: image_response())
        with self.assertLogs(LOGGER, level="INFO") as captured:
            asset = await self.run_operation(service)

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].method, "POST")
        self.assertEqual(requests[0].url.path, "/v1/images/generations")
        self.assertEqual(json.loads(requests[0].content), {
            "model": "gpt-image-2", "prompt": SAFE_IMAGE_INSTRUCTION + PROMPT,
            "n": 1, "size": "1024x1024", "quality": "medium",
            "output_format": "jpeg", "output_compression": 85, "moderation": "auto",
        })
        self.assertEqual(asset.mime_type, "image/jpeg")
        self.assertEqual((asset.width, asset.height), (1024, 1024))
        with Image.open(BytesIO(asset.data)) as decoded:
            self.assertEqual(decoded.format, "JPEG")
            self.assertEqual(decoded.mode, "RGB")
            self.assertEqual(decoded.size, (1024, 1024))
            self.assertEqual(decoded.getpixel((0, 0)), (255, 255, 255))
            self.assertGreater(decoded.getpixel((512, 512))[0], 240)
        started, finished = map(log_fields, captured.records)
        self.assertEqual(started["attempt_id"], finished["attempt_id"])
        self.assertEqual(finished["outcome"], "succeeded")
        self.assertEqual(finished["operation"], "generate")
        self.assertEqual(finished["status"], 200)
        self.assertEqual(finished["provider_request_id"], "req-image-success")
        self.assertGreaterEqual(finished["elapsed_ms"], 0)
        self.assertNotIn(PROMPT, " ".join(captured.output))

    async def test_edit_multipart_contract_includes_reference_and_fidelity(self):
        service, requests = self.service(lambda _: image_response())
        with self.assertLogs(LOGGER, level="INFO") as captured:
            await self.run_operation(service, "edit")
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url.path, "/v1/images/edits")
        parts = multipart_parts(request)
        self.assertNotIn("response_format", parts)
        expected = {
            "model": "gpt-image-2", "prompt": SAFE_IMAGE_INSTRUCTION + PROMPT,
            "n": "1", "size": "1024x1024", "quality": "medium",
            "output_format": "jpeg", "output_compression": "85",
            "input_fidelity": "high",
        }
        self.assertEqual(set(parts), set(expected) | {"image"})
        for name, value in expected.items():
            self.assertEqual(parts[name].get_payload(decode=True).decode(), value)
        self.assertEqual(parts["image"].get_filename(), "reference.png")
        self.assertEqual(parts["image"].get_content_type(), "image/png")
        self.assertEqual(parts["image"].get_payload(decode=True), base64.b64decode(encoded_png()))
        self.assertEqual(log_fields(captured.records[-1])["operation"], "edit")

    async def test_missing_reference_does_not_post(self):
        service, requests = self.service(lambda _: image_response())
        with self.assertLogs(LOGGER, level="INFO") as captured:
            with self.assertRaises(GPTImageError) as raised:
                await service.generate(ImageAction("edit", PROMPT, source_record_id=7))
        self.assertEqual(raised.exception.kind, "invalid_request")
        self.assertEqual(log_fields(captured.records[-1])["outcome"], "failed")
        self.assertEqual(requests, [])

    async def test_http_errors_are_classified_without_retry_for_both_operations(self):
        for operation in ("generate", "edit"):
            for status, kind in (
                (400, "invalid_request"), (401, "authentication"),
                (403, "authentication"), (404, "not_found"), (408, "unknown"),
                (409, "unknown"), (422, "invalid_request"), (429, "rate_limited"),
                (500, "upstream"), (503, "upstream"),
            ):
                with self.subTest(operation=operation, status=status):
                    service, requests = self.service(lambda _: httpx.Response(
                        status, json={"error": {"message": "Service unavailable"}},
                        headers={"x-request-id": "req-error"},
                    ))
                    with self.assertLogs(LOGGER, level="INFO") as captured:
                        with self.assertRaises(ImageGenerationUnavailable) as raised:
                            await self.run_operation(service, operation)
                    error = raised.exception
                    self.assertIsInstance(error, GPTImageError)
                    self.assertEqual(error.kind, kind)
                    self.assertEqual(error.status_code, status)
                    self.assertEqual(error.provider_request_id, "req-error")
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(requests[0].method, "POST")
                    self.assertEqual(len(captured.records), 2)
                    finished = log_fields(captured.records[-1])
                    self.assertEqual(finished["attempt_id"], error.attempt_id)
                    self.assertEqual(finished["error_kind"], kind)
                    self.assertEqual(finished["outcome"], "failed")

    async def test_bad_gateway_retries_replay_generate_and_edit_until_success(self):
        for operation in ("generate", "edit"):
            for retry_count in (1, 2):
                with self.subTest(operation=operation, retry_count=retry_count):
                    responses = iter([
                        httpx.Response(
                            502, json={"error": {"message": "Service unavailable"}},
                            headers={"x-request-id": f"req-gateway-{index + 1}"},
                        )
                        for index in range(retry_count)
                    ] + [image_response()])
                    service, requests = self.service(lambda _: next(responses))
                    with patch(f"{LOGGER}.asyncio.sleep", new_callable=AsyncMock) as sleep:
                        with self.assertLogs(LOGGER, level="INFO") as captured:
                            asset = await self.run_operation(service, operation)

                    self.assertEqual(len(requests), retry_count + 1)
                    self.assertEqual(sleep.await_args_list, [call(0.5), call(1.0)][:retry_count])
                    self.assertEqual(asset.mime_type, "image/jpeg")
                    self.assertEqual((asset.width, asset.height), (1024, 1024))
                    for request in requests:
                        self.assertEqual(request.method, "POST")
                        self.assertEqual(request.url, requests[0].url)
                        if operation == "generate":
                            self.assertEqual(request.content, requests[0].content)
                            self.assertEqual(
                                json.loads(request.content)["prompt"], SAFE_IMAGE_INSTRUCTION + PROMPT
                            )
                        else:
                            parts = multipart_parts(request)
                            original_parts = multipart_parts(requests[0])
                            self.assertEqual(set(parts), set(original_parts))
                            for name, part in parts.items():
                                self.assertEqual(
                                    part.get_payload(decode=True),
                                    original_parts[name].get_payload(decode=True),
                                )
                                self.assertEqual(part.get_filename(), original_parts[name].get_filename())
                                self.assertEqual(part.get_content_type(), original_parts[name].get_content_type())
                            self.assertEqual(
                                parts["image"].get_payload(decode=True), base64.b64decode(encoded_png())
                            )

                    events = [record.getMessage().split(" ", 1)[0] for record in captured.records]
                    self.assertEqual(events, ["event=gpt_image_started"] + ["event=gpt_image_retry"] * retry_count + ["event=gpt_image_finished"])
                    fields = list(map(log_fields, captured.records))
                    self.assertEqual(len({item["attempt_id"] for item in fields}), 1)
                    for retry_number, retry in enumerate(fields[1:-1], start=1):
                        self.assertEqual(retry["retry_number"], retry_number)
                        self.assertEqual(retry["delay_seconds"], (0.5, 1.0)[retry_number - 1])
                        self.assertEqual(retry["status"], 502)
                        self.assertEqual(retry["provider_request_id"], f"req-gateway-{retry_number}")
                        self.assertEqual(retry["provider_message"], "Service unavailable")
                        self.assertEqual(retry["operation"], operation)
                    self.assertEqual(fields[-1]["outcome"], "succeeded")
                    self.assertEqual(fields[-1]["status"], 200)
                    self.assertEqual(fields[-1]["provider_request_id"], "req-image-success")
                    self.assertNotIn("provider_message", fields[-1])

    async def test_continuous_bad_gateway_stops_after_three_posts(self):
        for operation in ("generate", "edit"):
            with self.subTest(operation=operation):
                def fail(_):
                    return httpx.Response(
                        502, json={"error": {"message": "Service unavailable"}},
                        headers={"x-request-id": f"req-gateway-{len(requests)}"},
                    )

                service, requests = self.service(fail)
                with patch(f"{LOGGER}.asyncio.sleep", new_callable=AsyncMock) as sleep:
                    with self.assertLogs(LOGGER, level="INFO") as captured:
                        with self.assertRaises(GPTImageError) as raised:
                            await self.run_operation(service, operation)
                self.assertEqual(len(requests), 3)
                self.assertEqual(sleep.await_args_list, [call(0.5), call(1.0)])
                self.assertEqual(raised.exception.kind, "upstream")
                self.assertEqual(raised.exception.status_code, 502)
                self.assertEqual(raised.exception.provider_request_id, "req-gateway-3")
                self.assertEqual(len(captured.records), 4)
                finished = log_fields(captured.records[-1])
                self.assertEqual(finished["outcome"], "failed")
                self.assertEqual(finished["provider_request_id"], "req-gateway-3")
                self.assertEqual(finished["attempt_id"], raised.exception.attempt_id)

    async def test_bad_gateway_followed_by_other_http_error_stops_immediately(self):
        for operation in ("generate", "edit"):
            for status, kind in (
                (400, "invalid_request"), (401, "authentication"),
                (403, "authentication"), (404, "not_found"), (408, "unknown"),
                (409, "unknown"), (422, "invalid_request"), (429, "rate_limited"),
                (500, "upstream"), (503, "upstream"),
            ):
                with self.subTest(operation=operation, status=status):
                    responses = iter([
                        httpx.Response(502, json={"error": {"message": "Service unavailable"}},
                                       headers={"x-request-id": "req-gateway"}),
                        httpx.Response(status, json={"error": {"type": "internal_error"}},
                                       headers={"x-request-id": "req-final-error"}),
                    ])
                    service, requests = self.service(lambda _: next(responses))
                    with patch(f"{LOGGER}.asyncio.sleep", new_callable=AsyncMock) as sleep:
                        with self.assertLogs(LOGGER, level="INFO") as captured:
                            with self.assertRaises(GPTImageError) as raised:
                                await self.run_operation(service, operation)
                    self.assertEqual(len(requests), 2)
                    sleep.assert_awaited_once_with(0.5)
                    self.assertEqual(raised.exception.kind, kind)
                    self.assertEqual(raised.exception.status_code, status)
                    self.assertEqual(raised.exception.provider_request_id, "req-final-error")
                    self.assertEqual(len(captured.records), 3)
                    finished = log_fields(captured.records[-1])
                    self.assertEqual(finished["status"], status)
                    self.assertEqual(finished["error_kind"], kind)
                    self.assertEqual(finished["provider_type"], "internal_error")
                    self.assertNotIn("provider_message", finished)

    async def test_bad_gateway_then_network_failure_has_no_stale_http_metadata(self):
        for operation in ("generate", "edit"):
            for error_type, kind in (
                (httpx.ReadTimeout, "timeout"), (httpx.ConnectTimeout, "timeout"),
                (httpx.ConnectError, "connection"), (httpx.ReadError, "connection"),
            ):
                with self.subTest(operation=operation, error_type=error_type):
                    def fail(request):
                        if len(requests) == 1:
                            return httpx.Response(
                                502, json={"error": {"message": "Service unavailable"}},
                                headers={"x-request-id": "req-gateway"},
                            )
                        raise error_type(f"{API_KEY} {PROMPT}", request=request)

                    service, requests = self.service(fail)
                    with patch(f"{LOGGER}.asyncio.sleep", new_callable=AsyncMock) as sleep:
                        with self.assertLogs(LOGGER, level="INFO") as captured:
                            with self.assertRaises(GPTImageError) as raised:
                                await self.run_operation(service, operation)
                    self.assertEqual(len(requests), 2)
                    sleep.assert_awaited_once_with(0.5)
                    self.assertEqual(raised.exception.kind, kind)
                    self.assertIsNone(raised.exception.status_code)
                    self.assertIsNone(raised.exception.provider_request_id)
                    finished = log_fields(captured.records[-1])
                    self.assertIsNone(finished["status"])
                    self.assertIsNone(finished["provider_request_id"])
                    self.assertEqual(finished["error_kind"], kind)
                    self.assertNotIn("provider_message", finished)
                    self.assertNotIn(API_KEY, " ".join(captured.output))
                    self.assertNotIn(PROMPT, " ".join(captured.output))

    async def test_bad_gateway_retry_diagnostics_redact_untrusted_details(self):
        private_url = "https://user:password@example.com/x?key=xyz"
        responses = iter([
            httpx.Response(502, json={"error": {
                "message": f"{API_KEY} {PROMPT} {encoded_png()}",
                "type": API_KEY, "code": private_url, "param": PROMPT,
            }}, headers={"x-request-id": API_KEY}),
            httpx.Response(502, text=f"<html>{PROMPT} {API_KEY}</html>", headers={
                "content-type": "text/html", "x-request-id": private_url,
            }),
            image_response(),
        ])
        service, requests = self.service(lambda _: next(responses))
        with patch(f"{LOGGER}.asyncio.sleep", new_callable=AsyncMock):
            with self.assertLogs(LOGGER, level="INFO") as captured:
                await self.run_operation(service)
        self.assertEqual(len(requests), 3)
        logs = " ".join(captured.output)
        for value in (API_KEY, PROMPT, private_url, encoded_png(), "<html>"):
            self.assertNotIn(value, logs)
        retry_fields = list(map(log_fields, captured.records[1:-1]))
        self.assertEqual(retry_fields[0]["provider_message"], "detail_omitted")
        self.assertEqual(retry_fields[1]["detail_omitted"], "non_json_body")
        for record, fields in zip(captured.records[1:-1], retry_fields):
            self.assertEqual(fields["provider_request_id"], "detail_omitted")
            self.assertLess(len(record.getMessage()), 1000)
            self.assertNotIn("\n", record.getMessage())

    async def test_cancellation_during_bad_gateway_backoff_stops_further_posts(self):
        for operation in ("generate", "edit"):
            with self.subTest(operation=operation):
                entered = asyncio.Event()

                async def pending_backoff(_):
                    entered.set()
                    await asyncio.Event().wait()

                service, requests = self.service(lambda _: httpx.Response(
                    502, json={"error": {"message": "Service unavailable"}},
                    headers={"x-request-id": "req-gateway"},
                ))
                with patch(f"{LOGGER}.asyncio.sleep", side_effect=pending_backoff) as sleep:
                    with self.assertLogs(LOGGER, level="INFO") as captured:
                        task = asyncio.create_task(self.run_operation(service, operation))
                        await asyncio.wait_for(entered.wait(), timeout=2)
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                self.assertEqual(len(requests), 1)
                sleep.assert_awaited_once_with(0.5)
                self.assertEqual(len(captured.records), 3)
                fields = list(map(log_fields, captured.records))
                self.assertEqual(len({item["attempt_id"] for item in fields}), 1)
                self.assertEqual(fields[-1]["outcome"], "cancelled")
                self.assertEqual(fields[-1]["error_kind"], "cancelled")
                self.assertEqual(fields[-1]["operation"], operation)

    async def test_network_failures_are_classified_without_retry(self):
        for operation in ("generate", "edit"):
            for error_type, kind in (
                (httpx.ReadTimeout, "timeout"), (httpx.ConnectTimeout, "timeout"),
                (httpx.ConnectError, "connection"), (httpx.ReadError, "connection"),
            ):
                with self.subTest(operation=operation, error_type=error_type):
                    def fail(request):
                        raise error_type(f"{API_KEY} {PROMPT}", request=request)
                    service, requests = self.service(fail)
                    with self.assertLogs(LOGGER, level="INFO") as captured:
                        with self.assertRaises(GPTImageError) as raised:
                            await self.run_operation(service, operation)
                    self.assertEqual(raised.exception.kind, kind)
                    self.assertIsNone(raised.exception.status_code)
                    self.assertIsNone(raised.exception.provider_request_id)
                    self.assertEqual(len(requests), 1)
                    logs = " ".join(captured.output)
                    self.assertNotIn(API_KEY, logs)
                    self.assertNotIn(PROMPT, logs)

    async def test_current_model_group_error_and_top_level_error_are_diagnosable(self):
        body = {
            "message": 'Model "gpt-image-2" is not supported by any configured account in this group',
            "type": "model_not_found", "code": "model_not_found", "param": "model",
            "prompt": PROMPT, "image": encoded_png(),
        }
        for payload in (body, {"error": body}):
            with self.subTest(nested="error" in payload):
                service, requests = self.service(lambda _: httpx.Response(
                    404, json=payload,
                    headers={"request-id": "e5697d63-a021-4054-8811-f311d07c76ff"},
                ))
                with self.assertLogs(LOGGER, level="INFO") as captured:
                    with self.assertRaises(GPTImageError) as raised:
                        await self.run_operation(service)
                finished = log_fields(captured.records[-1])
                self.assertEqual(finished["provider_type"], "model_not_found")
                self.assertEqual(finished["provider_code"], "model_not_found")
                self.assertEqual(finished["provider_param"], "model")
                self.assertIn("not supported by any configured account in this group", finished["provider_message"])
                self.assertEqual(raised.exception.provider_request_id, "e5697d63-a021-4054-8811-f311d07c76ff")
                self.assertEqual(len(requests), 1)
                self.assertNotIn(PROMPT, " ".join(captured.output))
                self.assertNotIn(encoded_png(), " ".join(captured.output))

    async def test_untrusted_fields_are_omitted_and_logs_are_bounded(self):
        values = (
            API_KEY, PROMPT, "Bearer unrelated-credential", "api_key=another-key",
            "access_token=another-token", "https://user:password@example.com/x?key=xyz",
            encoded_png(), "x" * 2000, "\r\nInjected log event",
        )
        for value in values:
            with self.subTest(value=value[:20]):
                service, _ = self.service(lambda _: httpx.Response(
                    404,
                    json={"error": {field: value for field in ("message", "type", "code", "param")}},
                    headers={"x-request-id": value},
                ))
                with self.assertLogs(LOGGER, level="INFO") as captured:
                    with self.assertRaises(GPTImageError):
                        await self.run_operation(service)
                logs = " ".join(captured.output)
                self.assertNotIn(value, logs)
                self.assertLess(len(captured.records[-1].getMessage()), 1000)
                self.assertNotIn("\n", captured.records[-1].getMessage())
                self.assertIn("detail_omitted", logs)

    async def test_unknown_prose_and_non_string_fields_are_not_logged(self):
        service, _ = self.service(lambda _: httpx.Response(404, json={"error": {
            "message": "Upstream echoed only part of a private red square prompt",
            "type": {"nested": API_KEY}, "code": [PROMPT], "param": None,
        }}))
        with self.assertLogs(LOGGER, level="INFO") as captured:
            with self.assertRaises(GPTImageError) as raised:
                await self.run_operation(service)
        finished = log_fields(captured.records[-1])
        self.assertEqual(finished["provider_message"], "detail_omitted")
        self.assertNotIn("provider_type", finished)
        self.assertIsNone(raised.exception.provider_request_id)
        self.assertNotIn("private red square", " ".join(captured.output))

    async def test_endpoint_diagnostics_strip_url_credentials_and_query(self):
        service, _ = self.service(
            lambda _: httpx.Response(404, json={"error": {"type": "model_not_found"}}),
            base_url="https://user:password@images.example/v1?api_key=url-credential#fragment",
        )
        with self.assertLogs(LOGGER, level="INFO") as captured:
            with self.assertRaises(GPTImageError):
                await self.run_operation(service)
        self.assertEqual(log_fields(captured.records[-1])["endpoint"], "https://images.example/v1/images/generations")
        logs = " ".join(captured.output)
        for value in ("user", "password", "url-credential", "fragment", API_KEY):
            self.assertNotIn(value, logs)

    async def test_unusable_error_bodies_do_not_expose_raw_content(self):
        cases = (
            ("text/html", f"<html>{API_KEY} {PROMPT}</html>", "non_json_body"),
            ("text/plain", f"{API_KEY} {PROMPT}", "non_json_body"),
            ("application/json", "not-json " + API_KEY, "invalid_json"),
            ("application/json", json.dumps({"error": {"message": API_KEY + "x" * MAX_ERROR_BODY_BYTES}}), "oversize_body"),
            ("application/json", json.dumps([API_KEY]), "invalid_error_object"),
            ("application/json", json.dumps({"error": API_KEY}), "invalid_error_object"),
            ("application/json", "{}", "no_safe_error_fields"),
        )
        for content_type, body, reason in cases:
            with self.subTest(reason=reason):
                service, requests = self.service(lambda _: httpx.Response(
                    404, content=body, headers={"content-type": content_type},
                ))
                with self.assertLogs(LOGGER, level="INFO") as captured:
                    with self.assertRaises(GPTImageError) as raised:
                        await self.run_operation(service)
                finished = log_fields(captured.records[-1])
                self.assertEqual(finished["detail_omitted"], reason)
                self.assertEqual(finished["body_bytes"], len(body.encode()))
                self.assertEqual(raised.exception.kind, "not_found")
                self.assertEqual(len(requests), 1)
                self.assertNotIn(API_KEY, " ".join(captured.output))
                self.assertNotIn(PROMPT, " ".join(captured.output))

    async def test_malformed_successes_are_invalid_response_with_terminal_metadata(self):
        responses = [
            httpx.Response(200, json={"data": data}, headers={"x-request-id": "req-bad-image"})
            for data in (None, [], {}, [None], [{"url": "https://example.com/private?key=xyz"}],
                         [{"b64_json": "%%%invalid"}], [{"b64_json": 123}],
                         [{"b64_json": base64.b64encode(b"not an image").decode()}])
        ]
        responses.extend((
            httpx.Response(200, text=f"<html>{API_KEY}</html>", headers={"content-type": "text/html"}),
            httpx.Response(200, content=f"{{{API_KEY}", headers={"content-type": "application/json"}),
        ))
        for response in responses:
            with self.subTest(body=response.content[:50]):
                service, requests = self.service(lambda _: response)
                with self.assertLogs(LOGGER, level="INFO") as captured:
                    with self.assertRaises(GPTImageError) as raised:
                        await self.run_operation(service)
                self.assertEqual(raised.exception.kind, "invalid_response")
                self.assertEqual(raised.exception.status_code, 200)
                finished = log_fields(captured.records[-1])
                self.assertEqual(finished["outcome"], "failed")
                self.assertEqual(finished["error_kind"], "invalid_response")
                self.assertEqual(finished["provider_request_id"], raised.exception.provider_request_id)
                self.assertEqual(len(requests), 1)
                self.assertNotIn(API_KEY, " ".join(captured.output))

    async def test_cancellation_propagates_with_one_terminal_log(self):
        for operation in ("generate", "edit"):
            with self.subTest(operation=operation):
                entered = asyncio.Event()

                async def pending(_):
                    entered.set()
                    await asyncio.Event().wait()

                service, requests = self.service(pending)
                with self.assertLogs(LOGGER, level="INFO") as captured:
                    task = asyncio.create_task(self.run_operation(service, operation))
                    await asyncio.wait_for(entered.wait(), timeout=2)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                self.assertEqual(len(requests), 1)
                self.assertEqual(len(captured.records), 2)
                finished = log_fields(captured.records[-1])
                self.assertEqual(finished["outcome"], "cancelled")
                self.assertEqual(finished["operation"], operation)
                self.assertEqual(finished["error_kind"], "cancelled")


if __name__ == "__main__":
    unittest.main()
