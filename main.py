import os
import time
import requests

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
FLIGHTAWARE_API_KEY = os.environ.get("FLIGHTAWARE_API_KEY")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "60"))

JAPAN_AIRPORT_PREFIXES = ("RJ", "RO")

if not DISCORD_WEBHOOK_URL:
    raise ValueError("エラー: DISCORD_WEBHOOK_URL が設定されていません。")

in_air_states = {}


def get_direction_text(track):
    """方位角（0〜360度）を16方位（北・東・南西など）に変換"""
    if track is None or not isinstance(track, (int, float)):
        return "不明"
    
    directions = [
        "北 (N)", "北北東 (NNE)", "北東 (NE)", "東北東 (ENE)",
        "東 (E)", "東南東 (ESE)", "南東 (SE)", "南南東 (SSE)",
        "南 (S)", "南南西 (SSW)", "南西 (SW)", "西南西 (WSW)",
        "西 (W)", "西北西 (WNW)", "北西 (NW)", "北北西 (NNW)"
    ]
    idx = int((track + 11.25) / 22.5) % 16
    return f"{directions[idx]} ({int(track)}°)"


def is_destination_japan_airport(destination_str):
    if not destination_str or destination_str in ["不明", "N/A"]:
        return False
    dest_clean = destination_str.strip().upper()
    return dest_clean.startswith(JAPAN_AIRPORT_PREFIXES)


def get_flight_route(flight_number):
    if not FLIGHTAWARE_API_KEY or not flight_number or flight_number == "N/A":
        return "不明", "不明"

    url = f"https://aeroapi.flightaware.com/aeroapi/flights/{flight_number.strip()}"
    headers = {"x-apikey": FLIGHTAWARE_API_KEY}

    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            flights = data.get("flights", [])
            if flights:
                latest = flights[0]
                origin = (latest.get("origin") or {}).get("code") or "不明"
                destination = (latest.get("destination") or {}).get("code") or "不明"
                return origin, destination
    except Exception as e:
        print(f"目的地取得エラー: {e}")

    return "不明", "不明"


def send_discord_notification(icao, tail, flight, alt, track, origin, destination):
    flight_str = flight if flight else "不明"
    tail_str = tail if tail else "不明"
    direction_str = get_direction_text(track)
    is_japan_airport = is_destination_japan_airport(destination)

    if is_japan_airport:
        content_text = f"🚨 **【重要】米軍機 ({tail_str}) の目的地が「日本の空港 ({destination})」に設定されました！** @everyone"
        embed_color = 15158332  # 赤色
    else:
        content_text = f"✈️ **米軍機の離陸を検知: {tail_str}**"
        embed_color = 3066993   # 緑色

    payload = {
        "content": content_text,
        "embeds": [
            {
                "title": "✈️ 米軍機 離陸ステータス詳細",
                "color": embed_color,
                "fields": [
                    {"name": "機体番号 (Tail / Reg)", "value": tail_str, "inline": True},
                    {"name": "フライト番号 (Callsign)", "value": flight_str, "inline": True},
                    {"name": "ICAOコード", "value": icao.upper(), "inline": True},
                    {"name": "高度", "value": f"{alt} ft", "inline": True},
                    {"name": "🧭 進行方位（向き）", "value": direction_str, "inline": True},
                    {"name": "🛫 出発地", "value": origin, "inline": True},
                    {"name": "🛬 目的地", "value": destination, "inline": True},
                ],
                "footer": {"text": "ADSB Military Tracker"}
            }
        ]
    }
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        print(f"[{time.strftime('%H:%M:%S')}] Discord通知完了: {tail_str} ({flight_str})")
    except Exception as e:
        print(f"送信エラー: {e}")


def check_military_takeoff():
    global in_air_states

    url = "https://api.adsb.lol/v2/mil"
    try:
        res = requests.get(url, timeout=15).json()
        ac_list = res.get("ac", [])
        if not ac_list:
            print(f"[{time.strftime('%H:%M:%S')}] 米軍機データなし")
            return

        for ac in ac_list:
            icao = ac.get("hex", "").strip()
            if not icao:
                continue

            tail = ac.get("r", "N/A").strip()
            flight = ac.get("flight", "N/A").strip()
            alt = ac.get("alt_baro")
            track = ac.get("track")

            is_ground = (alt == "ground") or (isinstance(alt, (int, float)) and alt < 100)
            is_in_air_current = not is_ground

            is_in_air_last = in_air_states.get(icao)

            if is_in_air_last is False and is_in_air_current is True:
                print(f"離陸検知！ Tail: {tail}, Flight: {flight}")
                origin, destination = get_flight_route(flight)
                send_discord_notification(icao, tail, flight, alt, track, origin, destination)

            in_air_states[icao] = is_in_air_current

    except Exception as e:
        print(f"チェック中エラー: {e}")


if __name__ == "__main__":
    print("米軍機（ミリタリー機）の常時監視を開始しました...")
    while True:
        check_military_takeoff()
        time.sleep(CHECK_INTERVAL)
