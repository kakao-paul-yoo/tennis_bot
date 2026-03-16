"""
네이버 예약 테니스장 빈 슬롯 모니터링 (Railway 배포용)
- 환경변수로 민감 정보 관리
- 5분마다 예약 가능 시간 체크
- 텔레그램 알림 발송
"""

import requests
import schedule
import time
import json
import os
from datetime import datetime, timedelta

# ==========================================
# 설정값 (환경변수에서 읽기)
# ==========================================

BIZ_ID = os.environ.get("BIZ_ID", "217811")
ITEM_ID = os.environ.get("ITEM_ID", "7409663")
MONITOR_DAYS_AHEAD = int(os.environ.get("MONITOR_DAYS_AHEAD", "22"))
CHECK_INTERVAL_MINUTES = int(os.environ.get("CHECK_INTERVAL_MINUTES", "5"))

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
COOKIE = os.environ.get("NAVER_COOKIE", "")

# ==========================================
# GraphQL API
# ==========================================

GRAPHQL_URL = "https://booking.naver.com/graphql"

HEADERS = {
    "Cookie": COOKIE,
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
    ),
    "Referer": f"https://booking.naver.com/booking/10/bizes/{BIZ_ID}/items/{ITEM_ID}",
    "Accept": "*/*",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Origin": "https://booking.naver.com",
}

GRAPHQL_QUERY = """
query hourlySchedule($scheduleParams: ScheduleParams) {
  schedule(input: $scheduleParams) {
    bizItemSchedule {
      hourly {
        id
        name
        slotId
        unitStartDateTime
        unitStartTime
        unitBookingCount
        unitStock
        bookingCount
        stock
        isBusinessDay
        isSaleDay
        isUnitSaleDay
        duration
        minBookingCount
        maxBookingCount
        saleStartDateTime
        saleEndDateTime
        prices {
          groupName
          price
          name
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}
"""


def get_hourly_schedule(start_dt: str, end_dt: str) -> list:
    payload = {
        "operationName": "hourlySchedule",
        "variables": {
            "scheduleParams": {
                "businessTypeId": 10,
                "businessId": BIZ_ID,
                "bizItemId": ITEM_ID,
                "startDateTime": start_dt,
                "endDateTime": end_dt,
                "fixedTime": True,
                "includesHolidaySchedules": True,
            }
        },
        "query": GRAPHQL_QUERY,
    }
    try:
        res = requests.post(GRAPHQL_URL, headers=HEADERS, json=payload, timeout=10)
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
        print(f"  [API 오류] {e}")
        return []


def is_available(slot: dict) -> bool:
    unit_booking = slot.get("unitBookingCount")
    if unit_booking is None:
        return False
    is_sale_day = slot.get("isSaleDay", False)
    is_unit_sale_day = slot.get("isUnitSaleDay", False)
    if not (is_sale_day and is_unit_sale_day):
        return False
    try:
        unit_start_time = slot.get("unitStartTime", "")
        hour = int(unit_start_time.split(" ")[1].split(":")[0])
        if not (6 <= hour <= 21):
            return False
        # 10~12시 제외
        if 10 <= hour <= 12:
            return False
    except Exception:
        return False
    return unit_booking == 0


# ==========================================
# 텔레그램 알림
# ==========================================

def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("  [텔레그램] 토큰 또는 채팅 ID 미설정")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        res = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
        }, timeout=10)
        if res.status_code == 200:
            print("  ✅ 텔레그램 알림 발송 성공")
        else:
            print(f"  ❌ 텔레그램 실패: {res.text}")
    except Exception as e:
        print(f"  [텔레그램 오류] {e}")


# ==========================================
# 메인 모니터링
# ==========================================

notified_slots = set()


def check_and_notify():
    # 쿠키 갱신 (환경변수 재읽기)
    HEADERS["Cookie"] = os.environ.get("NAVER_COOKIE", COOKIE)

    now = datetime.now()
    start_dt = now.strftime("%Y-%m-%dT00:00:00")
    end_dt = (now + timedelta(days=MONITOR_DAYS_AHEAD)).strftime("%Y-%m-%dT23:59:59")

    print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] 체크 중...")

    slots = get_hourly_schedule(start_dt, end_dt)

    if not slots:
        print("  → 슬롯 데이터 없음 (쿠키 만료 가능성)")
        send_telegram("⚠️ 테니스장 모니터링 오류: 슬롯 데이터 없음\n쿠키가 만료됐을 수 있어요!")
        return

    new_available = []
    for slot in slots:
        if not is_available(slot):
            continue
        slot_key = f"{slot.get('unitStartDateTime', '')}_{slot.get('unitStartTime', '')}"
        if slot_key in notified_slots:
            continue
        notified_slots.add(slot_key)
        new_available.append(slot)

    if new_available:
        lines = [f"🎾 테니스장 예약 가능! ({len(new_available)}개 슬롯)\n"]
        for s in new_available:
            unit_start_time = s.get("unitStartTime", "")  # "2026-03-16 06:00:00"
            duration = s.get("duration", 0)
            try:
                dt = datetime.strptime(unit_start_time, "%Y-%m-%d %H:%M:%S")
                weekdays = ["월", "화", "수", "목", "금", "토", "일"]
                wd = weekdays[dt.weekday()]
                time_str = dt.strftime(f"%m/%d({wd}) %H:%M")
            except Exception:
                time_str = unit_start_time
            lines.append(f"  📅 {time_str} ({duration}분)")

        lines.append(
            f"\n👉 https://booking.naver.com/booking/10/bizes/{BIZ_ID}/items/{ITEM_ID}"
        )
        message = "\n".join(lines)
        print(message)
        send_telegram(message)
    else:
        available_count = sum(1 for s in slots if is_available(s))
        print(f"  → 새 슬롯 없음 (전체 {len(slots)}개 | 현재 예약가능 {available_count}개)")


# ==========================================
# 실행
# ==========================================

if __name__ == "__main__":
    print("🎾 네이버 예약 테니스장 모니터링 시작")
    print(f"  BIZ_ID: {BIZ_ID} | ITEM_ID: {ITEM_ID}")
    print(f"  체크 주기: {CHECK_INTERVAL_MINUTES}분 | {MONITOR_DAYS_AHEAD}일 앞까지 모니터링\n")

    check_and_notify()

    schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(check_and_notify)

    while True:
        schedule.run_pending()
        time.sleep(1)
