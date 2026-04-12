from __future__ import annotations

from pathlib import Path


PLATFORM = "rappi"
RESTAURANT = "McDonald"

PRODUCTS = [
    "McFlurry Oreo",
    "Cuarto De Libra con Queso",
    "Big Mac"
]

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

START_URL = "https://www.rappi.com.mx/"
STORAGE_STATE = Path("auth/state_rappi.json")
SCREENSHOT_DIR = Path("data/screenshots")
NETWORK_LOG_FILE = Path("data/screenshots/checkout_network_trace.jsonl")
RESULT_FILE = Path("data/screenshots/checkout_result.json")

TIMEOUT_MS = 45000
INCLUDE_BODY = False
BODY_MAX_CHARS = 1500
MAX_CONCURRENCY = 1

