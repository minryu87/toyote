"""
Band 참석자 조회 로컬 서비스
Playwright stealth + 클립보드 → 네이버 로그인 → Band 일정 참석자 추출
ngrok으로 외부 노출하여 GitHub Pages에서 호출
"""

import asyncio
import random
import logging
import platform
import json
import urllib.request

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PASTE_KEY = "Meta+v" if platform.system() == "Darwin" else "Control+v"

# Airtable config
AT_TOKEN = "patqL1XAWzzitwppv.bffc8f8f1efd783c42d245f9bcbb480d5cd4dd16adf7c3f6f964ffbfb89db3d7"
AT_BASE = "app3Jmr8i0rfLISZN"


def at_fetch(endpoint, method="GET", body=None):
    url = f"https://api.airtable.com/v0/{AT_BASE}/{endpoint}"
    headers = {"Authorization": f"Bearer {AT_TOKEN}", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())


def get_config():
    """Airtable Config에서 설정값 읽기"""
    data = at_fetch("Config")
    config = {}
    for r in data["records"]:
        config[r["fields"].get("key", "")] = {"value": r["fields"].get("value", ""), "id": r["id"]}
    return config


def save_config(record_id, value):
    at_fetch("Config", method="PATCH", body={"records": [{"id": record_id, "fields": {"value": value}}]})


async def _clipboard_paste(page, selector: str, text: str):
    await page.click(selector)
    await asyncio.sleep(random.uniform(0.3, 0.8))
    await page.evaluate(f'navigator.clipboard.writeText("{text}")')
    await page.keyboard.press(PASTE_KEY)
    await asyncio.sleep(random.uniform(1, 2))


@app.get("/")
async def root():
    return {"status": "ok", "service": "band-local-proxy"}


@app.get("/band/attendees")
async def get_attendees(
    band_no: str = Query(default="97314094"),
    schedule_date: str = Query(default=""),
):
    """
    1. Airtable에서 naver_id/pw 읽기
    2. Playwright stealth로 네이버 로그인
    3. Band 캘린더 접속 → get_schedule 응답 캡처
    4. 참석자 반환
    """
    result = {"success": False, "attendees": [], "absentees": [], "error": ""}

    # Airtable에서 credentials 읽기
    config = get_config()
    naver_id = config.get("naver_id", {}).get("value", "")
    naver_pw = config.get("naver_pw", {}).get("value", "")

    if not naver_id or not naver_pw:
        result["error"] = "Airtable Config에 naver_id/naver_pw가 없습니다."
        return result

    stealth = Stealth()

    async with async_playwright() as p:
        stealth.hook_playwright_context(p)

        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
            locale="ko-KR",
            timezone_id="Asia/Seoul",
        )
        await context.grant_permissions(["clipboard-read", "clipboard-write"])
        page = await context.new_page()

        schedule_data = {}
        captured = asyncio.Event()

        async def handle_response(response):
            url = response.url
            if "get_schedule" in url and "band_no=" in url:
                try:
                    data = await response.json()
                    if data.get("result_code") == 1:
                        rd = data.get("result_data", {})
                        rsvp = rd.get("rsvp", {})
                        schedule_data["name"] = rd.get("name", "")
                        schedule_data["start_at"] = rd.get("start_at", "")
                        schedule_data["attendees"] = [
                            {"name": a.get("name", "")}
                            for a in rsvp.get("attendee_list", [])
                        ]
                        schedule_data["absentees"] = [
                            {"name": a.get("name", "")}
                            for a in rsvp.get("absentee_list", [])
                        ]
                        schedule_data["attendee_count"] = rsvp.get("attendee_count", 0)
                        schedule_data["absentee_count"] = rsvp.get("absentee_count", 0)
                        captured.set()
                        logger.info(f"Captured: {schedule_data.get('name')}, {schedule_data.get('attendee_count')} attendees")
                except Exception as e:
                    logger.error(f"Error parsing: {e}")

        page.on("response", handle_response)

        try:
            # Step 1: Naver Login
            logger.info("네이버 로그인 시작...")
            await page.goto("https://nid.naver.com/nidlogin.login", timeout=60000)
            await page.wait_for_load_state("networkidle", timeout=60000)
            await asyncio.sleep(random.uniform(1.5, 3))

            logger.info("ID/PW 입력...")
            await _clipboard_paste(page, "#id", naver_id)
            await _clipboard_paste(page, "#pw", naver_pw)

            logger.info("로그인 클릭...")
            await page.click("#log\\.login")
            await asyncio.sleep(random.uniform(5, 8))

            current_url = page.url

            # 캡차 확인
            captcha_el = await page.query_selector("#captcha")
            if captcha_el and await captcha_el.is_visible():
                result["error"] = "캡차 발생"
                await browser.close()
                return result

            if "otp" in current_url or "2step" in current_url:
                result["error"] = "2차 인증 필요"
                await browser.close()
                return result

            if "nidlogin" in current_url:
                result["error"] = "로그인 실패"
                await browser.close()
                return result

            # 기기 등록
            if "deviceConfirm" in current_url:
                logger.info("기기 등록 처리...")
                links = await page.query_selector_all("#content a")
                for link in links:
                    text = (await link.inner_text()).strip()
                    if text == "등록":
                        await link.click()
                        await asyncio.sleep(random.uniform(3, 5))
                        break

            logger.info(f"로그인 성공! URL: {page.url}")

            # Step 2: Band 일정 페이지
            if schedule_date:
                schedule_id = f"4/{band_no}/926860211/{schedule_date}"
                url = f"https://www.band.us/band/{band_no}/calendar/event/{schedule_id}"
            else:
                url = f"https://www.band.us/band/{band_no}/calendar"

            logger.info(f"Band 이동: {url}")
            await page.goto(url, timeout=60000)
            await page.wait_for_load_state("networkidle", timeout=60000)

            if "nid.naver.com" in page.url or "auth.band.us" in page.url:
                result["error"] = "Band 접근 실패"
                await browser.close()
                return result

            # Step 3: 응답 대기
            logger.info("일정 데이터 대기...")
            try:
                await asyncio.wait_for(captured.wait(), timeout=20.0)
            except asyncio.TimeoutError:
                if not schedule_date:
                    items = await page.query_selector_all('[class*="schedule"]')
                    for item in items[:5]:
                        await item.click()
                        await asyncio.sleep(2)
                        if captured.is_set():
                            break

                if not captured.is_set():
                    result["error"] = "일정 데이터를 가져올 수 없습니다."
                    await browser.close()
                    return result

            result["success"] = True
            result["schedule_name"] = schedule_data.get("name", "")
            result["attendees"] = schedule_data.get("attendees", [])
            result["absentees"] = schedule_data.get("absentees", [])
            result["attendee_count"] = schedule_data.get("attendee_count", 0)
            result["absentee_count"] = schedule_data.get("absentee_count", 0)

        except Exception as e:
            logger.error(f"오류: {e}")
            result["error"] = str(e)

        await browser.close()

    return result


@app.get("/health")
async def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
