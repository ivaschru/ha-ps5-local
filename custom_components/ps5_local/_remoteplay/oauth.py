import base64
from urllib.parse import parse_qs, urlparse

import aiohttp

from .const import (
    OAUTH_AUTHORIZE_URL,
    OAUTH_CLIENT_ID,
    OAUTH_CLIENT_SECRET,
    OAUTH_REDIRECT_URI,
    OAUTH_TOKEN_URL,
)
from .errors import OAuthError


def login_url() -> str:
    return (
        f"{OAUTH_AUTHORIZE_URL}?service_entity=urn:service-entity:psn&response_type=code"
        f"&client_id={OAUTH_CLIENT_ID}&redirect_uri={OAUTH_REDIRECT_URI}"
        "&scope=psn:clientapp&request_locale=en_US&ui=pr&service_logo=ps&layout_type=popup"
        "&smcid=remoteplay&prompt=always&PlatformPrivacyWs1=minimal&"
    )


def code_from_redirect(redirect_url: str) -> str:
    codes = parse_qs(urlparse(redirect_url.strip()).query).get("code")
    if not codes or not codes[0]:
        raise OAuthError("The pasted URL has no 'code' parameter; copy the full redirect URL")
    return codes[0]


def account_id_from_user_id(user_id: str | int) -> str:
    return base64.b64encode(int(user_id).to_bytes(8, "little")).decode()


class SignInCodeRejected(OAuthError):
    """Sony explicitly rejected the authorization grant."""


class AccountLookupFailed(OAuthError):
    """Sony returned a token but no usable account ID."""


async def _json(response: aiohttp.ClientResponse, what: str) -> dict:
    # Never include the response body or token-bearing URL in an exception.
    if not 200 <= response.status < 300:
        if what == "token exchange" and response.status in (400, 401):
            try:
                error = await response.json(content_type=None)
            except ValueError:
                error = None
            if isinstance(error, dict) and error.get("error") == "invalid_grant":
                raise SignInCodeRejected("Sony rejected the authorization grant")
        raise OAuthError(f"PSN {what} failed with HTTP {response.status}")
    try:
        payload = await response.json(content_type=None)
    except ValueError:
        raise OAuthError(f"PSN {what} did not return JSON") from None
    if not isinstance(payload, dict):
        raise OAuthError(f"PSN {what} returned an unexpected response")
    return payload


async def account_id_from_redirect(
    redirect_url: str,
    session: aiohttp.ClientSession | None = None,
) -> str:
    """Exchange the pasted redirect URL for the base64 Remote Play account id."""
    return await account_id_from_code(code_from_redirect(redirect_url), session)


async def account_id_from_code(
    code: str,
    session: aiohttp.ClientSession | None = None,
) -> str:
    """Exchange the authorization code without constructing a redirect URL."""
    auth = aiohttp.BasicAuth(OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET)
    owned = session is None
    session = session or aiohttp.ClientSession()
    try:
        async with session.post(
            OAUTH_TOKEN_URL,
            auth=auth,
            allow_redirects=False,
            data={
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": OAUTH_REDIRECT_URI,
            },
        ) as response:
            access_token = (await _json(response, "token exchange")).get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise OAuthError("PSN did not return an access token")

        try:
            async with session.get(
                f"{OAUTH_TOKEN_URL}/{access_token}", auth=auth, allow_redirects=False
            ) as response:
                user_id = (await _json(response, "account lookup")).get("user_id")
            if isinstance(user_id, bool) or not isinstance(user_id, (str, int)):
                raise ValueError
            number = int(user_id)
            if not 0 < number < 2**64:
                raise ValueError
            return account_id_from_user_id(number)
        except (OAuthError, ValueError):
            raise AccountLookupFailed("PSN did not return a usable account ID") from None
    finally:
        if owned:
            await session.close()
