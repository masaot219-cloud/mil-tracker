import os
import time
from datetime import datetime
import requests

# === 設定項目 ===
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
FLIGHTAWARE_API_KEY = os.environ.get("FLIGHTAWARE_API_KEY")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "60"))

if not DISCORD_WEBHOOK_URL:
    raise ValueError("エラー: DISCORD_WEBHOOK_URL が設定されていないよ！")

# テスト済みかどうかのフラグ
test_notified = False

def send_test_notification():
    """1分後のテスト用通知"""
    payload = {
        "content": "✈️ **【1分後テスト通知】60秒の自動ループとDiscord送信が正常に機能しています！**",
        "embeds": [{
            "title": "✈️ テスト機体ステータス詳細",
            "color": 0xE67E22,
            "fields": [
                {"name": "機体型式 (Type)", "value": "KC135 (テスト)", "inline": True},
                {"name": "所属/運用者 (Operator)", "value": "US Air Force", "inline": True},
                {"name": "機体番号 (Tail / Reg)", "value": "TEST-1234", "inline": True},
                {"name": "フライト番号 (Callsign)", "value": "TEST01", "inline": True},
                {"name": "高度", "value": "25000 ft", "inline": True},
                {"name": "📍 反応位置", "value": "テスト上空 (35.6895, 139.6917)", "inline": False},
            ],
            "footer": {"text": "ADSB Military Tracker - 1分後テスト"}
        }]
    }
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        print(f"[{time.strftime('%H:%M:%S')}] 1分後テスト通知の送信成功！")
    except Exception as e:
        print(f"送信エラー: {e}")

def check_military_takeoff():
    global test_notified
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 定期チェック実行中...")

    # 【テスト用】まだテスト通知を送っていなければ、1回目のチェック（＝約1分後）で強制送信する
    if not test_notified:
        print("【テスト】強制的にテスト通知を配信します！")
        send_test_notification()
        test_notified = True
        return

    # 通常時の処理（実際のAPIチェック）
    url = "https://api.adsb.lol/v2/mil"
    try:
        res = requests.get(url, timeout=15).json()
        ac_list = res.get("ac", [])
        print(f"API接続成功: 現在空中の軍用機候補 {len(ac_list)} 機")
    except Exception as e:
        print(f"チェック中エラー: {e}")

if __name__ == "__main__":
    print(f"1分後テストプログラムを開始しました。約60秒後にDiscordへテスト通知が飛びます。")
    while True:
        time.sleep(CHECK_INTERVAL)  # 最初に60秒待つことで、まさに「1分後」に動く
        check_military_takeoff()
import os
import time
import math
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests

# === 1. Renderのポート検知対策＆スリープ防止用ダミーサーバー ===
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
        pass  # サーバーアクセスのログを非表示にしてすっきりさせる

def start_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

# バックグラウンドスレッドでダミーサーバーを常時稼働
threading.Thread(target=start_dummy_server, daemon=True).start()

# === 2. 設定項目 ===
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
FLIGHTAWARE_API_KEY = os.environ.get("FLIGHTAWARE_API_KEY")

CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "60"))

JAPAN_AIRPORT_PREFIXES = ("RJ", "RO")

# 許可する指定運用者（純米軍 ＋ オメガタンカーのみ）
ALLOWED_OPERATORS = [
    "us air force", "usaf", "united states air force",
    "us navy", "usn", "united states navy",
    "us marine corps", "usmc", "united states marine corps",
    "omega air", "omega aerial refueling", "omega tanker"
]

# 米軍特有の代表的コールサイン
TARGET_CALLSIGNS = [
    "sentry", "recon", "jake", "cobra", "snoop", "bolt", "pyton", "olay",
    "hobo", "gold", "ethyl", "teal", "qid", "m35", "boeing"
]

# 監視対象の機種コード（KC-135, E-3, E-4, VC-25, E-6, E-8, B-52 等）
TARGET_TYPES = [
    # KC-135 派生 (E135系は除外へ移行)
    "k35r", "k35q", "k35t", "kc35", "c135", "c35", "kc135", "kc-135", "nc135", "tc135",
    "r135", "rc13", "rc135", "rc-135",
    "w135", "wc135", "wc-135", "wc135w", "wc135c",
    "oc135", "oc-135",
    
    # E-3 (AWACS) 関連
    "e3tf", "e3cf", "e3a", "e3b", "e3c", "e3d", "e3g", "e3", "e-3", "sentry",
    
    # E-4 / VC-25 / E-6 / E-8 / その他大型
    "e4", "e4b", "e-4b", "vc25", "vc25a", "vc-25a", "vc25b", "vc-25b",
    "e6", "e-6", "e6b", "e-6b", "e8", "e-8", "e8c", "e-8c", "b52"
]

# 【要請対応】E135系、C-17、P-8、ヘリ等を完全に除外
EXCLUDE_TYPES = [
    "ec135", "ec-135",  # E135系を完全除外
    "c17", "c-17", "globemaster",
    "p8a", "p8", "p-8", "poseidon",
    "e390", "e-390", "kc390", "kc-390", "c390", "c-390",
    "h60", "h-60", "mh60", "mh-60", "sh60", "sh-60", "hh60", "hh-60", "uh60", "uh-60",
    "blackhawk", "seahawk", "jayhawk", "knighthawk"
]

if not DISCORD_WEBHOOK_URL:
    raise ValueError("エラー: DISCORD_WEBHOOK_URL が設定されていません。")

in_air_states = {}
notified_icaos = set()


def get_embed_color(ac_type, is_japan_destination):
    """機種や目的地に応じてEmbed通知の枠線の色を切り替える"""
    if is_japan_destination:
        return 0xFF0000  # 赤色（日本目的地・最高警戒）

    type_clean = ac_type.lower().replace(" ", "").replace("-", "")

    if any(k in type_clean for k in ["w135", "wc135", "r135", "rc13", "rc135", "oc135", "ep3"]):
        return 0x9B59B6

    if any(k in type_clean for k in ["e3", "sentry"]):
        return 0xF1C40F

    if any(k in type_clean for k in ["k35", "kc35", "kc135", "c135"]):
        return 0xE67E22

    if any(k in type_clean for k in ["e4", "vc25", "e6", "e8"]):
        return 0x900C3F

    return 0x3498DB


def is_target_aircraft(ac):
    """米軍・オメガタンカー専用の判定ロジック（E135 / C-17 / P-8 / National等除外）"""
    ac_type = str(ac.get("t", "")).strip().lower().replace(" ", "")
    desc = str(ac.get("desc", "")).strip().lower()
    own_op = str(ac.get("ownOp", "")).strip().lower()
    flight = str(ac.get("flight", "")).strip().lower()

    # 民間・チャーター・輸送系除外
    if "national air" in own_op or flight.startswith("ncr") or flight.startswith("rch") or flight.startswith("reach"):
        return False

    # 除外機種（E135系、C-17、P-8、ヘリ等）の強制ブロック
    for exclude in EXCLUDE_TYPES:
        exclude_clean = exclude.replace("-", "")
        if (exclude in ac_type or exclude_clean in ac_type or
            exclude in desc or exclude_clean in desc):
            return False

    # 運用者が米軍またはオメガ系であることの厳格チェック
    is_allowed_op = any(op in own_op for op in ALLOWED_OPERATORS)
    if not is_allowed_op:
        return False  # 所属が米軍・オメガ以外ならここで確実に弾く

    is_target_callsign = any(cs in flight for cs in TARGET_CALLSIGNS)

    desc_clean = desc.replace(" ", "").replace("-", "")
    is_target_type = False

    for target in TARGET_TYPES:
        target_clean = target.replace("-", "")
        if (target == ac_type or target_clean == ac_type or
            target in desc or target_clean in desc_clean):
            is_target_type = True
            break

    if not is_target_type:
        if any(k in ac_type or k in desc for k in ["k35", "w135", "sentry", "e-3", "e-4", "e-6", "e-8"]):
            if not any(ex in ac_type or ex in desc for ex in ["c17", "p8", "ec135"]):
                is_target_type = True

    if is_target_callsign or is_target_type:
        return True

    return False


def get_location_name(lat, lon):
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
    op_str = own_op if own_op else "米軍/オメガ"
    direction_str = get_direction_text(track)
    is_japan_airport = is_destination_japan_airport(destination)

    embed_color = get_embed_color(type_str, is_japan_airport)

    if is_japan_airport:
        content_text = f"🚨 **【重要】{type_str} ({tail_str}) の目的地が「日本の空港 ({destination})」に設定されました！** @everyone"
    else:
        content_text = f"✈️ **【米軍機/オメガ{event_type}】{type_str}: {tail_str} (Callsign: {flight_str})**"

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

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 米軍・オメガ機を自動チェック中...")

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

            is_takeoff = (is_in_air_last is False and is_in_air_current is True)
            is_new_detection = (icao not in notified_icaos and is_in_air_current)

            if is_takeoff or is_new_detection:
                event_type = "離陸" if is_takeoff else "検知"
                print(f"【自動更新・{event_type}】 機種: {ac_type}, Tail: {tail}, Flight: {flight}")
                
                location_str = get_location_name(lat, lon)
                fa_origin, destination = get_flight_route(flight)
                origin = fa_origin if fa_origin != "不明" else location_str
                
                send_discord_notification(icao, tail, flight, ac_type, own_op, alt, track, origin, destination, location_str, event_type)
                
                notified_icaos.add(icao)

            in_air_states[icao] = is_in_air_current

        notified_icaos = notified_icaos.intersection(current_batch_icaos)

    except Exception as e:
        print(f"チェック中エラー: {e}")


if __name__ == "__main__":
    print(f"米軍・オメガタンカー限定監視（E135除外・60秒自動更新）を開始しました。")
    while True:
        check_military_takeoff()
        time.sleep(CHECK_INTERVAL)
