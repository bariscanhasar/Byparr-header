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
    logger.info(f"Request: {request}")

    if not (request.url.startswith("http://") or request.url.startswith("https://")):
        return LinkResponse.invalid(request.url)

    response: LinkResponse

    with SB(
        uc=True,
        locale_code="en",
        test=False,
        ad_block=True,
        xvfb=USE_XVFB,
        headless=USE_HEADLESS,
    ) as sb:
        try:
            sb: BaseCase
            
            # First bypass Cloudflare
            bypass_url = "/".join(request.url.split("/")[:3])  # Get base domain
            logger.info(f"Attempting to bypass Cloudflare at: {bypass_url}")
            
            # Initial visit to handle Cloudflare
            sb.uc_open_with_reconnect(bypass_url)
            
            # Handle Cloudflare challenge
            max_retries = 3
            for attempt in range(max_retries):
                source = sb.get_page_source()
                source_bs = BeautifulSoup(source, "html.parser")
                title_tag = source_bs.title
                
                logger.info(f"Checking page title (attempt {attempt + 1}/{max_retries})")
                if title_tag:
                    logger.info(f"Page title: {title_tag.string}")
                
                if not (title_tag and title_tag.string in src.utils.consts.CHALLENGE_TITLES):
                    logger.info("No challenge detected, proceeding...")
                    break
                
                logger.info("Challenge detected, attempting to solve...")
                try:
                    sb.uc_gui_click_captcha()
                    logger.info("Clicked captcha")
                except Exception as e:
                    logger.error(f"Failed to click captcha: {e}")
                
                time.sleep(3)
                logger.info("Waiting after captcha click...")
            
            # Now make the POST request with required headers
            if request.cmd == "request.post" and request.postData:
                logger.info("Preparing POST request...")
                
                # Ensure required headers are present
                headers = request.headers or {}
                
                # Create XMLHttpRequest instead of using fetch
                script = f"""
                var done = arguments[0];
                
                var xhr = new XMLHttpRequest();
                xhr.open('POST', '{request.url}', true);
                
                // Set headers
                {' '.join([f"xhr.setRequestHeader('{k}', '{v}');" for k, v in headers.items()])}
                
                xhr.onload = function() {{
                    done({{
                        status: xhr.status,
                        text: xhr.responseText
                    }});
                }};
                
                xhr.onerror = function() {{
                    done({{
                        status: 500,
                        text: 'XHR Error'
                    }});
                }};
                
                xhr.send(JSON.stringify({json.dumps(request.postData)}));
                """
                
                logger.info("Executing POST request...")
                try:
                    result = sb.driver.execute_async_script(script, timeout=30000)  # 30 second timeout
                    logger.info(f"POST request completed with status: {result.get('status', 'unknown')}")
                    source = result.get('text', '')
                    status = result.get('status', 500)
                except Exception as e:
                    logger.error(f"POST request failed: {e}")
                    source = str(e)
                    status = 500
            else:
                source = sb.get_page_source()
                status = 200

            response = LinkResponse(
                message="Success",
                solution=Solution(
                    userAgent=sb.get_user_agent(),
                    url=sb.get_current_url(),
                    status=status,
                    cookies=[],
                    headers=request.headers if request.headers else {},
                    response=source,
                ),
                startTimestamp=start_time,
            )
            
        except Exception as e:
            logger.error(f"Error: {e}")
            logger.error(f"Full error details: {str(e)}")
            if sb.driver:
                sb.driver.quit()
            raise HTTPException(
                status_code=500, detail=str(e)
            ) from e

    return response


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
