"""
Band 캘린더 참석자 조회 프록시 서버
Cookie 직접 주입 방식으로 Band 접속 → get_schedule 응답 캡처
"""

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright
import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def parse_cookie_string(cookie_str):
    """Band cookie 문자열을 Playwright cookie 형식으로 변환
    각 cookie를 여러 도메인에 세팅"""
    cookies = []
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            name, _, value = part.partition("=")
            name = name.strip()
            value = value.strip()
            if name and value:
                cookies.append({
                    "name": name,
                    "value": value,
                    "domain": ".band.us",
                    "path": "/",
                    "secure": True,
                    "sameSite": "None",
                })
    return cookies


@app.get("/")
async def root():
    return {"status": "ok", "service": "band-proxy"}


@app.get("/band/attendees")
async def get_attendees(
    cookie: str = Query(..., description="Band cookie string"),
    band_no: str = Query(default="97314094"),
    schedule_date: str = Query(default="", description="YYYYMMDD format"),
):
    result = {"success": False, "attendees": [], "absentees": [], "error": ""}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox']
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1280, "height": 720},
        )

        # Cookie 주입
        cookies = parse_cookie_string(cookie)
        logger.info(f"Setting {len(cookies)} cookies")
        await context.add_cookies(cookies)

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
                        logger.info(f"Captured: {schedule_data.get('name')}, attendees={schedule_data.get('attendee_count')}")
                    else:
                        logger.warning(f"get_schedule result_code: {data.get('result_code')}")
                except Exception as e:
                    logger.error(f"Error parsing response: {e}")

        page.on("response", handle_response)

        try:
            # 먼저 Band 메인 페이지 방문하여 세션 확립
            logger.info("Visiting band.us main first to establish session...")
            await page.goto("https://www.band.us/", timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=30000)
            await asyncio.sleep(2)
            logger.info(f"After main page: {page.url}")

            # Band 일정 페이지로 이동
            if schedule_date:
                schedule_id = f"4/{band_no}/926860211/{schedule_date}"
                url = f"https://www.band.us/band/{band_no}/calendar/event/{schedule_id}"
            else:
                url = f"https://www.band.us/band/{band_no}/calendar"

            logger.info(f"Navigating to: {url}")
            await page.goto(url, timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=30000)

            current_url = page.url
            logger.info(f"Current URL after navigation: {current_url}")

            # 로그인 페이지로 리다이렉트 확인
            if "nid.naver.com" in current_url or "auth.band.us" in current_url:
                result["error"] = f"쿠키가 만료되었습니다. Redirected to: {current_url}"
                await browser.close()
                return result

            # get_schedule 응답 대기
            logger.info("Waiting for schedule data...")
            try:
                await asyncio.wait_for(captured.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                # 캘린더 페이지에서 일정 클릭 시도
                if not schedule_date:
                    try:
                        items = await page.query_selector_all('[class*="schedule"]')
                        for item in items[:5]:
                            await item.click()
                            await asyncio.sleep(2)
                            if captured.is_set():
                                break
                    except Exception as e:
                        logger.error(f"Click error: {e}")

                if not captured.is_set():
                    result["error"] = "일정 데이터를 가져올 수 없습니다. 날짜를 확인하거나 쿠키를 갱신해주세요."
                    await browser.close()
                    return result

            result["success"] = True
            result["schedule_name"] = schedule_data.get("name", "")
            result["attendees"] = schedule_data.get("attendees", [])
            result["absentees"] = schedule_data.get("absentees", [])
            result["attendee_count"] = schedule_data.get("attendee_count", 0)
            result["absentee_count"] = schedule_data.get("absentee_count", 0)

        except Exception as e:
            logger.error(f"Error: {e}")
            result["error"] = str(e)

        await browser.close()

    return result


@app.get("/health")
async def health():
    return {"status": "healthy"}
