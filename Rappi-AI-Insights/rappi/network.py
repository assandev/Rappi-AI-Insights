from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from rappi.io_utils import iso_now, truncate
from rappi.models import NetworkEndpointHit


KEYWORDS = ["cart", "checkout", "summary", "fee", "pricing", "order"]


class NetworkCollector:
    def __init__(
        self,
        include_body: bool,
        body_max_chars: int,
        log_writer: Callable[[dict[str, Any]], None] | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.include_body = include_body
        self.body_max_chars = body_max_chars
        self.log_writer = log_writer
        self.logger = logger

        self.current_step = 0
        self.priority_window = False
        self.matched_requests = 0
        self.endpoint_map: dict[tuple[str, str], NetworkEndpointHit] = {}
        self.payloads: dict[str, Any] = {}

    def set_step(self, step: int) -> None:
        self.current_step = step

    def set_priority_window(self, value: bool) -> None:
        self.priority_window = value

    async def on_response(self, resp: Any) -> None:
        req = resp.request
        url_low = resp.url.lower()

        # Capture known pricing payloads regardless of keyword scoring.
        try:
            if "shopping-cart/v1/all/get" in url_low and resp.status == 200:
                self.payloads["all_get"] = await resp.json()
            elif "shopping-cart/v1/restaurant/summary-v2" in url_low and resp.status == 200:
                self.payloads["summary_v2"] = await resp.json()
            elif "shopping-cart/v1/restaurant/checkout/detail" in url_low and resp.status == 200:
                self.payloads["checkout_detail"] = await resp.json()
            elif "shopping-cart/v2/restaurant/store" in url_low and resp.status == 200:
                self.payloads["store_v2"] = await resp.json()
        except Exception:  # noqa: BLE001
            pass

        if req.resource_type not in {"xhr", "fetch"}:
            return

        haystack = f"{req.method} {resp.url} {req.post_data or ''}"
        if not _contains_keyword(haystack):
            return

        self.matched_requests += 1
        score = _keyword_score(haystack)
        now_ts = datetime.now(timezone.utc).timestamp()
        key = (req.method, resp.url)

        if key in self.endpoint_map:
            current = self.endpoint_map[key]
            current.hits += 1
            current.last_seen = now_ts
            current.score = max(current.score, score)
        else:
            self.endpoint_map[key] = NetworkEndpointHit(
                url=resp.url,
                method=req.method,
                status=resp.status,
                score=score,
                last_seen=now_ts,
            )

        if self.log_writer:
            self.log_writer(
                {
                    "ts": iso_now(),
                    "step": self.current_step,
                    "priority_window": self.priority_window,
                    "url": resp.url,
                    "method": req.method,
                    "status": resp.status,
                    "resource_type": req.resource_type,
                    "score": score,
                    "request_body_preview": truncate(req.post_data or "", 600) or None,
                    "response_body_preview": await _body_preview(
                        resp=resp,
                        include_body=self.include_body,
                        max_chars=self.body_max_chars,
                    ),
                }
            )

        if self.logger:
            self.logger(f"[MATCH] {req.method} {resp.status} {resp.url} (score={score})")

    def top_endpoints(self, limit: int = 12) -> list[NetworkEndpointHit]:
        sorted_candidates = sorted(
            self.endpoint_map.values(),
            key=lambda item: (item.score, item.last_seen),
            reverse=True,
        )
        return sorted_candidates[:limit]


def _contains_keyword(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in KEYWORDS)


def _keyword_score(text: str) -> int:
    low = text.lower()
    return sum(1 for k in KEYWORDS if k in low)


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
