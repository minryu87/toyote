"""
Chrome을 원격 디버깅 모드로 실행하는 스크립트
이미 Band에 로그인된 Chrome 프로필을 그대로 사용
"""

import subprocess
import sys
import os

CHROME_PATHS = [
    os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
]


def find_chrome():
    for path in CHROME_PATHS:
        if os.path.exists(path):
            return path
    return None


def main():
    chrome = find_chrome()
    if not chrome:
        print("Chrome을 찾을 수 없습니다. 경로를 확인하세요.")
        sys.exit(1)

    print("=" * 50)
    print("Chrome을 원격 디버깅 모드로 실행합니다.")
    print()
    print("주의: 기존 Chrome 창을 모두 닫고 실행하세요.")
    print("이미 로그인된 프로필이 그대로 사용됩니다.")
    print("=" * 50)
    print()

    cmd = [
        chrome,
        "--remote-debugging-port=9222",
        "https://www.band.us/band/97314094/calendar",
    ]

    print(f"실행: {chrome}")
    print("Chrome이 열리면 Band 캘린더가 보이는지 확인하세요.")
    print("이후 local_server.py를 별도 터미널에서 실행하면 됩니다.")
    print()

    subprocess.Popen(cmd)
    print("Chrome 실행됨. 이 터미널은 닫아도 됩니다.")


if __name__ == "__main__":
    main()
