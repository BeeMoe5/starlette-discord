import datetime

import aiohttp
from oauthlib.common import generate_token
from oauthlib.oauth2 import WebApplicationClient

from .models import Connection, Guild, User

DISCORD_URL = "https://discord.com"
API_URL = DISCORD_URL + "/api/v9"


class DiscordTokenUpdated(Exception):
    def __init__(self, token):
        super().__init__()
        self.token = token


class DiscordOauth2Session(aiohttp.ClientSession):
    def __init__(
        self, client_id, client_secret, scope, redirect_uri, *, code, token, **kwargs
    ):
        self._client = client = WebApplicationClient(client_id=client_id, token=token)

        if (not (code or token)) or (code and token):
            raise ValueError(
                "Either 'code' or 'token' parameter must be provided, but not both."
            )

        elif token:
            if not isinstance(token, dict):
                raise TypeError(
                    "Parameter 'token' must be an instance of dict with at least the 'access_token' key.'"
                )

            if "access_token" not in token:
                raise ValueError("Parameter 'token' requires 'access_token' key.")

            elif "token_type" not in token:
                token["token_type"] = "Bearer"

            elif code:
                client.populate_code_attributes({"code": code})

            self._cached_user = self._cached_guilds = self._cached_connections = None
            self._discord_client_secret = client_secret
            self.redirect_uri = redirect_uri
            self.scope = scope

            super().__init__(**kwargs)

    @property
    def token(self):
        return getattr(self._client, "token", None)

    @token.setter
    def token(self, value):
        self._client.token = value
        self._client.populate_token_attributes(value)

    @property
    def session_expired(self):
        return (
            datetime.datetime.fromtimestamp(self.token["expires_at"])
            < datetime.datetime.now()
        )

    @property
    def cached_user(self):
        return self._cached_user

    @property
    def cached_guilds(self):
        return self._cached_guilds

    @property
    def cached_connections(self):
        return self._cached_connections

    @staticmethod
    def new_state():
        return generate_token()

    async def ensure_token(self):

        if not self.token:
            self.token = await self.fetch_token(self._client.code)

        if self.session_expired and self.token:
            await self.refresh_token()

    async def _discord_request(
        self, url_fragment: str, method="GET"
    ):  # todo: maybe deprecate?
        # await self.ensure_token()

        access_token = self.token["access_token"]
        url = API_URL + url_fragment
        headers = {"Authorization": "Authorization: Bearer " + access_token}

        async with self.request(method, url, headers=headers) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def identify(self):
        user_data = await self._discord_request("/users/@me")
        user = User(data=user_data)
        self._cached_user = user
        return user

    async def guilds(self):
        guilds_data = await self._discord_request("/users/@me/guilds")
        guilds = list(map(lambda g: Guild(data=g), guilds_data))
        self._cached_guilds = guilds
        return guilds

    async def connections(self):
        connections_data = await self._discord_request("/users/@me/connections")
        connections = list(map(lambda c: Connection(data=c), connections_data))
        self._cached_connections = connections
        return connections

    async def join_guild(self, guild_id, user_id=None):

        if not user_id:
            user = await self.identify()
            user_id = user.id

        return await self._discord_request(
            f"/guilds/{guild_id}/members/{user_id}", method="PUT"
        )

    async def join_group_dm(self, dm_channel_id, user_id=None):

        if not user_id:
            user = await self.identify()
            user_id = user.id

        return await self._discord_request(
            f"/channels/{dm_channel_id}/recipients/{user_id}", method="PUT"
        )

    async def fetch_token(self, code: str):
        url = API_URL + "/oauth2/token"
        data = {
            "client_id": self._client.client_id,
            "client_secret": self._discord_client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        # can't use ._discord_request because that calls .ensure_token which
        # calls .fetch_token (this current method), what to do?
        async with self.post(url=url, headers=headers, data=data) as resp:
            resp.raise_for_status()
            text = await resp.text()
            self._client.parse_request_body_response(text, scope=self.scope)
            return self.token

    async def refresh_token(self):
        url = API_URL + "/oauth2/token"

        data = {
            "client_id": self._client.client_id,
            "client_secret": self._discord_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": self.token["refresh_token"],
        }

        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        async with self.post(url=url, headers=headers, data=data) as resp:
            resp.raise_for_status()
            text = await resp.text()
            self._client.parse_request_body_response(text, scope=self.scope)
            return self.token

    async def refresh(self):
        await self.refresh_token()
        raise DeprecationWarning(".refresh() is deprecated, switch to refresh_token")

    async def __aenter__(self):
        await self.ensure_token()
        await self.refresh()  # TODO / NOTE: line replaced by .ensure_token

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
