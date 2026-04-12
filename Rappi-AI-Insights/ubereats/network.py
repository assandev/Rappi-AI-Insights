from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from rappi.io_utils import iso_now, truncate
from rappi.models import NetworkEndpointHit


KEYWORDS = [
    "cart",
    "basket",
    "checkout",
    "summary",
    "fee",
    "fare",
    "pricing",
    "order",
    "receipt",
    "quote",
    "delivery",
    "eta",
    "payment",
    "subtotal",
    "total",
]


class NetworkCollector:
    def __init__(
        self,
        include_body: bool,
        body_max_chars: int,
        debug_capture_bodies: bool = False,
        debug_max_bodies: int = 120,
        log_writer: Callable[[dict[str, Any]], None] | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.include_body = include_body
        self.body_max_chars = body_max_chars
        self.debug_capture_bodies = debug_capture_bodies
        self.debug_max_bodies = debug_max_bodies
        self.log_writer = log_writer
        self.logger = logger

        self.current_step = 0
        self.priority_window = False
        self.matched_requests = 0
        self.endpoint_map: dict[tuple[str, str], NetworkEndpointHit] = {}
        self.payloads: dict[str, Any] = {}
        self.payload_steps: dict[str, int] = {}
        self.checkout_candidates: list[dict[str, Any]] = []
        self.checkout_candidate_bodies: list[dict[str, Any]] = []

    def set_step(self, step: int) -> None:
        self.current_step = step

    def set_priority_window(self, value: bool) -> None:
        self.priority_window = value

    def reset_payloads(self) -> None:
        self.payloads = {}
        self.payload_steps = {}
        self.checkout_candidates = []
        self.checkout_candidate_bodies = []

    async def on_response(self, resp: Any) -> None:
        req = resp.request
        url = resp.url
        url_low = url.lower()

        if req.resource_type not in {"xhr", "fetch"}:
            return

        haystack = f"{req.method} {url} {req.post_data or ''}"
        if not _contains_keyword(haystack):
            return

        self.matched_requests += 1
        score = _keyword_score(haystack)
        now_ts = datetime.now(timezone.utc).timestamp()
        key = (req.method, url)

        if key in self.endpoint_map:
            endpoint = self.endpoint_map[key]
            endpoint.hits += 1
            endpoint.last_seen = now_ts
            endpoint.score = max(endpoint.score, score)
        else:
            self.endpoint_map[key] = NetworkEndpointHit(
                url=url,
                method=req.method,
                status=resp.status,
                score=score,
                last_seen=now_ts,
            )

        # Uber payload buckets (prefer concrete API endpoints over generic keyword buckets).
        if resp.status == 200:
            try:
                payload = await resp.json()
                # Strong buckets by endpoint
                if "/_p/api/getcheckoutpresentationv1" in url_low:
                    self.payloads["checkout_presentation"] = payload
                    self.payload_steps["checkout_presentation"] = self.current_step
                if "/_p/api/getdraftorderbyuuidv1" in url_low or "/_p/api/getdraftorderbyuuidv2" in url_low:
                    self.payloads["draft_order"] = payload
                    self.payload_steps["draft_order"] = self.current_step
                if "/_p/api/getdraftordersbyeateruuidv1" in url_low:
                    self.payloads["draft_orders"] = payload
                    self.payload_steps["draft_orders"] = self.current_step
                if "/_p/api/getcartsviewforeateruuidv1" in url_low:
                    self.payloads["carts_view"] = payload
                    self.payload_steps["carts_view"] = self.current_step

                # Generic buckets (kept for resilience)
                if "cart" in url_low:
                    self.payloads["cart"] = payload
                    self.payload_steps["cart"] = self.current_step
                if "checkout" in url_low:
                    self.payloads["checkout"] = payload
                    self.payload_steps["checkout"] = self.current_step
                if "summary" in url_low:
                    self.payloads["summary"] = payload
                    self.payload_steps["summary"] = self.current_step
                if "pricing" in url_low or "fee" in url_low:
                    self.payloads["pricing"] = payload
                    self.payload_steps["pricing"] = self.current_step
                if "order" in url_low:
                    self.payloads["order"] = payload
                    self.payload_steps["order"] = self.current_step
            except Exception:  # noqa: BLE001
                pass

        is_checkout_candidate = self._is_checkout_candidate(url_low=url_low, score=score)
        response_preview = await _body_preview(
            resp=resp,
            include_body=self.include_body or is_checkout_candidate,
            max_chars=self.body_max_chars,
        )
        reason = _candidate_reason(url_low=url_low, haystack=haystack)
        log_record = {
            "ts": iso_now(),
            "step": self.current_step,
            "priority_window": self.priority_window,
            "url": url,
            "method": req.method,
            "status": resp.status,
            "resource_type": req.resource_type,
            "score": score,
            "reason": reason,
            "request_body_preview": truncate(req.post_data or "", 600) or None,
            "response_body_preview": response_preview,
        }
        if self.log_writer:
            self.log_writer(log_record)
        if is_checkout_candidate:
            self.checkout_candidates.append(log_record)
            if self.debug_capture_bodies and len(self.checkout_candidate_bodies) < self.debug_max_bodies:
                full_body, body_format = await _body_full(resp=resp)
                self.checkout_candidate_bodies.append(
                    {
                        "ts": iso_now(),
                        "step": self.current_step,
                        "url": url,
                        "method": req.method,
                        "status": resp.status,
                        "content_type": (resp.header_value("content-type") or ""),
                        "reason": reason,
                        "body_format": body_format,
                        "body": full_body,
                    }
                )

        if self.logger:
            self.logger(f"[MATCH] {req.method} {resp.status} {url} (score={score})")

    def top_endpoints(self, limit: int = 12) -> list[NetworkEndpointHit]:
        candidates = sorted(self.endpoint_map.values(), key=lambda x: (x.score, x.last_seen), reverse=True)
        return candidates[:limit]

    def checkout_candidate_summary(self, limit: int = 20) -> list[dict[str, Any]]:
        ranked = sorted(
            self.checkout_candidates,
            key=lambda x: (x.get("score", 0), x.get("ts", "")),
            reverse=True,
        )
        return ranked[:limit]

    def _is_checkout_candidate(self, url_low: str, score: int) -> bool:
        if self.current_step < 9:
            return False
        if "/_p/api/" in url_low:
            if any(
                token in url_low
                for token in (
                    "checkout",
                    "draftorder",
                    "cart",
                    "summary",
                    "order",
                    "receipt",
                    "payment",
                    "quote",
                    "delivery",
                )
            ):
                return True
        return score >= 3


def _contains_keyword(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in KEYWORDS)


def _keyword_score(text: str) -> int:
    low = text.lower()
    return sum(1 for k in KEYWORDS if k in low)


def _candidate_reason(url_low: str, haystack: str) -> str:
    matched = [k for k in KEYWORDS if k in haystack.lower()]
    parts: list[str] = []
    if "/_p/api/" in url_low:
        parts.append("uber_api")
    if matched:
        parts.append("keywords=" + ",".join(sorted(set(matched))))
    return ";".join(parts) if parts else "candidate"


async def _body_preview(resp: Any, include_body: bool, max_chars: int) -> str | None:
    if not include_body:
        return None
    try:
        content_type = (resp.header_value("content-type") or "").lower()
        if "application/json" in content_type:
            body = json.dumps(await resp.json(), ensure_ascii=False)
        else:
            body = await resp.text()
        return truncate(body, max_chars)
    except Exception:  # noqa: BLE001
        return None


async def _body_full(resp: Any) -> tuple[str | None, str]:
    try:
        content_type = (resp.header_value("content-type") or "").lower()
        if "application/json" in content_type:
            return json.dumps(await resp.json(), ensure_ascii=False, indent=2), "json"
        return await resp.text(), "txt"
    except Exception:  # noqa: BLE001
        return None, "txt"
