from __future__ import annotations

import re
import unicodedata
from typing import Any

from playwright.async_api import Page

from rappi.models import EtaResult, PricingResult, RestaurantContext


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return ascii_only.lower().strip()


def money_to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = str(value).replace(",", ".")
    match = re.search(r"(\d+(?:\.\d{1,2})?)", text)
    if not match:
        return None
    try:
        return round(float(match.group(1)), 2)
    except Exception:  # noqa: BLE001 - parsing should never break run
        return None


def extract_totals_from_payloads(payloads: dict[str, Any]) -> PricingResult:
    subtotal: float | None = None
    delivery_fee: float | None = None
    service_fee: float | None = None
    total: float | None = None

    summary_v2 = payloads.get("summary_v2")
    if isinstance(summary_v2, dict):
        summary_items = summary_v2.get("summary") or []
        if isinstance(summary_items, list):
            for row in summary_items:
                key = _normalize(str(row.get("key", "")))
                val = row.get("raw_value") or row.get("value")
                if key in {"total", "<b>total</b>"}:
                    total = money_to_float(val) or total
                sub_value = row.get("sub_value") or []
                if isinstance(sub_value, list):
                    for item in sub_value:
                        ikey = _normalize(str(item.get("key", "")))
                        ival = item.get("raw_value") or item.get("value")
                        if "productos" in ikey or "product" in ikey:
                            subtotal = money_to_float(ival) or subtotal
                        if "envio" in ikey or "shipping" in ikey:
                            delivery_fee = money_to_float(ival) or delivery_fee
                        if "servicio" in ikey or "service" in ikey:
                            service_fee = money_to_float(ival) or service_fee

    checkout_detail = payloads.get("checkout_detail")
    if isinstance(checkout_detail, dict):
        sections = checkout_detail.get("summary") or []
        if isinstance(sections, list):
            for sec in sections:
                details = sec.get("details") or []
                if isinstance(details, list):
                    for item in details:
                        key = _normalize(str(item.get("key", "")))
                        val = item.get("value")
                        if "subtotal de productos" in key:
                            subtotal = money_to_float(val) or subtotal
                        if "costo de envio" in key or "shipping" in key:
                            delivery_fee = money_to_float(val) or delivery_fee
                        if "tarifa de servicio" in key:
                            service_fee = money_to_float(val) or service_fee
                        if "<b>total</b>" in key or key == "total":
                            total = money_to_float(val) or total

    all_get = payloads.get("all_get")
    if isinstance(all_get, list) and all_get:
        first = all_get[0]
        stores = first.get("stores") or []
        if stores:
            store = stores[0]
            subtotal = money_to_float(store.get("product_total")) or subtotal
            charges = store.get("charges") or []
            for charge in charges:
                charge_type = _normalize(str(charge.get("charge_type", "")))
                charge_value = money_to_float(charge.get("value") or charge.get("total"))
                if charge_type == "shipping":
                    delivery_fee = charge_value or delivery_fee
                if charge_type == "service_fee":
                    service_fee = charge_value or service_fee
            st = money_to_float(store.get("sub_total"))
            if st is not None:
                total = st
            elif subtotal is not None:
                total = round(subtotal + (delivery_fee or 0) + (service_fee or 0), 2)

    return PricingResult(
        subtotal=subtotal,
        delivery_fee=delivery_fee,
        service_fee=service_fee,
        total=total,
    )


async def extract_eta_context(page: Page, payloads: dict[str, Any]) -> EtaResult:
    eta_min: float | None = None
    eta_max: float | None = None
    eta_avg: float | None = None
    source = "none"

    body_text = ""
    try:
        body_text = await page.inner_text("body", timeout=2500)
    except Exception:  # noqa: BLE001
        body_text = ""

    if body_text:
        range_match = re.search(r"(\d{1,3})\s*[-\u2013]\s*(\d{1,3})\s*min", body_text, flags=re.IGNORECASE)
        if range_match:
            eta_min = float(range_match.group(1))
            eta_max = float(range_match.group(2))
            eta_avg = round((eta_min + eta_max) / 2.0, 2)
            source = "dom:range"
        else:
            single_match = re.search(r"(?:entrega estimada[:\s]*)?(\d{1,3})\s*min", body_text, flags=re.IGNORECASE)
            if single_match:
                eta_min = float(single_match.group(1))
                eta_max = float(single_match.group(1))
                eta_avg = float(single_match.group(1))
                source = "dom:single"

    if eta_avg is None:
        all_get = payloads.get("all_get")
        if isinstance(all_get, list) and all_get:
            try:
                stores = all_get[0].get("stores") or []
                if stores:
                    payload_eta = money_to_float(stores[0].get("eta"))
                    if payload_eta is not None:
                        eta_min = payload_eta
                        eta_max = payload_eta
                        eta_avg = payload_eta
                        source = "payload:all_get"
            except Exception:  # noqa: BLE001
                pass

    return EtaResult(
        eta_min_minutes=eta_min,
        eta_max_minutes=eta_max,
        eta_avg_minutes=eta_avg,
        eta_source=source,
    )


async def extract_restaurant_context(page: Page, payloads: dict[str, Any], fallback_name: str) -> RestaurantContext:
    restaurant_name: str | None = None
    restaurant_address: str | None = None
    source = "fallback"

    all_get = payloads.get("all_get")
    if isinstance(all_get, list) and all_get:
        try:
            stores = all_get[0].get("stores") or []
            if stores:
                first_store = stores[0]
                restaurant_name = first_store.get("name") or restaurant_name
                restaurant_address = first_store.get("address") or restaurant_address
                if restaurant_name or restaurant_address:
                    source = "payload:all_get"
        except Exception:  # noqa: BLE001
            pass

    checkout_detail = payloads.get("checkout_detail")
    if isinstance(checkout_detail, dict):
        try:
            sections = checkout_detail.get("summary") or []
            if isinstance(sections, list):
                for section in sections:
                    header = section.get("header") or {}
                    title = header.get("title")
                    if isinstance(title, str) and title.strip():
                        restaurant_name = title.strip()
                        if source == "fallback":
                            source = "payload:checkout_detail"
                        break
        except Exception:  # noqa: BLE001
            pass

    if not restaurant_name:
        try:
            h1_loc = page.locator("h1").first
            if await h1_loc.is_visible(timeout=1200):
                txt = (await h1_loc.inner_text(timeout=1500)).strip()
                if txt:
                    restaurant_name = txt
                    source = "dom:h1"
        except Exception:  # noqa: BLE001
            pass

    if not restaurant_address:
        dom_address_selectors = [
            'h2[data-testid="typography"]',
            "h2",
            "div:has(h1) h2",
        ]
        for selector in dom_address_selectors:
            try:
                loc = page.locator(selector)
                count = await loc.count()
                for idx in range(count):
                    txt = (await loc.nth(idx).inner_text(timeout=1200)).strip()
                    norm = _normalize(txt)
                    if "," in txt and any(token in norm for token in ["cdmx", "ciudad de mexico", "mexico"]):
                        restaurant_address = txt
                        if source == "fallback":
                            source = "dom:h2"
                        break
                if restaurant_address:
                    break
            except Exception:  # noqa: BLE001
                continue

    if not restaurant_name:
        restaurant_name = fallback_name

    return RestaurantContext(
        restaurant_name=restaurant_name,
        restaurant_address=restaurant_address,
        source=source,
    )
