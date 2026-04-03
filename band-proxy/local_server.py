"""
Band 참석자 조회 로컬 서비스
Chrome 원격 디버깅(CDP)으로 이미 로그인된 브라우저에 접속
Band 일정 참석자/댓글 추출 후 반환
ngrok으로 외부 노출하여 GitHub Pages에서 호출
"""

import asyncio
import logging

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CDP_URL = "http://localhost:9222"
BAND_NO = "97314094"
CALENDAR_ID = "926860211"


@app.get("/")
async def root():
    return {"status": "ok", "service": "band-local-proxy"}


@app.get("/band/attendees")
async def get_attendees(
    band_no: str = Query(default=BAND_NO),
    schedule_date: str = Query(default=""),
):
    """
    CDP로 이미 실행 중인 Chrome에 접속
    Band 일정 페이지를 새 탭으로 열어 get_schedule + get_comments 캡처
    """
    result = {"success": False, "attendees": [], "absentees": [], "comments": [], "error": ""}

    if not schedule_date:
        result["error"] = "schedule_date 파라미터가 필요합니다 (YYYYMMDD)"
        return result

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            result["error"] = f"Chrome 연결 실패. --remote-debugging-port=9222로 Chrome을 실행하세요. ({e})"
            return result

        context = browser.contexts[0]
        page = await context.new_page()

        schedule_data = {}
        comments_data = []
        schedule_captured = asyncio.Event()
        comments_captured = asyncio.Event()

        async def handle_response(response):
            url = response.url

            # get_schedule 캡처
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
                        schedule_captured.set()
                        logger.info(f"Schedule captured: {schedule_data.get('name')}, {schedule_data.get('attendee_count')} attendees")
                except Exception as e:
                    logger.error(f"Error parsing schedule: {e}")

            # get_comments 캡처
            if "get_comments" in url and "band_no=" in url:
                try:
                    data = await response.json()
                    if data.get("result_code") == 1:
                        items = data.get("result_data", {}).get("items", [])
                        for item in items:
                            author = item.get("author", {}).get("name", "")
                            body = item.get("body", "")
                            comments_data.append({"name": author, "body": body})
                        comments_captured.set()
                        logger.info(f"Comments captured: {len(comments_data)} comments")
                except Exception as e:
                    logger.error(f"Error parsing comments: {e}")

        page.on("response", handle_response)

        try:
            schedule_id = f"4/{band_no}/{CALENDAR_ID}/{schedule_date}"
            url = f"https://www.band.us/band/{band_no}/calendar/event/{schedule_id}"

            logger.info(f"Band 이동: {url}")
            await page.goto(url, timeout=60000)
            await page.wait_for_load_state("networkidle", timeout=60000)

            # 로그인 페이지로 리다이렉트된 경우
            if "nid.naver.com" in page.url or "auth.band.us" in page.url:
                result["error"] = "로그인 세션 만료. Chrome에서 Band에 다시 로그인하세요."
                await page.close()
                return result

            # 응답 대기
            logger.info("일정 데이터 대기...")
            try:
                await asyncio.wait_for(schedule_captured.wait(), timeout=20.0)
            except asyncio.TimeoutError:
                result["error"] = "일정 데이터를 가져올 수 없습니다."
                await page.close()
                return result

            # 댓글 대기 (없을 수도 있음)
            try:
                await asyncio.wait_for(comments_captured.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.info("댓글 없음 또는 타임아웃")

            result["success"] = True
            result["schedule_name"] = schedule_data.get("name", "")
            result["attendees"] = schedule_data.get("attendees", [])
            result["absentees"] = schedule_data.get("absentees", [])
            result["attendee_count"] = schedule_data.get("attendee_count", 0)
            result["absentee_count"] = schedule_data.get("absentee_count", 0)
            result["comments"] = comments_data

        except Exception as e:
            logger.error(f"오류: {e}")
            result["error"] = str(e)

        await page.close()

    return result


@app.get("/health")
async def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
