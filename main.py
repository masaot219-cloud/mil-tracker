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

# 1. 許可する指定運用者（USAF, US Navy, USMC, Omega Tanker）
ALLOWED_OPERATORS = [
    "us air force", "usaf", "united states air force",
    "us navy", "usn", "united states navy",
    "us marine corps", "usmc", "united states marine corps",
    "omega air", "omega aerial refueling", "omega tanker"
]

# 2. 監視対象の指定機種
TARGET_TYPES = [
    # C-135 / RC-135 / KC-135 派生
    "kc-135", "kc135",
    "rc-135", "rc135", "rc-135u", "rc135u",
    "ec-135", "ec135",
    "wc-135", "wc135",
    
    # E-3 (AWACS) 関連
    "e-3a", "e3a", "e-3b", "e3b", "e-3c", "e3c", "e-3d", "e3d", "e-3g", "e3g", "e-3tf", "e3tf", "sentry",
    
    # E-4 / VC-25
    "e-4a", "e4a", "e-4b", "e4b", "e-4c", "e4c",
    "vc-25a", "vc25a", "vc-25b", "vc25b",

    # Boeing 707 全般 & 派生機 (B707, B703, E-6, E-8, C-137 等)
    "b707", "b-707", "b703", "b-703", "boeing707", "boeing 707",
    "e-6", "e6", "e-8", "e8", "c-137", "c137",

    # KDC-10 / DC-10 タンク機
    "kdc-10", "kdc10", "dc-10", "dc10", "kdc103"
]

# 3. 明確に除外したい機種（E390やヘリコプター全般）
EXCLUDE_TYPES = [
    "e390", "e-390", "kc390", "kc-390", "c390", "c-390",
    "h60", "h-60", "mh60", "mh-60", "sh60", "sh-60", "hh60", "hh-60", "uh60", "uh-60",
    "blackhawk", "seahawk", "jayhawk", "knighthawk"
]

if not DISCORD_WEBHOOK_URL:
    raise ValueError("エラー: DISCORD_WEBHOOK_URL が設定されていません。")

# 機体の状態管理（前回高度情報＆通知済みリスト）
in_air_states = {}
notified_icaos = set()


def is_target_aircraft(ac):
    """USAF / US Navy / USMC / Omega Air かつ指定機種のみに判定を絞り込み"""
    ac_type = str(ac.get("t", "")).strip().lower().replace(" ", "")
    desc = str(ac.get("desc", "")).strip().lower().replace(" ", "")
    own_op = str(ac.get("ownOp", "")).strip().lower()

    # 1. 除外対象のチェック
    for exclude in EXCLUDE_TYPES:
        exclude_clean = exclude.replace("-", "")
        if (exclude in ac_type or exclude_clean in ac_type or
            exclude in desc or exclude_clean in desc):
            return False

    # 2. 運用者（Operator）のチェック
    is_allowed_op = False
    if own_op:
        for op in ALLOWED_OPERATORS:
            if op in own_op:
                is_allowed_op = True
                break
    else:
        is_allowed_op = True

    if not is_allowed_op:
        return False

    # 3. 監視対象機種のチェック
    for target in TARGET_TYPES:
        target_clean = target.replace("-", "")
        if (target in ac_type or target_clean in ac_type or
            target in desc or target_clean in desc):
            return True

    return False


def get_location_name(lat, lon):
    """緯度・経度から地名・施設名を取得し、座標情報も併記して返す"""
    if lat is None or lon is None:
        return "位置情報なし"
    
    coord_str = f"({round(lat, 4)}, {round(lon, 4)})"
    
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=10"
        headers = {"User-Agent": "ADSB-Military-Tracker/1.0"}
        res = requests.get(url, headers=headers, timeout=5).json()
        
        address = res.get("address", {})
        aeroway = address.get("aeroway") or address.get("military")
        
        if aeroway:
            return f"{aeroway} 周辺 {coord_str}"
        
        location_name = (address.get("aerodrome") or 
                         address.get("city") or 
                         address.get("town") or 
                         address.get("county") or 
                         address.get("state") or "")
        
        if location_name:
            return f"{location_name} 上空/周辺 {coord_str}"
        return f"座標 {coord_str}"
    except Exception:
        return f"座標 {coord_str}"


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


def send_discord_notification(icao, tail, flight, ac_type, own_op, alt, track, origin, destination, location_str, event_type="検知"):
    flight_str = flight if flight else "不明"
    tail_str = tail if tail else "不明"
    type_str = ac_type if ac_type else "対象機"
    op_str = own_op if own_op else "米軍/関連機関"
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
                    {"name": "所属/運用者 (Operator)", "value": op_str, "inline": True},
                    {"name": "機体番号 (Tail / Reg)", "value": tail_str, "inline": True},
                    {"name": "フライト番号 (Callsign)", "value": flight_str, "inline": True},
                    {"name": "ICAOコード", "value": icao.upper(), "inline": True},
                    {"name": "高度", "value": f"{alt} ft" if isinstance(alt, (int, float)) else str(alt), "inline": True},
                    {"name": "🧭 進行方位（向き）", "value": direction_str, "inline": True},
                    {"name": "📍 反応位置（地名＆座標）", "value": location_str, "inline": False},
                    {"name": "🛫 出発地", "value": origin, "inline": True},
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
            own_op = ac.get("ownOp", "不明").strip()
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
                
                location_str = get_location_name(lat, lon)
                fa_origin, destination = get_flight_route(flight)
                origin = fa_origin if fa_origin != "不明" else location_str
                
                send_discord_notification(icao, tail, flight, ac_type, own_op, alt, track, origin, destination, location_str, event_type)
                
                # 重複通知防止フラグを立てる
                notified_icaos.add(icao)

            # 状態更新
            in_air_states[icao] = is_in_air_current

        # ADSB受信圏外（着陸・見失った）になった機体は通知済みセットから消去
        notified_icaos = notified_icaos.intersection(current_batch_icaos)

    except Exception as e:
        print(f"チェック中エラー: {e}")


if __name__ == "__main__":
    print("指定運用者の監視を開始しました...")
    while True:
        check_military_takeoff()
        time.sleep(CHECK_INTERVAL)
