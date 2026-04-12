from __future__ import annotations


def build_selectors(restaurant: str, product: str) -> dict[str, list[str]]:
    return {
        "cart_open": ['button[data-test-id="view-carts-btn"]'],
        "cart_badge": ['[data-testid="view-carts-badge"]'],
        "cart_root": ['div[data-test="cart"]'],
        "cart_remove": ['button[data-test="item-stepper-dec"]'],
        "edit_location": ['a[data-testid="edit-delivery-location-button"]'],
        "location_input": ['input[data-testid="location-typeahead-input"]'],
        "location_suggestion": [
            '[data-testid="location-typeahead"] li button',
            '[data-testid="location-typeahead"] li',
            'li[role="option"]',
        ],
        "location_skip": [
            'button[aria-label="Omitir"]',
            'button[data-baseweb="button"]:has-text("Omitir")',
            'button:has-text("Omitir")',
        ],
        "location_save": [
            'button[data-test="save-address"]',
            'button[data-baseweb="button"]:has-text("Guardar")',
            'button:has-text("Guardar")',
        ],
        "global_search_input": ['input[data-testid="search-input"]'],
        "search_results_ready": ["text=/Resultado superior/i", "main a[href*='/store/']", "main h3"],
        "restaurant_fallback": [
            "xpath=(//*[contains(translate(normalize-space(.), 'RESULTADO SUPERIOR', 'resultado superior'), 'resultado superior')]/following::h3[1])[1]",
            "xpath=(//*[contains(translate(normalize-space(.), 'RESULTADO SUPERIOR', 'resultado superior'), 'resultado superior')]/following::a[1])[1]",
            "main h3",
            "main a[href*='/store/']",
        ],
        "store_search_input": [f'input[placeholder*="Buscar en {restaurant}"]', 'input[placeholder*="Buscar en"]'],
        "confirm_add": ['button[data-testid="add-to-cart-button"]'],
        "go_checkout": ['a[data-testid="go-to-checkout-button"]'],
        "skip_upsell": ['button[data-test="skip-upsell"]'],
        "checkout_marker": [
            "text=/metodo de pago|método de pago/i",
            "text=/resumen|pedido/i",
            "text=/total/i",
            "text=/direccion de entrega|dirección de entrega/i",
        ],
        # TODO validate "Resultado superior" variants across all accounts.
        # TODO validate location suggestion list variants in all UI experiments.
        # TODO validate checkout marker variants by locale.
    }
