"""
네이버 예약 테니스장 빈 슬롯 모니터링 (Railway 배포용)
- 내곡 테니스장 1~8번 코트
- 양재 테니스장 A~C(실내) + 1~8번(실외) 코트
- 텔레그램 명령어로 수동 조회 가능
- 5분마다 자동 체크 + 텔레그램 알림
"""

import requests
import schedule
import time
import os
import threading
from datetime import datetime, timedelta

# ==========================================
# 설정값
# ==========================================

MONITOR_DAYS_AHEAD = int(os.environ.get("MONITOR_DAYS_AHEAD", "22"))
CHECK_INTERVAL_MINUTES = int(os.environ.get("CHECK_INTERVAL_MINUTES", "5"))

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ==========================================
# 테니스장 목록
# ==========================================

VENUES = {
    "내곡": {
        "biz_id": "217811",
        "courts": {
            "1번코트(하드)": "7409663",
            "2번코트(하드)": "7409667",
            "3번코트(하드)": "7409675",
            "4번코트(하드)": "7409682",
            "5번코트(하드)": "7409701",
            "6번코트(하드)": "7409707",
            "7번코트(잔디)": "7409712",
            "8번코트(잔디)": "7409714",
        },
        "exclude_hours": [10, 11, 12],
    },
    "양재": {
        "biz_id": "210031",
        "courts": {
            "A코트(실내)": "7378215",
            "B코트(실내)": "7378223",
            "C코트(실내)": "7378226",
            "1번코트(실외)": "7378227",
            "2번코트(실외)": "7378234",
            "3번코트(실외)": "7378236",
            "4번코트(실외)": "7378258",
            "5번코트(실외)": "7378262",
            "6번코트(실외)": "7378328",
            "7번코트(실외)": "7378337",
            "8번코트(실외)": "7378348",
        },
        "exclude_hours": [],
    },
}

# ==========================================
# GraphQL API
# ==========================================

GRAPHQL_URL = "https://booking.naver.com/graphql"

GRAPHQL_QUERY = """
query hourlySchedule($scheduleParams: ScheduleParams) {
  schedule(input: $scheduleParams) {
    bizItemSchedule {
      hourly {
        id
        unitStartDateTime
        unitStartTime
        unitBookingCount
        unitStock
        isSaleDay
        isUnitSaleDay
        duration
        __typename
      }
      __typename
    }
    __typename
  }
}
"""


def get_headers(biz_id: str, item_id: str) -> dict:
    cookie = os.environ.get("NAVER_COOKIE", "")
    return {
        "Cookie": cookie,
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
        ),
        "Referer": f"https://booking.naver.com/booking/10/bizes/{biz_id}/items/{item_id}",
        "Accept": "*/*",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Origin": "https://booking.naver.com",
    }


def get_hourly_schedule(biz_id: str, item_id: str, start_dt: str, end_dt: str) -> list:
    payload = {
        "operationName": "hourlySchedule",
        "variables": {
            "scheduleParams": {
                "businessTypeId": 10,
                "businessId": biz_id,
                "bizItemId": item_id,
                "startDateTime": start_dt,
                "endDateTime": end_dt,
                "fixedTime": True,
                "includesHolidaySchedules": True,
            }
        },
        "query": GRAPHQL_QUERY,
    }
    try:
        res = requests.post(
            GRAPHQL_URL, headers=get_headers(biz_id, item_id), json=payload, timeout=10
        )
        res.raise_for_status()
        data = res.json()
        hourly = (
            data.get("data", {})
            .get("schedule", {})
            .get("bizItemSchedule", {})
            .get("hourly", [])
        )
        return hourly or []
    except Exception as e:
        print(f"  [API 오류 - {item_id}] {e}")
        return []


from datetime import datetime, timedelta, timezone

# KST 타임존 정의
KST = timezone(timedelta(hours=9))

def is_available(slot: dict, exclude_hours: list = []) -> bool:
    unit_booking = slot.get("unitBookingCount")
    if unit_booking is None:
        return False
    if not slot.get("isSaleDay", False) or not slot.get("isUnitSaleDay", False):
        return False
    try:
        unit_start_time = slot.get("unitStartTime", "")
        hour = int(unit_start_time.split(" ")[1].split(":")[0])
        if not (6 <= hour <= 21):
            return False
        if hour in exclude_hours:
            return False
        # KST 현재 시간과 비교
        slot_dt = datetime.strptime(unit_start_time, "%Y-%m-%d %H:%M:%S")
        now_kst = datetime.now(KST).replace(tzinfo=None)
        if slot_dt <= now_kst:
            return False
    except Exception:
        return False
    return unit_booking == 0


def format_slot_time(unit_start_time: str) -> str:
    try:
        dt = datetime.strptime(unit_start_time, "%Y-%m-%d %H:%M:%S")
        weekdays = ["월", "화", "수", "목", "금", "토", "일"]
        wd = weekdays[dt.weekday()]
        return dt.strftime(f"%m/%d({wd}) %H:%M")
    except Exception:
        return unit_start_time


# ==========================================
# 텔레그램 API
# ==========================================

def send_telegram(message: str, chat_id: str = None):
    if not TELEGRAM_BOT_TOKEN:
        return
    target = chat_id or TELEGRAM_CHAT_ID
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunks = [message[i:i+4000] for i in range(0, len(message), 4000)]
    for chunk in chunks:
        try:
            res = requests.post(url, json={"chat_id": target, "text": chunk}, timeout=10)
            if res.status_code == 200:
                print("  ✅ 텔레그램 발송 성공")
            else:
                print(f"  ❌ 텔레그램 실패: {res.text}")
        except Exception as e:
            print(f"  [텔레그램 오류] {e}")


def get_telegram_updates(offset: int = None) -> list:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    try:
        res = requests.get(url, params=params, timeout=35)
        return res.json().get("result", [])
    except Exception:
        return []


# ==========================================
# 명령어 처리
# ==========================================

def query_venue(venue_name: str, court_name: str = None, days: int = None) -> str:
    venue = VENUES.get(venue_name)
    if not venue:
        return f"❓ '{venue_name}' 테니스장을 찾을 수 없어요"

    now = datetime.now()
    start_dt = now.strftime("%Y-%m-%dT00:00:00")
    end_dt = (now + timedelta(days=days or MONITOR_DAYS_AHEAD)).strftime("%Y-%m-%dT23:59:59")
    biz_id = venue["biz_id"]
    exclude_hours = venue.get("exclude_hours", [])

    courts = (
        {court_name: venue["courts"][court_name]}
        if court_name and court_name in venue["courts"]
        else venue["courts"]
    )

    lines = [f"🎾 {venue_name} 테니스장 예약 가능 현황\n"]
    total = 0

    for cname, item_id in courts.items():
        slots = get_hourly_schedule(biz_id, item_id, start_dt, end_dt)
        avail = [s for s in slots if is_available(s, exclude_hours)]
        if avail:
            total += len(avail)
            lines.append(f"✅ {cname} ({len(avail)}개)")
            by_date = {}
            for s in avail:
                t = s.get("unitStartTime", "")
                date = t.split(" ")[0]
                by_date.setdefault(date, []).append(t)
            for date, times in sorted(by_date.items()):
                time_strs = [t.split(" ")[1][:5] for t in times]
                dt = datetime.strptime(date, "%Y-%m-%d")
                weekdays = ["월", "화", "수", "목", "금", "토", "일"]
                wd = weekdays[dt.weekday()]
                lines.append(f"   {dt.strftime('%m/%d')}({wd}): {', '.join(time_strs)}")

    if total == 0:
        lines.append("❌ 예약 가능한 시간이 없어요")
    else:
        lines.append(f"\n총 {total}개 슬롯")
        lines.append(f"👉 https://booking.naver.com/booking/10/bizes/{biz_id}/items/")

    return "\n".join(lines)


def handle_command(text: str, chat_id: str):
    text = text.strip()
    text_lower = text.lower()

    # /help
    if text_lower in ["/help", "도움말", "명령어"]:
        msg = (
            "🎾 테니스장 봇 명령어\n\n"
            "📋 내곡 현황 — 내곡 전체 코트\n"
            "📋 양재 현황 — 양재 전체 코트\n"
            "📋 내곡 1번코트 — 내곡 특정 코트\n"
            "📋 양재 A코트 — 양재 특정 코트\n"
            "📋 오늘 내곡 — 내곡 오늘 현황\n"
            "📋 오늘 양재 — 양재 오늘 현황\n"
            "📋 상태 — 모니터링 상태 확인\n"
        )
        send_telegram(msg, chat_id)

    # 상태
    elif text_lower in ["상태", "status"]:
        now = datetime.now()
        venue_info = "\n".join(
            [f"  {name}: {len(v['courts'])}개 코트" for name, v in VENUES.items()]
        )
        msg = (
            f"✅ 모니터링 중\n"
            f"체크 주기: {CHECK_INTERVAL_MINUTES}분\n"
            f"모니터링 범위: {MONITOR_DAYS_AHEAD}일\n"
            f"테니스장:\n{venue_info}\n"
            f"현재 시각: {now.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        send_telegram(msg, chat_id)

    # 오늘 + 테니스장
    elif "오늘" in text_lower:
        matched = False
        for venue_name in VENUES.keys():
            if venue_name in text:
                send_telegram(query_venue(venue_name, days=1), chat_id)
                matched = True
        if not matched:
            for venue_name in VENUES.keys():
                send_telegram(query_venue(venue_name, days=1), chat_id)

    # 테니스장 + 코트
    else:
        matched_venue = None
        for venue_name in VENUES.keys():
            if venue_name in text:
                matched_venue = venue_name
                break

        if matched_venue:
            venue = VENUES[matched_venue]
            matched_court = None
            for court_name in venue["courts"].keys():
                # "A코트", "1번코트" 등 부분 일치 검색
                court_short = court_name.split("(")[0]  # "(실내)" 제거
                if court_short in text:
                    matched_court = court_name
                    break
            send_telegram(query_venue(matched_venue, matched_court), chat_id)
        else:
            send_telegram(
                "❓ 알 수 없는 명령어예요\n'도움말' 을 입력해보세요!",
                chat_id
            )


# ==========================================
# 자동 모니터링
# ==========================================

notified_slots = set()


def check_and_notify():
    now = datetime.now()
    start_dt = now.strftime("%Y-%m-%dT00:00:00")
    end_dt = (now + timedelta(days=MONITOR_DAYS_AHEAD)).strftime("%Y-%m-%dT23:59:59")

    print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] 전체 체크 중...")

    all_new_slots = []
    total_courts = sum(len(v["courts"]) for v in VENUES.values())
    error_count = 0

    for venue_name, venue in VENUES.items():
        biz_id = venue["biz_id"]
        exclude_hours = venue.get("exclude_hours", [])

        for court_name, item_id in venue["courts"].items():
            slots = get_hourly_schedule(biz_id, item_id, start_dt, end_dt)
            if not slots:
                error_count += 1
                continue
            for slot in slots:
                if not is_available(slot, exclude_hours):
                    continue
                slot_key = f"{item_id}_{slot.get('unitStartDateTime', '')}_{slot.get('unitStartTime', '')}"
                if slot_key in notified_slots:
                    continue
                notified_slots.add(slot_key)
                all_new_slots.append((venue_name, court_name, item_id, slot))

    if error_count == total_courts:
        print("  → 전체 오류 (쿠키 만료 가능성)")
        send_telegram(
            "⚠️ 테니스장 모니터링 오류\n"
            "쿠키가 만료됐을 수 있어요!\n"
            "Railway Variables에서 NAVER_COOKIE를 갱신해주세요."
        )
        return

    if all_new_slots:
        near_slots = []
        far_slots = []
        cutoff = now + timedelta(days=3)

        for venue_name, court_name, item_id, s in all_new_slots:
            try:
                dt = datetime.strptime(s.get("unitStartTime", ""), "%Y-%m-%d %H:%M:%S")
                if dt <= cutoff:
                    near_slots.append((venue_name, court_name, item_id, s))
                else:
                    far_slots.append((venue_name, court_name, item_id, s))
            except Exception:
                near_slots.append((venue_name, court_name, item_id, s))

        lines = [f"🎾 테니스장 예약 가능! (총 {len(all_new_slots)}개)\n"]

        if near_slots:
            lines.append("📌 3일 이내:")
            for venue_name, court_name, item_id, s in near_slots:
                time_str = format_slot_time(s.get("unitStartTime", ""))
                duration = s.get("duration", 0)
                lines.append(f"  📅 {time_str} ({duration}분) | {venue_name} {court_name}")

        if far_slots:
            lines.append(f"\n📋 이후 슬롯: {len(far_slots)}개 더 있음")

        message = "\n".join(lines)
        print(message)
        send_telegram(message)
    else:
        print(f"  → 새 슬롯 없음")


# ==========================================
# 텔레그램 폴링
# ==========================================

def start_polling():
    print("💬 텔레그램 명령어 수신 시작")
    last_update_id = None

    while True:
        try:
            updates = get_telegram_updates(
                offset=last_update_id + 1 if last_update_id else None
            )
            for update in updates:
                last_update_id = update["update_id"]
                message = update.get("message", {})
                text = message.get("text", "")
                chat_id = str(message.get("chat", {}).get("id", ""))
                if text and chat_id:
                    print(f"  [명령어] {chat_id}: {text}")
                    handle_command(text, chat_id)
        except Exception as e:
            print(f"  [폴링 오류] {e}")
            time.sleep(5)


# ==========================================
# 실행
# ==========================================

if __name__ == "__main__":
    venue_summary = ", ".join(
        [f"{name}({len(v['courts'])}코트)" for name, v in VENUES.items()]
    )
    print(f"🎾 테니스장 모니터링 시작: {venue_summary}")
    print(f"  체크 주기: {CHECK_INTERVAL_MINUTES}분 | {MONITOR_DAYS_AHEAD}일 앞까지\n")

    polling_thread = threading.Thread(target=start_polling, daemon=True)
    polling_thread.start()

    send_telegram(
        f"🎾 테니스장 모니터링 시작!\n"
        f"대상: {venue_summary}\n"
        f"'도움말' 을 입력하면 명령어를 볼 수 있어요."
    )

    check_and_notify()

    schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(check_and_notify)

    while True:
        schedule.run_pending()
        time.sleep(1)
