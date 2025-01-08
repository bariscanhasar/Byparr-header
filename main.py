from __future__ import annotations

import logging
import time
from http import HTTPStatus
import json

import uvicorn
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from sbase import SB, BaseCase

import src
import src.utils
import src.utils.consts
from src.models.requests import LinkRequest, LinkResponse, Solution
from src.utils import logger
from src.utils.consts import LOG_LEVEL, USE_HEADLESS, USE_XVFB

app = FastAPI(debug=LOG_LEVEL == logging.DEBUG, log_level=LOG_LEVEL)

cookies = []


@app.get("/")
def read_root():
    """Redirect to /docs."""
    logger.debug("Redirecting to /docs")
    return RedirectResponse(url="/docs", status_code=301)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    health_check_request = read_item(
        LinkRequest.model_construct(url="https://prowlarr.servarr.com/v1/ping")
    )

    if health_check_request.solution.status != HTTPStatus.OK:
        raise HTTPException(
            status_code=500,
            detail="Health check failed",
        )

    return {"status": "ok"}


@app.post("/v1")
def read_item(request: LinkRequest) -> LinkResponse:
    """Handle POST requests."""
    start_time = int(time.time() * 1000)
    
    logger.info(f"Incoming request details:")
    logger.info(f"  URL: {request.url}")
    logger.info(f"  Method: {request.cmd}")
    logger.info(f"  Post Data: {request.postData}")

    if not (request.url.startswith("http://") or request.url.startswith("https://")):
        return LinkResponse.invalid(request.url)

    options = {
        "uc": True,
        "locale_code": "en",
        "test": False,
        "ad_block": True,
        "xvfb": USE_XVFB,
        "headless": USE_HEADLESS,
        "page_load_strategy": "eager",
        "undetected": True
    }

    with SB(**options) as sb:
        try:
            # Disable webdriver flags
            sb.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            # Set stealth JS
            stealth_js = """
            () => {
                const newProto = navigator.__proto__;
                delete newProto.webdriver;
                navigator.__proto__ = newProto;
                
                window.chrome = {
                    runtime: {},
                };
                
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en']
                });
            }
            """
            sb.driver.execute_script(stealth_js)

            if request.headers:
                # Add some additional headers that help with Cloudflare
                headers = {
                    **request.headers,
                    'Cache-Control': 'no-cache',
                    'Pragma': 'no-cache',
                    'sec-ch-ua': '"Chromium";v="121", "Not A(Brand";v="99"',
                    'sec-ch-ua-mobile': '?0',
                    'sec-ch-ua-platform': '"Windows"',
                    'Upgrade-Insecure-Requests': '1',
                }
                sb.driver.execute_cdp_cmd('Network.setExtraHTTPHeaders', {'headers': headers})

            global cookies
            if cookies:
                sb.add_cookies(cookies)
            
            # Initial page load
            sb.uc_open_with_reconnect(request.url)
            time.sleep(5)  # Give some time for initial JS to load
            
            # Handle Cloudflare
            max_retries = 3
            for attempt in range(max_retries):
                source = sb.get_page_source()
                source_bs = BeautifulSoup(source, "html.parser")
                title_tag = source_bs.title
                
                if not (title_tag and title_tag.string in src.utils.consts.CHALLENGE_TITLES):
                    break
                
                logger.debug(f"Challenge detected (attempt {attempt + 1}/{max_retries})")
                
                # Try to solve challenge
                try:
                    # Look for iframe first
                    iframes = sb.driver.find_elements("xpath", "//iframe")
                    for iframe in iframes:
                        try:
                            sb.driver.switch_to.frame(iframe)
                            checkbox = sb.driver.find_element("css selector", "#checkbox")
                            if checkbox.is_displayed():
                                checkbox.click()
                                logger.info("Clicked challenge checkbox")
                                time.sleep(2)
                            sb.driver.switch_to.default_content()
                        except:
                            sb.driver.switch_to.default_content()
                            continue
                    
                    # Try standard captcha click
                    sb.uc_gui_click_captcha()
                    logger.info("Clicked standard captcha")
                except Exception as e:
                    logger.debug(f"Captcha interaction failed: {e}")
                
                time.sleep(5)  # Wait for challenge to process
            
            # Check if we're still on challenge page
            source = sb.get_page_source()
            if "Just a moment" in source or "challenge-running" in source:
                sb.save_screenshot(f"./screenshots/{request.url}.png")
                raise_captcha_bypass_error()

            # Handle POST request if needed
            if request.cmd == "request.post" and request.postData:
                script = f"""
                return fetch('{request.url}', {{
                    method: 'POST',
                    headers: {json.dumps(headers)},
                    body: JSON.stringify({json.dumps(request.postData)}),
                    credentials: 'include',
                    mode: 'cors'
                }}).then(response => response.text())
                  .catch(error => 'Error: ' + error.message);
                """
                source = sb.execute_script(script)

            response = LinkResponse(
                message="Success",
                solution=Solution(
                    userAgent=sb.get_user_agent(),
                    url=sb.get_current_url(),
                    status=200,
                    cookies=sb.get_cookies(),
                    headers=headers if request.headers else {},
                    response=source,
                ),
                startTimestamp=start_time,
            )
            cookies = sb.get_cookies()
            return response

        except Exception as e:
            logger.error(f"Error: {e}")
            if sb.driver:
                sb.driver.quit()
            raise HTTPException(
                status_code=500, detail=str(e)
            ) from e


def raise_captcha_bypass_error():
    """
    Raise a 500 error if the challenge could not be bypassed.

    This function should be called if the challenge is not bypassed after
    clicking the captcha.

    Returns:
        None

    """
    raise HTTPException(status_code=500, detail="Could not bypass challenge")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8191, log_level=LOG_LEVEL)  # noqa: S104
