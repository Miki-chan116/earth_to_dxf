"""Address geocoding helpers for Geolonia Community Geocoder data."""

from datetime import datetime
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from src.gsi_tile import create_ssl_context, save_gsi_settings, validate_gsi_settings


GEOLONIA_ADDRESS_API_BASE = "https://japanese-addresses-v2.geoloniamaps.com/api/ja"
GEOCODER_TIMEOUT_SECONDS = 20
KANJI_NUMERALS = {
    0: "〇",
    1: "一",
    2: "二",
    3: "三",
    4: "四",
    5: "五",
    6: "六",
    7: "七",
    8: "八",
    9: "九",
    10: "十",
}


class GeocodingError(Exception):
    """Raised when an address cannot be geocoded."""


def fetch_json(url):
    """Fetch JSON from Geolonia's public address API."""

    request = Request(
        url,
        headers={
            "User-Agent": "earth_to_dxf/1.0 (+https://community-geocoder.geolonia.com/)",
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(
            request,
            timeout=GEOCODER_TIMEOUT_SECONDS,
            context=create_ssl_context(),
        ) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise GeocodingError(f"住所検索の通信に失敗しました: {error}") from error


def normalize_address_text(text):
    """Normalize small address differences for prefix matching."""

    return (
        str(text)
        .strip()
        .replace(" ", "")
        .replace("　", "")
        .replace("ヶ", "ケ")
        .replace("が", "ケ")
        .replace("ノ", "之")
        .replace("の", "之")
    )


def number_to_kanji(number):
    """Convert a small Arabic number to Japanese numerals for 丁目 matching."""

    value = int(number)
    if value <= 10:
        return KANJI_NUMERALS[value]
    if value < 20:
        return "十" + KANJI_NUMERALS[value - 10]
    if value < 100:
        tens, ones = divmod(value, 10)
        result = KANJI_NUMERALS[tens] + "十"
        if ones:
            result += KANJI_NUMERALS[ones]
        return result
    return str(value)


def expand_chome_variants(name):
    """Return common Arabic/Kanji variants for a town name."""

    variants = {name}
    for number in range(1, 100):
        kanji = number_to_kanji(number)
        variants.add(name.replace(f"{kanji}丁目", f"{number}丁目"))
        variants.add(name.replace(f"{number}丁目", f"{kanji}丁目"))
    return variants


def city_display_name(city):
    """Return the endpoint/display city name used by the address API."""

    city_name = city.get("city", "")
    ward_name = city.get("ward", "")
    return f"{city_name}{ward_name}" if ward_name else city_name


def find_prefecture(address, prefectures):
    """Find the prefecture from an address prefix."""

    normalized = normalize_address_text(address)
    for pref in prefectures:
        pref_name = pref.get("pref", "")
        if normalized.startswith(normalize_address_text(pref_name)):
            return pref, normalized[len(normalize_address_text(pref_name)) :]
    return None, normalized


def find_city(rest_address, cities):
    """Find the longest matching city/ward prefix."""

    matches = []
    for city in cities:
        name = city_display_name(city)
        normalized_name = normalize_address_text(name)
        if rest_address.startswith(normalized_name):
            matches.append((len(normalized_name), city, rest_address[len(normalized_name) :]))

    if not matches:
        return None, rest_address

    matches.sort(key=lambda item: item[0], reverse=True)
    _, city, remaining = matches[0]
    return city, remaining


def town_names(town):
    """Return searchable names for a town/chome record."""

    oaza = town.get("oaza_cho", "") or ""
    chome = town.get("chome", "") or ""
    chome_number = town.get("chome_n")
    names = []

    if oaza and chome:
        names.append(f"{oaza}{chome}")
    if oaza:
        names.append(oaza)

    if oaza and isinstance(chome_number, int):
        names.append(f"{oaza}{chome_number}")
        names.append(f"{oaza}{chome_number}丁目")

    variants = set()
    for name in names:
        variants.update(expand_chome_variants(name))
    return variants


def town_display_name(town):
    """Return the canonical town/chome name from the API record."""

    oaza = town.get("oaza_cho", "") or ""
    chome = town.get("chome", "") or ""
    return f"{oaza}{chome}" if chome else oaza


def find_town(rest_address, towns):
    """Find the longest matching town prefix."""

    matches = []
    for town in towns:
        point = town.get("point")
        if not isinstance(point, list) or len(point) < 2:
            continue

        for name in town_names(town):
            normalized_name = normalize_address_text(name)
            if normalized_name and rest_address.startswith(normalized_name):
                matches.append((len(normalized_name), town, name))

    if not matches:
        return None, ""

    matches.sort(key=lambda item: item[0], reverse=True)
    _, town, _ = matches[0]
    return town, town_display_name(town)


def parse_geocoder_response(response):
    """Normalize Geolonia Community Geocoder style responses."""

    if not isinstance(response, dict):
        raise GeocodingError("住所検索結果が不正です")

    latitude = response.get("lat")
    longitude = response.get("lng")

    if latitude is None or longitude is None:
        point = response.get("point")
        if isinstance(point, dict):
            latitude = point.get("lat")
            longitude = point.get("lng")
        elif isinstance(point, list) and len(point) >= 2:
            longitude, latitude = point[0], point[1]

    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError) as error:
        raise GeocodingError("住所検索結果に緯度経度がありません") from error

    if latitude == 0 and longitude == 0:
        raise GeocodingError("住所検索結果に有効な緯度経度がありません")

    pref = response.get("pref", "") or ""
    city = response.get("city", "") or ""
    town = response.get("town", "") or response.get("oaza_cho", "") or ""
    address = response.get("address", "") or "".join([pref, city, town])

    return {
        "address": address,
        "display_name": response.get("display_name") or address,
        "latitude": latitude,
        "longitude": longitude,
        "level": response.get("level"),
        "source": "geolonia_community_geocoder",
        "raw": response,
    }


def geocode_address(address):
    """Geocode an address using Geolonia Community Geocoder public data."""

    clean_address = str(address).strip()
    if not clean_address:
        raise GeocodingError("住所を入力してください")

    prefectures_response = fetch_json(f"{GEOLONIA_ADDRESS_API_BASE}.json")
    prefectures = prefectures_response.get("data", [])
    pref, rest = find_prefecture(clean_address, prefectures)
    if pref is None:
        raise GeocodingError("住所から都道府県を判定できませんでした")

    city, rest = find_city(rest, pref.get("cities", []))
    if city is None:
        point = pref.get("point")
        return parse_geocoder_response(
            {
                "address": clean_address,
                "display_name": pref.get("pref", clean_address),
                "pref": pref.get("pref"),
                "level": 1,
                "point": point,
            }
        )

    pref_name = pref.get("pref", "")
    city_name = city_display_name(city)
    city_url = (
        f"{GEOLONIA_ADDRESS_API_BASE}/"
        f"{quote(pref_name)}/{quote(city_name)}.json"
    )
    town_response = fetch_json(city_url)
    town, town_name = find_town(rest, town_response.get("data", []))

    if town is None:
        point = city.get("point")
        display_name = f"{pref_name}{city_name}"
        return parse_geocoder_response(
            {
                "address": clean_address,
                "display_name": display_name,
                "pref": pref_name,
                "city": city_name,
                "level": 2,
                "point": point,
            }
        )

    display_name = f"{pref_name}{city_name}{town_name}"
    return parse_geocoder_response(
        {
            "address": clean_address,
            "display_name": display_name,
            "pref": pref_name,
            "city": city_name,
            "town": town_name,
            "level": 3,
            "point": town.get("point"),
            "raw_town": town,
        }
    )


def update_gsi_settings_from_address(address, geocode_result, current_settings, path=None):
    """Save gsi_settings.json with geocoded lat/lon and current tile settings."""

    settings = validate_gsi_settings(
        geocode_result["latitude"],
        geocode_result["longitude"],
        current_settings.get("zoom"),
        current_settings.get("grid_size"),
        current_settings.get("tile_type"),
    )
    settings["address"] = address
    settings["display_name"] = geocode_result.get("display_name") or address
    settings["updated_at"] = datetime.now().isoformat(timespec="seconds")
    if path is None:
        return save_gsi_settings(settings)
    return save_gsi_settings(settings, path=path)
