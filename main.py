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
    
    # Log the incoming request details
    logger.info(f"Incoming request details:")
    logger.info(f"  URL: {request.url}")
    logger.info(f"  Method: {request.cmd}")
    logger.info(f"  Post Data: {request.postData}")
    logger.info(f"  Headers:")
    if request.headers:
        for header_name, header_value in request.headers.items():
            logger.info(f"    {header_name}: {header_value}")

    # URL validation
    if not (request.url.startswith("http://") or request.url.startswith("https://")):
        return LinkResponse.invalid(request.url)

    with SB(
        uc=True,
        locale_code="en",
        test=False,
        ad_block=True,
        xvfb=USE_XVFB,
        headless=USE_HEADLESS,
        page_load_strategy='eager',
        uc_cdp=True,
    ) as sb:
        try:
            # Set custom headers
            if request.headers:
                for header_name, header_value in request.headers.items():
                    sb.driver.execute_cdp_cmd('Network.setExtraHTTPHeaders', {
                        'headers': {header_name: header_value}
                    })

            global cookies
            if cookies:
                sb.add_cookies(cookies)
            
            # First visit to handle Cloudflare
            sb.uc_open_with_reconnect(request.url)
            
            # Wait for Cloudflare challenge to complete (up to 30 seconds)
            max_wait = 30
            start = time.time()
            while time.time() - start < max_wait:
                source = sb.get_page_source()
                source_bs = BeautifulSoup(source, "html.parser")
                title_tag = source_bs.title
                
                if not title_tag or title_tag.string not in src.utils.consts.CHALLENGE_TITLES:
                    break  # Challenge completed
                
                if "Just a moment" in source:
                    logger.info("Waiting for Cloudflare challenge to complete...")
                    time.sleep(2)
                    continue
                
                # Try to solve challenge if interactive elements are present
                try:
                    sb.uc_gui_click_captcha()
                    logger.info("Clicked captcha")
                except:
                    pass
                
                time.sleep(2)
            
            # After bypass, if it's a POST request, execute it
            if request.cmd == "request.post" and request.postData:
                script = f"""
                return fetch('{request.url}', {{
                    method: 'POST',
                    headers: {json.dumps(request.headers)} || {{}},
                    body: JSON.stringify({json.dumps(request.postData)}),
                    credentials: 'include'
                }}).then(response => response.text());
                """
                source = sb.execute_script(script)

            response = LinkResponse(
                message="Success",
                solution=Solution(
                    userAgent=sb.get_user_agent(),
                    url=sb.get_current_url(),
                    status=200,
                    cookies=sb.get_cookies(),
                    headers=request.headers if request.headers else {},
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
