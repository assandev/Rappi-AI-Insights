from __future__ import annotations

import re
from typing import Any

from playwright.async_api import Page

from rappi.models import EtaResult, PricingResult, RestaurantContext


def money_to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = str(value).replace(",", ".")
    match = re.search(r"(-?\d+(?:\.\d{1,2})?)", text)
    if not match:
        return None
    try:
        return round(float(match.group(1)), 2)
    except Exception:  # noqa: BLE001
        return None


def extract_totals_from_payloads(payloads: dict[str, Any]) -> PricingResult:
    # Priority order: concrete checkout endpoints first, generic buckets later.
    roots: list[Any] = []
    for key in (
        "checkout_presentation",
        "draft_order",
        "draft_orders",
        "carts_view",
        "checkout",
        "pricing",
        "summary",
        "cart",
    ):
        if key in payloads:
            roots.append(payloads[key])

    subtotal = _extract_money_from_roots(roots, ["subtotal", "sub_total", "items_subtotal", "products_subtotal"])
    delivery_fee = _extract_money_from_roots(roots, ["delivery_fee", "deliveryFee", "shipping_fee", "shippingFee"])
    service_fee = _extract_money_from_roots(roots, ["service_fee", "serviceFee", "service"])
    total = _extract_money_from_roots(roots, ["total", "grand_total", "grandTotal", "checkout_total"])

    # Uber checkout often encodes fare lines; use labels as stronger signal.
    if subtotal is None:
        subtotal = _find_labeled_amount(roots, ["subtotal", "productos", "costo de productos"])
    if delivery_fee is None:
        delivery_fee = _find_labeled_amount(roots, ["delivery", "envio", "envío"])
    if service_fee is None:
        service_fee = _find_labeled_amount(roots, ["service", "servicio"])
    if total is None:
        total = _find_labeled_amount(roots, ["total"])

    if total is None and subtotal is not None:
        total = round(subtotal + (delivery_fee or 0) + (service_fee or 0), 2)

    return PricingResult(
        subtotal=subtotal,
        delivery_fee=delivery_fee,
        service_fee=service_fee,
        total=total,
    )


async def extract_totals_from_dom(page: Page) -> PricingResult:
    body = ""
    try:
        body = await page.inner_text("body", timeout=3000)
    except Exception:  # noqa: BLE001
        pass
    if not body:
        return PricingResult()

    subtotal = _money_after_label(body, ["Subtotal", "Subtotal de productos"])
    delivery_fee = _money_after_label(body, ["Delivery fee", "Costo de envio", "Costo de envío"])
    service_fee = _money_after_label(body, ["Service fee", "Tarifa de servicio"])
    total = _money_after_label(body, ["Total"])

    if total is None and subtotal is not None:
        total = round(subtotal + (delivery_fee or 0) + (service_fee or 0), 2)

    return PricingResult(
        subtotal=subtotal,
        delivery_fee=delivery_fee,
        service_fee=service_fee,
        total=total,
    )


async def extract_eta_context(page: Page, payloads: dict[str, Any]) -> EtaResult:
    # Network-first ETA best effort.
    roots: list[Any] = []
    for key in ("checkout_presentation", "draft_order", "checkout", "summary", "cart"):
        if key in payloads:
            roots.append(payloads[key])
    eta_from_payload = None
    for root in roots:
        eta_from_payload = _find_by_key(root, ["eta", "eta_minutes", "delivery_eta", "estimated_delivery_time"])
        if eta_from_payload is not None:
            break
    if eta_from_payload is not None:
        return EtaResult(
            eta_min_minutes=eta_from_payload,
            eta_max_minutes=eta_from_payload,
            eta_avg_minutes=eta_from_payload,
            eta_source="payload",
        )

    body = ""
    try:
        body = await page.inner_text("body", timeout=2500)
    except Exception:  # noqa: BLE001
        pass

    if body:
        range_match = re.search(r"(\d{1,3})\s*[-\u2013]\s*(\d{1,3})\s*min", body, flags=re.IGNORECASE)
        if range_match:
            low = float(range_match.group(1))
            high = float(range_match.group(2))
            return EtaResult(
                eta_min_minutes=low,
                eta_max_minutes=high,
                eta_avg_minutes=round((low + high) / 2.0, 2),
                eta_source="dom:range",
            )
        single = re.search(r"(\d{1,3})\s*min", body, flags=re.IGNORECASE)
        if single:
            val = float(single.group(1))
            return EtaResult(
                eta_min_minutes=val,
                eta_max_minutes=val,
                eta_avg_minutes=val,
                eta_source="dom:single",
            )
    return EtaResult()


async def extract_restaurant_context(page: Page, payloads: dict[str, Any], fallback_name: str) -> RestaurantContext:
    name = None
    address = None
    source = "fallback"

    # TODO: tighten payload path once concrete Uber checkout payload keys are confirmed.
    maybe_name = _find_text_by_key(payloads, ["restaurant_name", "store_name", "merchant_name"])
    maybe_address = _find_text_by_key(payloads, ["restaurant_address", "store_address", "address"])
    if maybe_name:
        name = maybe_name
        source = "payload"
    if maybe_address:
        address = maybe_address
        if source == "fallback":
            source = "payload"

    if not name:
        try:
            h1 = page.locator("h1").first
            if await h1.is_visible(timeout=1200):
                txt = (await h1.inner_text(timeout=1200)).strip()
                if txt:
                    name = txt
                    source = "dom:h1"
        except Exception:  # noqa: BLE001
            pass

    if not name:
        name = fallback_name

    return RestaurantContext(
        restaurant_name=name,
        restaurant_address=address,
        source=source,
    )


def _find_by_key(obj: Any, keys: list[str]) -> float | None:
    keys_l = {k.lower() for k in keys}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in keys_l:
                val = money_to_float(v)
                if val is not None:
                    return val
            nested = _find_by_key(v, keys)
            if nested is not None:
                return nested
    elif isinstance(obj, list):
        for item in obj:
            nested = _find_by_key(item, keys)
            if nested is not None:
                return nested
    return None


def _extract_money_from_roots(roots: list[Any], keys: list[str]) -> float | None:
    for root in roots:
        value = _find_by_key(root, keys)
        if value is None:
            continue
        normalized = _normalize_minor_units(value)
        if normalized is not None:
            return normalized
    return None


def _normalize_minor_units(value: float | None) -> float | None:
    if value is None:
        return None
    # Uber APIs frequently use integer cents, e.g. 5900 => 59.00
    if abs(value) >= 1000:
        return round(value / 100.0, 2)
    return round(value, 2)


def _find_labeled_amount(roots: list[Any], labels: list[str]) -> float | None:
    for root in roots:
        val = _find_labeled_amount_in_obj(root, labels)
        if val is not None:
            return _normalize_minor_units(val)
    return None


def _find_labeled_amount_in_obj(obj: Any, labels: list[str]) -> float | None:
    labels_l = [l.lower() for l in labels]
    if isinstance(obj, dict):
        # Pattern: {"label"/"title"/"name": "...", "value"/"amount"/"raw_value": ...}
        text_fields = []
        for key in ("label", "title", "name", "key", "description", "displayName"):
            if key in obj and isinstance(obj[key], str):
                text_fields.append(obj[key].lower())
        if any(any(label in field for label in labels_l) for field in text_fields):
            for money_key in ("amount", "value", "raw_value", "total", "price"):
                if money_key in obj:
                    val = money_to_float(obj[money_key])
                    if val is not None:
                        return val
        for v in obj.values():
            nested = _find_labeled_amount_in_obj(v, labels)
            if nested is not None:
                return nested
    elif isinstance(obj, list):
        for item in obj:
            nested = _find_labeled_amount_in_obj(item, labels)
            if nested is not None:
                return nested
    return None


def _find_text_by_key(obj: Any, keys: list[str]) -> str | None:
    keys_l = {k.lower() for k in keys}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in keys_l and isinstance(v, str) and v.strip():
                return v.strip()
            nested = _find_text_by_key(v, keys)
            if nested:
                return nested
    elif isinstance(obj, list):
        for item in obj:
            nested = _find_text_by_key(item, keys)
            if nested:
                return nested
    return None


def _money_after_label(text: str, labels: list[str]) -> float | None:
    for label in labels:
        match = re.search(rf"{re.escape(label)}[^\d$-]*([$]?\s*-?\d+(?:[.,]\d{{1,2}})?)", text, flags=re.IGNORECASE)
        if match:
            return money_to_float(match.group(1))
    return None


# --- Strict Uber extraction overrides (network-first, label-driven) ---
import unicodedata
from pathlib import Path

from rappi.io_utils import iso_now, write_json

_STRICT_MONEY_RE = re.compile(r"(?:mxn\s*)?\$?\s*(-?\d{1,4}(?:[.,]\d{1,2})?)", flags=re.IGNORECASE)
_IGNORE_LABEL_TOKENS = ("credito", "uber cash", "descuento", "propina", "tip", "impuesto")


def _s_norm(text: str) -> str:
    base = unicodedata.normalize("NFKD", str(text))
    ascii_only = base.encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_only.lower().split())


def _strict_money_to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        val = float(value)
    else:
        match = _STRICT_MONEY_RE.search(str(value).replace(",", "."))
        if not match:
            return None
        val = float(match.group(1))
    if abs(val) >= 1000:
        return round(val / 100.0, 2)
    return round(val, 2)


def _strict_is_subtotal(label_norm: str) -> bool:
    return any(token in label_norm for token in ("subtotal", "productos", "articulos", "items"))


def _strict_is_delivery(label_norm: str) -> bool:
    return any(token in label_norm for token in ("envio", "delivery", "costo de envio", "tarifa de entrega"))


def _strict_is_service(label_norm: str) -> bool:
    return any(token in label_norm for token in ("tarifa de servicio", "service fee", "servicio"))


def _strict_is_total(label_norm: str) -> bool:
    return any(token in label_norm for token in ("total del pedido", "order total")) or label_norm == "total"


def _strict_first_string(obj: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _strict_first_money(obj: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key not in obj:
            continue
        value = obj.get(key)
        if isinstance(value, dict):
            nested = _strict_first_money(value, ("value", "amount", "raw_value", "total", "price"))
            if nested is not None:
                return nested
        parsed = _strict_money_to_float(value)
        if parsed is not None:
            return parsed
    return None


def _strict_iter_labeled_rows(obj: Any):
    if isinstance(obj, dict):
        label = _strict_first_string(obj, ("label", "title", "name", "key", "description", "displayName", "text"))
        amount = _strict_first_money(obj, ("amount", "value", "raw_value", "total", "price", "displayValue", "formattedValue"))
        if label and amount is not None:
            yield label, amount
        for value in obj.values():
            yield from _strict_iter_labeled_rows(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _strict_iter_labeled_rows(item)


def extract_ubereats_totals_from_network(payloads: dict[str, Any], product: str) -> PricingResult:
    _ = product
    roots = [payloads.get(key) for key in ("checkout_presentation", "draft_order", "draft_orders", "carts_view") if key in payloads]
    subtotal: float | None = None
    delivery_fee: float | None = None
    service_fee: float | None = None
    total: float | None = None

    for root in roots:
        for label, amount in _strict_iter_labeled_rows(root):
            ln = _s_norm(label)
            if any(token in ln for token in _IGNORE_LABEL_TOKENS):
                continue
            if subtotal is None and _strict_is_subtotal(ln):
                subtotal = amount
                continue
            if delivery_fee is None and _strict_is_delivery(ln):
                delivery_fee = amount
                continue
            if service_fee is None and _strict_is_service(ln):
                service_fee = amount
                continue
            if total is None and _strict_is_total(ln):
                total = amount

    if total is None and subtotal is not None:
        total = round(subtotal + (delivery_fee or 0) + (service_fee or 0), 2)
    return PricingResult(subtotal=subtotal, delivery_fee=delivery_fee, service_fee=service_fee, total=total)


def _strict_find_labeled_money_in_text(text: str, labels: tuple[str, ...]) -> float | None:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        ln = _s_norm(line)
        if any(token in ln for token in _IGNORE_LABEL_TOKENS):
            continue
        if not any(label in ln for label in labels):
            continue
        match = _STRICT_MONEY_RE.search(line.replace(",", "."))
        if match:
            parsed = _strict_money_to_float(match.group(1))
            if parsed is not None:
                return parsed
    return None


async def _strict_checkout_text_snapshot(page: Page) -> str:
    selectors = (
        "main",
        '[data-testid*="checkout" i]',
        '[data-test*="checkout" i]',
        '[role="dialog"]',
        "body",
    )
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if await loc.is_visible(timeout=700):
                text = await loc.inner_text(timeout=1300)
                if text and text.strip():
                    return text
        except Exception:  # noqa: BLE001
            continue
    return ""


async def extract_ubereats_totals_from_dom(page: Page) -> PricingResult:
    text = await _strict_checkout_text_snapshot(page)
    if not text:
        return PricingResult()
    subtotal = _strict_find_labeled_money_in_text(text, ("subtotal", "productos", "articulos", "items"))
    delivery_fee = _strict_find_labeled_money_in_text(text, ("envio", "delivery", "costo de envio", "tarifa de entrega"))
    service_fee = _strict_find_labeled_money_in_text(text, ("tarifa de servicio", "service fee", "servicio"))
    total = _strict_find_labeled_money_in_text(text, ("total del pedido", "order total", "total"))
    if total is None and subtotal is not None:
        total = round(subtotal + (delivery_fee or 0) + (service_fee or 0), 2)
    return PricingResult(subtotal=subtotal, delivery_fee=delivery_fee, service_fee=service_fee, total=total)


def merge_pricing(network_pricing: PricingResult, dom_pricing: PricingResult) -> tuple[PricingResult, str]:
    merged = PricingResult(
        subtotal=network_pricing.subtotal if network_pricing.subtotal is not None else dom_pricing.subtotal,
        delivery_fee=network_pricing.delivery_fee if network_pricing.delivery_fee is not None else dom_pricing.delivery_fee,
        service_fee=network_pricing.service_fee if network_pricing.service_fee is not None else dom_pricing.service_fee,
        total=network_pricing.total if network_pricing.total is not None else dom_pricing.total,
    )
    net_any = any(v is not None for v in (network_pricing.subtotal, network_pricing.delivery_fee, network_pricing.service_fee, network_pricing.total))
    dom_any = any(v is not None for v in (dom_pricing.subtotal, dom_pricing.delivery_fee, dom_pricing.service_fee, dom_pricing.total))
    if net_any and dom_any:
        return merged, "mixed"
    if net_any:
        return merged, "network"
    if dom_any:
        return merged, "dom"
    return merged, "none"


def validate_ubereats_checkout_result(pricing: PricingResult, product: str) -> str | None:
    if pricing.subtotal is None or pricing.total is None:
        return "Missing subtotal/total in extracted checkout data."
    if pricing.total + 0.5 < pricing.subtotal:
        return "Invalid totals: total is lower than subtotal."
    if pricing.subtotal == 5.0 and pricing.total == 5.0 and pricing.delivery_fee is None and pricing.service_fee is None:
        return "Invalid extraction signature detected (subtotal=total=5.0 with null fees)."
    ranges = {
        _s_norm("McFlurry Oreo"): (20, 180),
        _s_norm("Big Mac"): (25, 250),
        _s_norm("Cuarto de Libra con Queso"): (30, 260),
    }
    low, high = ranges.get(_s_norm(product), (10, 300))
    if not (low <= pricing.subtotal <= high):
        return f"Subtotal {pricing.subtotal} outside expected range [{low}, {high}] for '{product}'."
    if not (low <= pricing.total <= high + 120):
        return f"Total {pricing.total} outside expected range for '{product}'."
    return None


async def save_checkout_dom_snapshot(page: Page, output_path: Path) -> str | None:
    scoped_text = await _strict_checkout_text_snapshot(page)
    body_text = ""
    try:
        body_text = await page.inner_text("body", timeout=2500)
    except Exception:  # noqa: BLE001
        body_text = ""
    write_json(
        output_path,
        {
            "ts": iso_now(),
            "url": page.url,
            "title": await page.title(),
            "scoped_checkout_text": scoped_text,
            "body_text": body_text,
        },
    )
    return str(output_path)


def extract_totals_from_payloads(payloads: dict[str, Any]) -> PricingResult:
    # Override with strict network extraction.
    return extract_ubereats_totals_from_network(payloads, product="")


async def extract_totals_from_dom(page: Page) -> PricingResult:
    # Override with strict DOM fallback extraction.
    return await extract_ubereats_totals_from_dom(page)


# --- Payload-specific forensic extraction overrides ---
from datetime import datetime, timedelta


def extract_ubereats_checkout_payload(payloads: dict[str, Any]) -> dict[str, Any] | None:
    root = payloads.get("checkout_presentation")
    if not isinstance(root, dict):
        return None
    data = root.get("data")
    if not isinstance(data, dict):
        return None
    checkout_payloads = data.get("checkoutPayloads")
    if isinstance(checkout_payloads, dict):
        return checkout_payloads
    return None


def extract_currency_value(text: Any) -> float | None:
    if text is None:
        return None
    match = _STRICT_MONEY_RE.search(str(text).replace(",", "."))
    if not match:
        return None
    try:
        return round(float(match.group(1)), 2)
    except Exception:  # noqa: BLE001
        return None


def extract_fare_breakdown_by_label(charges: list[Any]) -> dict[str, float | None]:
    out: dict[str, float | None] = {
        "subtotal": None,
        "delivery_fee": None,
        "service_fee": None,
    }
    for charge in charges:
        if not isinstance(charge, dict):
            continue
        title = (
            (((charge.get("title") or {}) if isinstance(charge.get("title"), dict) else {}).get("text"))
            or charge.get("title")
            or ""
        )
        title_norm = _s_norm(str(title))
        value_obj = (charge.get("value") or {}) if isinstance(charge.get("value"), dict) else {}
        charge_value_obj = (charge.get("chargeValue") or {}) if isinstance(charge.get("chargeValue"), dict) else {}
        badge_obj = (charge_value_obj.get("badgeChargeValue") or {}) if isinstance(charge_value_obj.get("badgeChargeValue"), dict) else {}
        value_text = value_obj.get("text") or badge_obj.get("text")
        value = extract_currency_value(value_text)
        if value is None:
            continue
        if "subtotal" in title_norm:
            out["subtotal"] = value
        elif "envio" in title_norm or "delivery" in title_norm:
            out["delivery_fee"] = value
        elif "servicio" in title_norm or "service" in title_norm:
            out["service_fee"] = value
    return out


def extract_cart_item_title(checkout_payloads: dict[str, Any] | None) -> str | None:
    if not isinstance(checkout_payloads, dict):
        return None
    cart_items = ((checkout_payloads.get("cartItems") or {}) if isinstance(checkout_payloads.get("cartItems"), dict) else {})
    items = cart_items.get("cartItems")
    if not isinstance(items, list) or not items:
        return None
    first = items[0] if isinstance(items[0], dict) else {}
    title = (first.get("title") or {}) if isinstance(first.get("title"), dict) else {}
    rich = title.get("richTextElements")
    if isinstance(rich, list):
        parts: list[str] = []
        for token in rich:
            if isinstance(token, dict):
                txt = token.get("text")
                if isinstance(txt, str) and txt.strip():
                    parts.append(txt.strip())
        if parts:
            return " ".join(parts).strip()
    txt = title.get("text")
    if isinstance(txt, str) and txt.strip():
        return txt.strip()
    return None


def _parse_eta_wallclock_range(range_text: str) -> tuple[float | None, float | None, float | None]:
    text = range_text.strip()
    # Example: 8:23–8:34 PM or 8:23-8:34 PM
    m = re.search(r"(\d{1,2}:\d{2})\s*[-\u2013]\s*(\d{1,2}:\d{2})\s*([AP]M)", text, flags=re.IGNORECASE)
    if not m:
        return None, None, None
    t1 = m.group(1)
    t2 = m.group(2)
    mer = m.group(3).upper()
    now = datetime.now()
    try:
        dt1 = datetime.strptime(f"{t1} {mer}", "%I:%M %p").replace(year=now.year, month=now.month, day=now.day)
        dt2 = datetime.strptime(f"{t2} {mer}", "%I:%M %p").replace(year=now.year, month=now.month, day=now.day)
    except Exception:  # noqa: BLE001
        return None, None, None
    if dt1 < now - timedelta(hours=1):
        dt1 += timedelta(days=1)
    if dt2 < dt1:
        dt2 += timedelta(days=1)
    min_m = max(0.0, round((dt1 - now).total_seconds() / 60.0, 2))
    max_m = max(min_m, round((dt2 - now).total_seconds() / 60.0, 2))
    avg_m = round((min_m + max_m) / 2.0, 2)
    return min_m, max_m, avg_m


def extract_ubereats_totals_from_network(payloads: dict[str, Any], product: str) -> PricingResult:
    _ = product
    cp = extract_ubereats_checkout_payload(payloads)
    if not cp:
        return PricingResult()

    subtotal = extract_currency_value((((cp.get("subtotal") or {}) if isinstance(cp.get("subtotal"), dict) else {}).get("subtotal") or {}).get("formattedValue")) if isinstance((((cp.get("subtotal") or {}) if isinstance(cp.get("subtotal"), dict) else {}).get("subtotal")), dict) else None
    if subtotal is None:
        subtotal = extract_currency_value((((cp.get("subtotal") or {}) if isinstance(cp.get("subtotal"), dict) else {}).get("formattedSubtotalAmount") or {}).get("accessibilityText")) if isinstance((((cp.get("subtotal") or {}) if isinstance(cp.get("subtotal"), dict) else {}).get("formattedSubtotalAmount")), dict) else None

    total = extract_currency_value((((cp.get("total") or {}) if isinstance(cp.get("total"), dict) else {}).get("total") or {}).get("formattedValue")) if isinstance((((cp.get("total") or {}) if isinstance(cp.get("total"), dict) else {}).get("total")), dict) else None

    fare = ((cp.get("fareBreakdown") or {}) if isinstance(cp.get("fareBreakdown"), dict) else {})
    charges = fare.get("charges") if isinstance(fare.get("charges"), list) else []
    mapped = extract_fare_breakdown_by_label(charges)
    if mapped.get("subtotal") is not None:
        subtotal = mapped["subtotal"]
    delivery_fee = mapped.get("delivery_fee")
    service_fee = mapped.get("service_fee")

    if total is None and subtotal is not None:
        total = round(subtotal + (delivery_fee or 0) + (service_fee or 0), 2)
    return PricingResult(subtotal=subtotal, delivery_fee=delivery_fee, service_fee=service_fee, total=total)


async def extract_eta_context(page: Page, payloads: dict[str, Any]) -> EtaResult:
    cp = extract_ubereats_checkout_payload(payloads)
    if cp:
        eta = ((cp.get("eta") or {}) if isinstance(cp.get("eta"), dict) else {})
        range_text = eta.get("rangeText") if isinstance(eta.get("rangeText"), str) else None
        if range_text:
            min_m, max_m, avg_m = _parse_eta_wallclock_range(range_text)
            if avg_m is not None:
                return EtaResult(
                    eta_min_minutes=min_m,
                    eta_max_minutes=max_m,
                    eta_avg_minutes=avg_m,
                    eta_source="payload:rangeText",
                )
            return EtaResult(
                eta_min_minutes=None,
                eta_max_minutes=None,
                eta_avg_minutes=None,
                eta_source=f"payload:rangeText:{range_text}",
            )
    return await _extract_eta_from_dom_fallback(page)


async def _extract_eta_from_dom_fallback(page: Page) -> EtaResult:
    body = ""
    try:
        body = await page.inner_text("body", timeout=2500)
    except Exception:  # noqa: BLE001
        body = ""
    if body:
        range_match = re.search(r"(\d{1,3})\s*[-\u2013]\s*(\d{1,3})\s*min", body, flags=re.IGNORECASE)
        if range_match:
            low = float(range_match.group(1))
            high = float(range_match.group(2))
            return EtaResult(
                eta_min_minutes=low,
                eta_max_minutes=high,
                eta_avg_minutes=round((low + high) / 2.0, 2),
                eta_source="dom:range",
            )
        single = re.search(r"(\d{1,3})\s*min", body, flags=re.IGNORECASE)
        if single:
            val = float(single.group(1))
            return EtaResult(eta_min_minutes=val, eta_max_minutes=val, eta_avg_minutes=val, eta_source="dom:single")
    return EtaResult()


def validate_ubereats_result(
    pricing: PricingResult,
    expected_product: str,
    checkout_payloads: dict[str, Any] | None,
) -> tuple[str | None, str | None, str | None]:
    actual_title = extract_cart_item_title(checkout_payloads)
    if actual_title:
        if _s_norm(actual_title) != _s_norm(expected_product):
            msg = f"Expected product '{expected_product}' but payload cart item is '{actual_title}'."
            return msg, "ProductMismatchError", msg

    if pricing.subtotal is None or pricing.total is None or pricing.subtotal <= 0:
        msg = "Missing or invalid subtotal/total from checkout_presentation."
        return msg, "ExtractionValidationError", msg
    if pricing.total + 0.5 < pricing.subtotal:
        msg = "Invalid totals: total is lower than subtotal."
        return msg, "ExtractionValidationError", msg
    if pricing.subtotal == 5.0 and pricing.total == 5.0:
        msg = "Invalid extraction signature detected (subtotal=total=5.0)."
        return msg, "ExtractionValidationError", msg

    has_fare = isinstance((checkout_payloads or {}).get("fareBreakdown"), dict)
    if has_fare and (pricing.delivery_fee is None or pricing.service_fee is None):
        msg = "fareBreakdown exists but delivery/service fee could not be extracted."
        return msg, "ExtractionValidationError", msg
    return None, None, None


def validate_ubereats_checkout_result(pricing: PricingResult, product: str) -> str | None:
    # Backward-compatible wrapper kept for existing call sites.
    warning, _, _ = validate_ubereats_result(pricing=pricing, expected_product=product, checkout_payloads=None)
    return warning


# --- ETA wall-clock parsing overrides (Mexico City local conversion) ---
def extract_ubereats_eta_range_text(checkout_payloads: dict[str, Any] | None) -> str | None:
    if not isinstance(checkout_payloads, dict):
        return None

    eta = checkout_payloads.get("eta")
    if isinstance(eta, dict):
        range_text = eta.get("rangeText")
        if isinstance(range_text, str) and range_text.strip():
            return range_text.strip()

    delivery_info = checkout_payloads.get("deliveryOptInInfo")
    if not isinstance(delivery_info, dict):
        return None
    display_infos = delivery_info.get("displayInfos")
    if not isinstance(display_infos, list):
        return None

    for info in display_infos:
        if not isinstance(info, dict):
            continue
        title_text = _extract_text_from_unknown(info.get("title"))
        opt_type = _extract_optin_type(info)
        if "basica" not in _s_norm(title_text) and opt_type != "STANDARD_DELIVERY":
            continue
        candidate = _extract_range_text_in_obj(info)
        if candidate:
            return candidate
    return None


def parse_time_range_text(
    range_text: str,
    reference_dt,
    tz_name: str = "America/Mexico_City",
) -> tuple[datetime | None, datetime | None]:
    from zoneinfo import ZoneInfo

    text = range_text.replace("–", "-").strip()
    m = re.search(
        r"(\d{1,2}:\d{2})\s*([AP]M)?\s*-\s*(\d{1,2}:\d{2})\s*([AP]M)?",
        text,
        flags=re.IGNORECASE,
    )
    if not m:
        return None, None

    t1, mer1, t2, mer2 = m.group(1), m.group(2), m.group(3), m.group(4)
    mer1 = mer1.upper() if isinstance(mer1, str) else None
    mer2 = mer2.upper() if isinstance(mer2, str) else None
    if mer1 is None and mer2 is not None:
        mer1 = mer2
    if mer2 is None and mer1 is not None:
        mer2 = mer1
    if mer1 is None or mer2 is None:
        return None, None

    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("UTC")
    ref_local = reference_dt.astimezone(tz)
    try:
        start_clock = datetime.strptime(f"{t1} {mer1}", "%I:%M %p")
        end_clock = datetime.strptime(f"{t2} {mer2}", "%I:%M %p")
    except Exception:  # noqa: BLE001
        return None, None

    start_dt = ref_local.replace(hour=start_clock.hour, minute=start_clock.minute, second=0, microsecond=0)
    end_dt = ref_local.replace(hour=end_clock.hour, minute=end_clock.minute, second=0, microsecond=0)

    if start_dt < ref_local - timedelta(hours=2):
        start_dt += timedelta(days=1)
    if end_dt < ref_local - timedelta(hours=2):
        end_dt += timedelta(days=1)
    if end_dt < start_dt:
        end_dt += timedelta(days=1)

    return start_dt, end_dt


def compute_eta_minutes_from_range(
    range_text: str,
    run_ts_utc: str | None,
    tz_name: str = "America/Mexico_City",
) -> tuple[float | None, float | None, float | None]:
    from datetime import timezone
    from zoneinfo import ZoneInfo

    if run_ts_utc:
        try:
            iso = run_ts_utc.replace("Z", "+00:00")
            ref_utc = datetime.fromisoformat(iso)
            if ref_utc.tzinfo is None:
                ref_utc = ref_utc.replace(tzinfo=timezone.utc)
            else:
                ref_utc = ref_utc.astimezone(timezone.utc)
        except Exception:  # noqa: BLE001
            ref_utc = datetime.now(timezone.utc)
    else:
        ref_utc = datetime.now(timezone.utc)

    start_dt, end_dt = parse_time_range_text(range_text, reference_dt=ref_utc, tz_name=tz_name)
    if start_dt is None or end_dt is None:
        return None, None, None

    try:
        ref_local = ref_utc.astimezone(ZoneInfo(tz_name))
    except Exception:  # noqa: BLE001
        ref_local = ref_utc

    min_m = round((start_dt - ref_local).total_seconds() / 60.0, 2)
    max_m = round((end_dt - ref_local).total_seconds() / 60.0, 2)
    avg_m = round((min_m + max_m) / 2.0, 2)
    return min_m, max_m, avg_m


async def extract_eta_context(page: Page, payloads: dict[str, Any], run_ts_utc: str | None = None) -> EtaResult:
    checkout_payloads = extract_ubereats_checkout_payload(payloads)
    if checkout_payloads:
        range_text = extract_ubereats_eta_range_text(checkout_payloads)
        if range_text:
            min_m, max_m, avg_m = compute_eta_minutes_from_range(
                range_text=range_text,
                run_ts_utc=run_ts_utc,
                tz_name="America/Mexico_City",
            )
            if avg_m is not None:
                return EtaResult(
                    eta_min_minutes=min_m,
                    eta_max_minutes=max_m,
                    eta_avg_minutes=avg_m,
                    eta_source="payload:rangeText:minutes",
                    eta_range_text_raw=range_text,
                )
            return EtaResult(
                eta_min_minutes=None,
                eta_max_minutes=None,
                eta_avg_minutes=None,
                eta_source="payload_parse_failed",
                eta_range_text_raw=range_text,
            )
    return await _extract_eta_from_dom_fallback_v2(page)


async def _extract_eta_from_dom_fallback_v2(page: Page) -> EtaResult:
    body = ""
    try:
        body = await page.inner_text("body", timeout=2500)
    except Exception:  # noqa: BLE001
        body = ""
    if body:
        range_match = re.search(r"(\d{1,3})\s*[-\u2013]\s*(\d{1,3})\s*min", body, flags=re.IGNORECASE)
        if range_match:
            low = float(range_match.group(1))
            high = float(range_match.group(2))
            return EtaResult(
                eta_min_minutes=low,
                eta_max_minutes=high,
                eta_avg_minutes=round((low + high) / 2.0, 2),
                eta_source="dom:range",
                eta_range_text_raw=range_match.group(0),
            )
        single = re.search(r"(\d{1,3})\s*min", body, flags=re.IGNORECASE)
        if single:
            val = float(single.group(1))
            return EtaResult(
                eta_min_minutes=val,
                eta_max_minutes=val,
                eta_avg_minutes=val,
                eta_source="dom:single",
                eta_range_text_raw=single.group(0),
            )
    return EtaResult()


def _extract_text_from_unknown(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("text", "label", "value", "accessibilityText", "rangeText"):
            nested = _extract_text_from_unknown(value.get(key))
            if nested:
                return nested
        rich = value.get("richTextElements")
        if isinstance(rich, list):
            parts = [_extract_text_from_unknown(item) for item in rich]
            return " ".join(p for p in parts if p).strip()
        return ""
    if isinstance(value, list):
        parts = [_extract_text_from_unknown(item) for item in value]
        return " ".join(p for p in parts if p).strip()
    return str(value).strip()


def _extract_optin_type(info: dict[str, Any]) -> str:
    opt = info.get("optInDetails")
    if isinstance(opt, dict):
        typ = opt.get("optInType")
        if isinstance(typ, str):
            return typ.strip()
    return ""


def _extract_range_text_in_obj(obj: Any) -> str | None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "rangeText" and isinstance(value, str) and value.strip():
                return value.strip()
            nested = _extract_range_text_in_obj(value)
            if nested:
                return nested
    elif isinstance(obj, list):
        for item in obj:
            nested = _extract_range_text_in_obj(item)
            if nested:
                return nested
    elif isinstance(obj, str):
        txt = obj.strip()
        if re.search(r"\d{1,2}:\d{2}\s*[-\u2013]\s*\d{1,2}:\d{2}", txt):
            return txt
    return None


# --- ETA parser hotfix override ---
def parse_time_range_text(
    range_text: str,
    reference_dt,
    tz_name: str = "America/Mexico_City",
) -> tuple[datetime | None, datetime | None]:
    from zoneinfo import ZoneInfo

    text = (
        range_text.replace("â€“", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
        .strip()
    )
    m = re.search(
        r"(\d{1,2}:\d{2})\s*([AP]M)?\s*[-\u2013\u2014]\s*(\d{1,2}:\d{2})\s*([AP]M)?",
        text,
        flags=re.IGNORECASE,
    )
    if not m:
        return None, None

    t1, mer1, t2, mer2 = m.group(1), m.group(2), m.group(3), m.group(4)
    mer1 = mer1.upper() if isinstance(mer1, str) else None
    mer2 = mer2.upper() if isinstance(mer2, str) else None
    if mer1 is None and mer2 is not None:
        mer1 = mer2
    if mer2 is None and mer1 is not None:
        mer2 = mer1
    if mer1 is None or mer2 is None:
        return None, None

    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("UTC")
    ref_local = reference_dt.astimezone(tz)
    try:
        start_clock = datetime.strptime(f"{t1} {mer1}", "%I:%M %p")
        end_clock = datetime.strptime(f"{t2} {mer2}", "%I:%M %p")
    except Exception:  # noqa: BLE001
        return None, None

    start_dt = ref_local.replace(hour=start_clock.hour, minute=start_clock.minute, second=0, microsecond=0)
    end_dt = ref_local.replace(hour=end_clock.hour, minute=end_clock.minute, second=0, microsecond=0)

    if start_dt < ref_local - timedelta(hours=2):
        start_dt += timedelta(days=1)
    if end_dt < ref_local - timedelta(hours=2):
        end_dt += timedelta(days=1)
    if end_dt < start_dt:
        end_dt += timedelta(days=1)

    return start_dt, end_dt


async def _extract_eta_from_dom_fallback_v2(page: Page) -> EtaResult:
    body = ""
    try:
        body = await page.inner_text("body", timeout=2500)
    except Exception:  # noqa: BLE001
        body = ""

    if body:
        range_match = re.search(r"(\d{1,3})\s*[-\u2013]\s*(\d{1,3})\s*min", body, flags=re.IGNORECASE)
        if range_match:
            low = float(range_match.group(1))
            high = float(range_match.group(2))
            if low > 0 and high > 0:
                return EtaResult(
                    eta_min_minutes=low,
                    eta_max_minutes=high,
                    eta_avg_minutes=round((low + high) / 2.0, 2),
                    eta_source="dom:range",
                    eta_range_text_raw=range_match.group(0),
                )
        single = re.search(r"(\d{1,3})\s*min", body, flags=re.IGNORECASE)
        if single:
            val = float(single.group(1))
            if val > 0:
                return EtaResult(
                    eta_min_minutes=val,
                    eta_max_minutes=val,
                    eta_avg_minutes=val,
                    eta_source="dom:single",
                    eta_range_text_raw=single.group(0),
                )
    return EtaResult()
