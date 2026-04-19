from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode
import re

import httpx

from ..config import get_settings


@dataclass
class WeatherResult:
    location: str
    observed_at: str | None
    description: str
    temperature_c: str
    feels_like_c: str
    humidity: str
    wind_kmph: str
    max_temp_c: str
    min_temp_c: str
    source_url: str


class WeatherService:
    _WEATHER_CODE_MAP: dict[int, str] = {
        0: "晴",
        1: "大体晴",
        2: "局部多云",
        3: "阴",
        45: "雾",
        48: "冻雾",
        51: "小毛毛雨",
        53: "毛毛雨",
        55: "强毛毛雨",
        56: "冻毛毛雨",
        57: "强冻毛毛雨",
        61: "小雨",
        63: "中雨",
        65: "大雨",
        66: "冻雨",
        67: "强冻雨",
        71: "小雪",
        73: "中雪",
        75: "大雪",
        77: "雪粒",
        80: "小阵雨",
        81: "中阵雨",
        82: "强阵雨",
        85: "小阵雪",
        86: "强阵雪",
        95: "雷暴",
        96: "雷暴伴小冰雹",
        99: "雷暴伴强冰雹",
    }

    _GENERIC_LOCATIONS = {
        "今天",
        "今日",
        "现在",
        "目前",
        "本地",
        "当地",
        "这里",
        "这儿",
    }

    def __init__(self) -> None:
        settings = get_settings()
        self.timeout_sec = settings.web_search_timeout_sec

    def is_weather_query(self, text: str) -> bool:
        lower = (text or "").lower()
        return any(
            k in lower
            for k in (
                "天气",
                "气温",
                "温度",
                "weather",
                "forecast",
            )
        )

    def extract_location(self, text: str) -> str | None:
        s = (text or "").strip()
        if not s:
            return None

        # English forms: "weather in Beijing", "Beijing weather"
        english_patterns = [
            r"(?:weather|forecast)\s+(?:in|for)\s+(?P<loc>[A-Za-z\s]{2,40})",
            r"(?P<loc>[A-Za-z\s]{2,40})\s+weather",
        ]
        for pattern in english_patterns:
            matched = re.search(pattern, s, flags=re.IGNORECASE)
            if not matched:
                continue
            loc = (matched.group("loc") or "").strip(" ,.?")
            loc = re.sub(
                r"\b(today|now|current|currently)\b$",
                "",
                loc,
                flags=re.IGNORECASE,
            ).strip()
            if loc:
                return loc

        # Chinese forms: "北京今天天气", "上海气温", "广州温度"
        matched = re.search(
            r"(?P<loc>[\u4e00-\u9fa5A-Za-z\s]{1,24})"
            r"(?:(?:今天|今日|现在|目前))?"
            r"(?:天气|气温|温度)",
            s,
        )
        if matched:
            loc = (matched.group("loc") or "").strip(" ,.?，。！？；")
            loc = re.sub(r"(今天|今日|现在|目前)$", "", loc).strip()
            if loc and loc not in self._GENERIC_LOCATIONS:
                return loc

        return None

    def extract_location_from_followup(self, text: str) -> str | None:
        """
        Parse location from short follow-up messages such as:
        - "北京"
        - "上海"
        - "Tokyo"
        - "在深圳"
        """
        s = (text or "").strip()
        if not s:
            return None

        # Remove common wrapper words.
        s = re.sub(
            r"^(在|是|查|帮我查|请查|查询|查一下|帮我查一下)\s*",
            "",
            s,
            flags=re.IGNORECASE,
        )
        s = re.sub(
            r"(天气|气温|温度|weather|forecast)$",
            "",
            s,
            flags=re.IGNORECASE,
        ).strip()
        s = s.strip(" ,.?，。！？；")

        if not s:
            return None
        if len(s) > 24:
            return None

        lower = s.lower()
        blocked_tokens = (
            "最新",
            "今天",
            "最近",
            "现在",
            "什么",
            "怎么",
            "why",
            "what",
            "how",
            "news",
        )
        if any(token in lower for token in blocked_tokens):
            return None

        return s

    def get_weather(self, location: str) -> WeatherResult:
        if not location.strip():
            raise RuntimeError("Location is empty.")

        geo = self._geocode_location(location.strip())
        lat = geo["latitude"]
        lon = geo["longitude"]

        weather_data = self._fetch_weather(lat, lon)
        current = weather_data.get("current", {}) or {}
        daily = weather_data.get("daily", {}) or {}

        weather_code = self._to_int(current.get("weather_code"))
        description = self._WEATHER_CODE_MAP.get(weather_code, "未知")

        max_temp = self._pick_daily_value(daily, "temperature_2m_max")
        min_temp = self._pick_daily_value(daily, "temperature_2m_min")

        source_url = (
            "https://api.open-meteo.com/v1/forecast?"
            + urlencode(
                {
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,apparent_temperature,wind_speed_10m,weather_code",
                    "daily": "temperature_2m_max,temperature_2m_min",
                    "timezone": "auto",
                }
            )
        )

        display_name = geo.get("display_name") or location.strip()

        return WeatherResult(
            location=display_name,
            observed_at=self._to_str(current.get("time")),
            description=description,
            temperature_c=self._to_str(current.get("temperature_2m")),
            feels_like_c=self._to_str(current.get("apparent_temperature")),
            humidity=self._to_str(current.get("relative_humidity_2m")),
            wind_kmph=self._to_str(current.get("wind_speed_10m")),
            max_temp_c=self._to_str(max_temp),
            min_temp_c=self._to_str(min_temp),
            source_url=source_url,
        )

    def _geocode_location(self, location: str) -> dict:
        endpoint = "https://geocoding-api.open-meteo.com/v1/search"
        params = {
            "name": location,
            "count": 1,
            "language": "zh",
            "format": "json",
        }
        try:
            with httpx.Client(timeout=self.timeout_sec) as client:
                response = client.get(endpoint, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Weather geocoding failed: {exc}") from exc

        data = response.json()
        results = data.get("results") or []
        if not results:
            raise RuntimeError("无法识别该城市，请换一个更明确的地点。")

        first = results[0]
        name = (first.get("name") or "").strip()
        admin1 = (first.get("admin1") or "").strip()
        country = (first.get("country") or "").strip()
        display_name = " / ".join([item for item in (name, admin1, country) if item])
        return {
            "latitude": first.get("latitude"),
            "longitude": first.get("longitude"),
            "display_name": display_name or location,
        }

    def _fetch_weather(self, latitude: float, longitude: float) -> dict:
        endpoint = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,wind_speed_10m,weather_code",
            "daily": "temperature_2m_max,temperature_2m_min",
            "timezone": "auto",
        }
        try:
            with httpx.Client(timeout=self.timeout_sec) as client:
                response = client.get(endpoint, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Weather request failed: {exc}") from exc
        return response.json()

    def _pick_daily_value(self, daily: dict, key: str):
        values = daily.get(key)
        if isinstance(values, list) and values:
            return values[0]
        return ""

    def _to_int(self, value) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return -1

    def _to_str(self, value) -> str:
        if value is None:
            return ""
        if isinstance(value, float):
            return f"{value:.1f}".rstrip("0").rstrip(".")
        return str(value)
