import os
import time
import math
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests

# === Renderのポート検知をクリア＆501エラー解消用ダミーサーバー ===
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        return  # ログをキレイに保つため無効化

def start_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=start_dummy_server, daemon=True).start()

# === 設定項目 ===
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
FLIGHTAWARE_API_KEY = os.environ.get("FLIGHTAWARE_API_KEY")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "60"))

JAPAN_AIRPORT_PREFIXES = ("RJ", "RO")

# 監視対象の指定機種
TARGET_TYPES = [
    "kc-135", "kc135",
    "rc-135", "rc135", "rc-135u", "rc135u",
    "ec-135", "ec135",
    "wc-135", "wc135",
    "e-3", "e3", "e-3g", "e3g", "e-3b", "e3b", "e-3a", "e3a", "e-3c", "e3c", "e-3d", "e3d",
    "e-4a", "e4a", "e-4b", "e4b", "e-4c", "e4c",
    "vc-25a", "vc25a", "vc-25b", "vc25b"
]

if not DISCORD_WEBHOOK_URL:
    raise ValueError("エラー: DISCORD_WEBHOOK_URL が設定されていません。")

# 機体の状態管理（前回高度情報＆通知済みリスト）
in_air_states = {}
notified_icaos = set()


def is_target_aircraft(ac):
    """指定機種に該当するか判定"""
    ac_type = str(ac.get("t", "")).strip().lower().replace(" ", "")
    desc = str(ac.get("desc", "")).strip().lower().replace(" ", "")

    for target in TARGET_TYPES:
        target_clean = target.replace("-", "")
        if (target in ac_type or target_clean in ac_type or
            target in desc or target_clean in desc):
            return True
    return False


def get_nearest_airport(lat, lon):
    """緯度・経度から最寄りの空港/基地または主要エリアを取得"""
    if lat is None or lon is None:
        return "不明"
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=10"
        headers = {"User-Agent": "ADSB-Military-Tracker/1.0"}
        res = requests.get(url, headers=headers, timeout=5).json()
        
        address = res.get("address", {})
        aeroway = address.get("aeroway") or address.get("military")
        
        if aeroway:
            return str(aeroway)
        
        location_name = (address.get("aerodrome") or 
                         address.get("city") or 
                         address.get("town") or 
                         address.get("county") or 
                         address.get("state") or "付近")
        return f"{location_name} 周辺"
    except Exception:
        return f"座標 ({round(lat, 2)}, {round(lon, 2)})"


def get_direction_text(track):
    """方位角（0〜360度）を16方位に変換"""
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


def send_discord_notification(icao, tail, flight, ac_type, alt, track, origin, destination, event_type="検知"):
    flight_str = flight if flight else "不明"
    tail_str = tail if tail else "不明"
    type_str = ac_type if ac_type else "対象米軍機"
    direction_str = get_direction_text(track)
    is_japan_airport = is_destination_japan_airport(destination)

    if is_japan_airport:
        content_text = f"🚨 **【重要】{type_str} ({tail_str}) の目的地が「日本の空港 ({destination})」に設定されました！** @everyone"
        embed_color = 15158332  # 赤色
    else:
        content_text = f"✈️ **【特定機種{event_type}】{type_str}: {tail_str}**"
        embed_color = 3066993   # 緑色

    payload = {
        "content": content_text,
        "embeds": [
            {
                "title": f"✈️ {type_str} 飛行ステータス詳細 ({event_type})",
                "color": embed_color,
                "fields": [
                    {"name": "機体型式 (Type)", "value": type_str, "inline": True},
                    {"name": "機体番号 (Tail / Reg)", "value": tail_str, "inline": True},
                    {"name": "フライト番号 (Callsign)", "value": flight_str, "inline": True},
                    {"name": "ICAOコード", "value": icao.upper(), "inline": True},
                    {"name": "高度", "value": f"{alt} ft" if isinstance(alt, (int, float)) else str(alt), "inline": True},
                    {"name": "🧭 進行方位（向き）", "value": direction_str, "inline": True},
                    {"name": "🛫 出発地（最寄り）", "value": origin, "inline": True},
                    {"name": "🛬 目的地", "value": destination, "inline": True},
                ],
                "footer": {"text": "ADSB Military Tracker"}
            }
        ]
    }
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        print(f"[{time.strftime('%H:%M:%S')}] Discord通知完了({event_type}): {type_str} {tail_str} ({flight_str})")
    except Exception as e:
        print(f"送信エラー: {e}")


def check_military_takeoff():
    global in_air_states, notified_icaos

    url = "https://api.adsb.lol/v2/mil"
    try:
        res = requests.get(url, timeout=15).json()
        ac_list = res.get("ac", [])
        if not ac_list:
            return

        current_batch_icaos = set()

        for ac in ac_list:
            if not is_target_aircraft(ac):
                continue

            icao = ac.get("hex", "").strip()
            if not icao:
                continue

            current_batch_icaos.add(icao)

            tail = ac.get("r", "N/A").strip()
            flight = ac.get("flight", "N/A").strip()
            ac_type = ac.get("t", ac.get("desc", "不明")).strip()
            alt = ac.get("alt_baro")
            track = ac.get("track")
            lat = ac.get("lat")
            lon = ac.get("lon")

            is_ground = (alt == "ground") or (isinstance(alt, (int, float)) and alt < 100)
            is_in_air_current = not is_ground
            is_in_air_last = in_air_states.get(icao)

            # --- 条件1: 地上 -> 飛行中 に変化した瞬間（離陸検知） ---
            is_takeoff = (is_in_air_last is False and is_in_air_current is True)
            
            # --- 条件2: ADSB電波に新しく出現した（初検知） ---
            is_new_detection = (icao not in notified_icaos and is_in_air_current)

            if is_takeoff or is_new_detection:
                event_type = "離陸" if is_takeoff else "検知"
                print(f"【{event_type}】 機種: {ac_type}, Tail: {tail}, Flight: {flight}")
                
                fa_origin, destination = get_flight_route(flight)
                origin = fa_origin if fa_origin != "不明" else get_nearest_airport(lat, lon)
                
                send_discord_notification(icao, tail, flight, ac_type, alt, track, origin, destination, event_type)
                
                # 重複通知防止フラグを立てる
                notified_icaos.add(icao)

            # 状態更新
            in_air_states[icao] = is_in_air_current

        # ADSB受信圏外（着陸・見失った）になった機体は通知済みセットから消去し、次回離陸時に再反応できるようにする
        notified_icaos = notified_icaos.intersection(current_batch_icaos)

    except Exception as e:
        print(f"チェック中エラー: {e}")


if __name__ == "__main__":
    print("指定機種（KC-135, RC-135系, EC-135, WC-135, E-3系, E-4系, VC-25系）の監視を開始しました...")
    while True:
        check_military_takeoff()
        time.sleep(CHECK_INTERVAL)
