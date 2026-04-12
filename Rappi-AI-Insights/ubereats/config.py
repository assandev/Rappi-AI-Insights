from __future__ import annotations

from pathlib import Path


PLATFORM = "ubereats"
RESTAURANT = "McDonald's"

PRODUCTS = [
    "Cuarto De Libra con Queso",
    "McFlurry Oreo",
    'Big Mac'
]

# Same scope currently used for MVP runs.
ADDRESSES = [
    {
        "address_id": 1,
        "zone_type": "high_income",
        "address_text": "Avenida Campos Elíseos 470,  Lomas de Chapultepec I Sección,  11000 Miguel Hidalgo, CDMX,  México",
    },
    {
        "address_id": 2,
        "zone_type": "corporate",
        "address_text": "Paseo de la Reforma 222,  Juárez,  06600 Cuauhtémoc, CDMX,  México",
    },
    {
        "address_id": 3,
        "zone_type": "middle_class",
        "address_text": "Avenida División del Norte 1300,  Letrán Valle,  03650 Benito Juárez, CDMX,  México",
    },
    {
        "address_id": 4,
        "zone_type": "student",
        "address_text": "Avenida Universidad 3000,  Ciudad Universitaria,  04510 Coyoacán, CDMX,  México",
    },
    {
        "address_id": 5,
        "zone_type": "tourist",
        "address_text": "Avenida Álvaro Obregón 100,  Roma Norte,  06700 Cuauhtémoc, CDMX,  México",
    },
    {
        "address_id": 6,
        "zone_type": "high_density",
        "address_text": "Calzada General Ignacio Zaragoza 1298,  Juan Escutia,  09100 Iztapalapa, CDMX,  México",
    }
]

START_URL = "https://www.ubereats.com/mx"
STORAGE_STATE = Path("auth/state_ubereats.json")
DATA_ROOT = Path("data/ubereats")
SCREENSHOT_DIR = DATA_ROOT / "screenshots"
NETWORK_LOG_FILE = DATA_ROOT / "network" / "checkout_network_trace_ubereats.jsonl"
RESULT_FILE = DATA_ROOT / "results" / "checkout_result_ubereats.json"

TIMEOUT_MS = 45000
INCLUDE_BODY = False
BODY_MAX_CHARS = 1500
MAX_CONCURRENCY = 1

# Optional forensic debug mode (safe defaults for normal runs).
DEBUG_CHECKOUT_MODE = False
DEBUG_MAX_NETWORK_BODIES = 40
DEBUG_CHECKOUT_DIR = Path("debug/ubereats_checkout")
