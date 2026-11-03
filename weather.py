"""Weather lookup helpers used by the Vidmahi assistant."""

import requests


def get_weather(city: str) -> str:
    geocoding = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city, "count": 1, "language": "en", "format": "json"},
        timeout=8,
    )
    geocoding.raise_for_status()
    locations = geocoding.json().get("results") or []
    if not locations:
        return f"I couldn't find {city}. Please check the city name."
    location = locations[0]
    forecast = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "current": "temperature_2m,wind_speed_10m",
        },
        timeout=8,
    )
    forecast.raise_for_status()
    current = forecast.json().get("current")
    if not current:
        raise RuntimeError("The weather service did not return current conditions.")
    return (
        f"Current weather in {location['name']} is "
        f"{current['temperature_2m']} degrees Celsius with wind speed "
        f"{current['wind_speed_10m']} kilometers per hour."
    )
