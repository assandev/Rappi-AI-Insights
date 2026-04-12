from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def slugify_product(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "product"


@dataclass(slots=True)
class CheckoutJob:
    platform: str
    address_id: int
    address_text: str
    zone_type: str
    restaurant: str
    product: str
    storage_state: Path
    screenshot_dir: Path
    network_log_file: Path
    result_file: Path
    timeout_ms: int
    include_body: bool
    body_max_chars: int


@dataclass(slots=True)
class NetworkEndpointHit:
    url: str
    method: str
    status: int
    score: int
    last_seen: float
    hits: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "url": self.url,
            "status": self.status,
            "score": self.score,
            "hits": self.hits,
        }


@dataclass(slots=True)
class PricingResult:
    subtotal: float | None = None
    delivery_fee: float | None = None
    service_fee: float | None = None
    total: float | None = None


@dataclass(slots=True)
class EtaResult:
    eta_min_minutes: float | None = None
    eta_max_minutes: float | None = None
    eta_avg_minutes: float | None = None
    eta_source: str = "none"
    eta_range_text_raw: str | None = None


@dataclass(slots=True)
class RestaurantContext:
    restaurant_name: str
    restaurant_address: str | None = None
    source: str = "fallback"


@dataclass(slots=True)
class CheckoutResult:
    ts: str
    status: str
    platform: str = "rappi"
    address: str = ""
    restaurant: str = ""
    restaurant_address: str | None = None
    restaurant_source: str = "fallback"
    product: str = ""
    checkout_url: str = ""
    subtotal: float | None = None
    delivery_fee: float | None = None
    service_fee: float | None = None
    total: float | None = None
    eta_min_minutes: float | None = None
    eta_max_minutes: float | None = None
    eta_avg_minutes: float | None = None
    eta_source: str = "none"
    eta_range_text_raw: str | None = None
    currency: str = "MXN"
    screenshot_path: str | None = None
    network_log_file: str = ""
    matched_requests: int = 0
    top_endpoints: list[dict[str, Any]] = field(default_factory=list)
    extraction_warning: str | None = None
    extraction_source: str | None = None
    checkout_payload_file: str | None = None
    checkout_dom_snapshot_file: str | None = None
    checkout_candidates_file: str | None = None
    extracted_cart_item_title: str | None = None
    failed_step: int | None = None
    error_type: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        if self.status in {"success", "partial"}:
            return {
                "ts": self.ts,
                "status": self.status,
                "platform": self.platform,
                "address": self.address,
                "restaurant": self.restaurant,
                "restaurant_address": self.restaurant_address,
                "restaurant_source": self.restaurant_source,
                "product": self.product,
                "checkout_url": self.checkout_url,
                "subtotal": self.subtotal,
                "delivery_fee": self.delivery_fee,
                "service_fee": self.service_fee,
                "total": self.total,
                "eta_min_minutes": self.eta_min_minutes,
                "eta_max_minutes": self.eta_max_minutes,
                "eta_avg_minutes": self.eta_avg_minutes,
                "eta_source": self.eta_source,
                "eta_range_text_raw": self.eta_range_text_raw,
                "currency": self.currency,
                "screenshot_path": self.screenshot_path,
                "network_log_file": self.network_log_file,
                "matched_requests": self.matched_requests,
                "extraction_warning": self.extraction_warning,
                "extraction_source": self.extraction_source,
                "checkout_payload_file": self.checkout_payload_file,
                "checkout_dom_snapshot_file": self.checkout_dom_snapshot_file,
                "checkout_candidates_file": self.checkout_candidates_file,
                "extracted_cart_item_title": self.extracted_cart_item_title,
                "error_type": self.error_type,
                "error_message": self.error_message,
            }
        return {
            "ts": self.ts,
            "status": self.status,
            "failed_step": self.failed_step,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "platform": self.platform,
            "address": self.address,
            "restaurant": self.restaurant,
            "restaurant_address": self.restaurant_address,
            "restaurant_source": self.restaurant_source,
            "product": self.product,
            "checkout_url": self.checkout_url,
            "eta_min_minutes": self.eta_min_minutes,
            "eta_max_minutes": self.eta_max_minutes,
            "eta_avg_minutes": self.eta_avg_minutes,
            "eta_source": self.eta_source,
            "eta_range_text_raw": self.eta_range_text_raw,
            "screenshot_path": self.screenshot_path,
            "network_log_file": self.network_log_file,
            "matched_requests": self.matched_requests,
        }
