from urllib.parse import urlparse, parse_qs
from random import choices, choice, shuffle
from aiohttp import ClientSession
from string import hexdigits
from loguru import logger
from time import time

from modules import DataBase
from modules.retry import retry, have_json
from settings import BID_SETTINGS


class Browser:

    def __init__(self, proxy: str, address: str, db: DataBase):
        self.max_retries = 5
        self.address = address
        self.db = db

        if proxy not in ['https://log:pass@ip:port', 'http://log:pass@ip:port', 'log:pass@ip:port', '', None]:
            self.proxy = "http://" + proxy.removeprefix("https://").removeprefix("http://")
        else:
            self.proxy = None

        self.sessions = []
        self.session = self.get_new_session()


    def get_new_session(self):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            "Origin": "https://app.opinion.trade",
            "Referer": "https://app.opinion.trade/",
            "x-device-kind": "web",
            "x-device-fingerprint": "".join(choices(hexdigits, k=32)).lower(),
        }

        session = ClientSession(headers=headers)
        session.proxy = self.proxy

        self.sessions.append(session)
        return session


    async def close_sessions(self):
        for session in self.sessions:
            await session.close()


    @have_json
    async def send_request(self, **kwargs):
        if kwargs.get("session"):
            session = kwargs["session"]
            del kwargs["session"]
        else:
            session = self.session

        if kwargs.get("method"): kwargs["method"] = kwargs["method"].upper()
        if self.proxy:
            kwargs["proxy"] = self.proxy

        return await session.request(**kwargs)


    async def is_user_registered(self, retry: int = 0):
        r = await self.send_request(
            method="GET",
            url=f'https://proxy.opinion.trade:8443/api/bsc/api/v1/user/is/new/user?wallet_address={self.address}',
        )
        response = await r.json()
        if not response.get("result") or "result" not in response["result"]:
            if retry < 5:
                return await self.is_user_registered(retry + 1)
            raise Exception(f'Failed to get user registration: {response}')
        return not response["result"]["result"]


    async def user_login(self, sign_text: str, signature: str, timestamp: int, nonce: int | str):
        r = await self.send_request(
            method="POST",
            url='https://proxy.opinion.trade:8443/api/bsc/api/v1/user/token',
            json={
                "nonce": str(nonce),
                "timestamp": timestamp,
                "siwe_message": sign_text,
                "sign": signature,
                "invite_code": "",
                "sources": "web",
                "sign_in_wallet_plugin": None
            },
        )
        response = await r.json()
        if response.get("errmsg") or response.get("errno"):
            raise Exception(f'Failed to user login: {response}')

        self.session.headers.update({
            "Authorization": "Bearer " + response["result"]["token"],
            "x-aws-waf-token": "",
        })


    async def get_profile_info(self):
        r = await self.send_request(
            method="GET",
            url=f'https://proxy.opinion.trade:8443/api/bsc/api/v2/user/{self.address}/profile?chainId=56',
        )
        response = await r.json()
        if response.get("errmsg") or response.get("errno"):
            raise Exception(f'Failed to get profile info: {response}')
        return response["result"]


    async def is_approved(self, proxy_address: str):
        r = await self.send_request(
            method="GET",
            url=f'https://proxy.opinion.trade:8443/api/bsc/api/v2/gnosis_safe/{proxy_address}/approved?chainId=56',
        )
        response = await r.json()
        if response.get("errmsg") or response.get("errno"):
            raise Exception(f'Failed to get is approved: {response}')
        return response["result"]


    async def get_events(self, event_to_find: dict = None):
        def _parse_event(event: dict, event_name: str = ""):
            return {
                "name": event_name + (" " if event_name else "") + event["title"],
                "prices": [float(event.get("yesBuyPrice") or event["yesMarketPrice"]), float(event.get("noBuyPrice") or event["noMarketPrice"])],
                "tokens": [event["yesPos"], event["noPos"]],
                "labels": [event["yesLabel"], event["noLabel"]],
                "is_child": bool(event_name),
                "raw_event": event,
            }

        if BID_SETTINGS["LIST"] or BID_SETTINGS["SINGLE_BUY"] or event_to_find:
            if event_to_find:
                event_url = event_to_find["link"]
            elif BID_SETTINGS["LIST"]:
                event_url = choice(BID_SETTINGS["LIST"])

            elif BID_SETTINGS["SINGLE_BUY"]:
                event_to_find = choice(BID_SETTINGS["SINGLE_BUY"])
                event_url = event_to_find["link"]

            event_params = {
                k: v[0] for k, v in
                parse_qs(urlparse(event_url).query).items()
            }

            api_url = "https://proxy.opinion.trade:8443/api/bsc/api/v2/topic/"
            if event_params.get("type") == "multi":
                api_url += "mutil/"
            r = await self.send_request(
                method="GET",
                url=api_url + event_params["topicId"],
            )
            response = await r.json()
            event = response["result"]["data"]
            if event["childList"]:
                raw_events = []
                for child in event["childList"]:
                    parsed_child = _parse_event(child, event_name=event["title"])
                    if (
                        not event_to_find or
                        parsed_child["raw_event"]["title"] == event_to_find["event_name"]
                    ):
                        if event_to_find:
                            parsed_child["force_vote"] = event_to_find["vote"]

                        raw_events.append(parsed_child)
            else:
                parsed_event = _parse_event(event)
                if event_to_find:
                    parsed_event["force_vote"] = event_to_find["vote"]
                raw_events = [parsed_event]

            if raw_events:
                parsed_events = [choice(raw_events)]
            else:
                parsed_events = []

        else:
            r = await self.send_request(
                method="GET",
                url=f'https://proxy.opinion.trade:8443/api/bsc/api/v2/topic',
                params={
                    "labelId": "",
                    "keywords": "",
                    "sortBy": 3,
                    "chainId": 56,
                    "limit": 30,
                    "status": 2,
                    "isShow": 1,
                    "topicType": 2,
                    "page": 1,
                    "indicatorType": "2",
                },
            )
            response = await r.json()
            if response.get("errmsg") or response.get("errno"):
                raise Exception(f'Failed to parse events: {response}')

            raw_events = []
            for event in response["result"]["list"]:
                if event["childList"]:
                    for child in event["childList"]:
                        raw_events.append(_parse_event(child, event_name=event["title"]))
                else:
                    raw_events.append(_parse_event(event))

            parsed_events = []
            shuffle(raw_events)
            for event in raw_events:
                if min(event["prices"]) * 100 < BID_SETTINGS["PARSE"]["min_event_percent"]: continue
                book = await self.get_event_book(
                    question_id=event["raw_event"]["questionId"],
                    symbol=event["raw_event"]["yesPos"],
                    event_choice_index=0,
                )
                spread = round((book["asks"][0] - book["bids"][0]) * 100, 3)
                if spread <= BID_SETTINGS["PARSE"]["max_spread"]:
                    event["prices"] = [book["asks"][0], round(1 - book["bids"][0], 3)]
                    parsed_events.append(event)
                    if len(parsed_events) > 2: # to optimize requests
                        break

        if parsed_events:
            choose_event = choice(parsed_events)
            if BID_SETTINGS["LIST"] or BID_SETTINGS["SINGLE_BUY"] or event_to_find:
                book = await self.get_event_book(
                    question_id=choose_event["raw_event"]["questionId"],
                    symbol=choose_event["raw_event"]["yesPos"],
                    event_choice_index=0,
                )
                choose_event["prices"] = [book["asks"][0], round(1 - book["bids"][0], 3)]

            return choose_event


    @retry(source="Browser")
    async def get_event_book(self, question_id: str, symbol: str, event_choice_index: int):
        r = await self.send_request(
            method="GET",
            url='https://proxy.opinion.trade:8443/api/bsc/api/v2/order/market/depth',
            params={
                "symbol_types": str(event_choice_index),
                "question_id": question_id,
                "symbol": symbol,
                "chainId": "56",
            },
        )
        response = await r.json()
        if response.get("errmsg") or response.get("errno"):
            raise Exception(f'Failed to get event book: {response}')

        book = response["result"]
        asks = sorted(book["asks"], key=lambda x: float(x[0]))
        bids = sorted(book["bids"], key=lambda x: float(x[0]), reverse=True)

        return {
            "asks": [float(p[0]) for p in asks],
            "bids": [float(p[0]) for p in bids],
        }


    async def create_order(
            self,
            typed_message: dict,
            signature: str,
            event_id: int,
            safe_rate: str,
            price: str,
    ):
        payload = {
            "contractAddress": "",
            "orderExpTime": "0",
            "currencyAddress": "0x55d398326f99059fF775485246999027B3197955",
            "chainId": 56
        }
        payload.update({
            **typed_message,
            "topicId": event_id,
            "signature": signature,
            "sign": signature,
            "timestamp": int(time()),
            "safeRate": safe_rate,
            "price": price,
            "tradingMethod": 1 if price == "0" else 2,
        })
        r = await self.send_request(
            method="POST",
            url='https://proxy.opinion.trade:8443/api/bsc/api/v2/order',
            json=payload,
        )
        response = await r.json()
        if response.get("errmsg") or response.get("errno"):
            raise Exception(f'Failed to create order: {response}')

        return response["result"]["orderData"]


    async def get_orders(
            self,
            order_type: str,
            topic_id: int = None,
            trans_no: str = None,
            is_parent: bool = None,
    ):
        params = {
            "page": 1,
            "limit": 100,
            "walletAddress": self.address,
        }
        if order_type == "market":
            params["queryType"] = 2
        else:
            params["queryType"] = 1
        if topic_id:
            params["parentTopicId" if is_parent else "topicId"] = topic_id

        r = await self.send_request(
            method="GET",
            url='https://proxy.opinion.trade:8443/api/bsc/api/v2/order',
            params=params,
        )
        response = await r.json()
        if response.get("errmsg") or response.get("errno") or response.get("result") is None:
            raise Exception(f'Failed to get orders: {response}')

        orders = response["result"]["list"]
        if not trans_no:
            return orders or []

        return next((
            order for order in orders
            if order["transNo"] == trans_no
        ), None)


    async def get_position(self, topic_id: int = None, outcome_side: int = None):
        params = {
            "page": 1,
            "limit": 100,
            "walletAddress": self.address,
        }
        if topic_id:
            params["topicId"] = topic_id
        else:
            params["chainId"] = "56"
        r = await self.send_request(
            method="GET",
            url='https://proxy.opinion.trade:8443/api/bsc/api/v2/portfolio',
            params=params,
        )
        response = await r.json()
        if response.get("errmsg") or response.get("errno"):
            raise Exception(f'Failed to get position: {response}')

        positions = response["result"]["list"]
        if outcome_side:
            return next((
                position for position in positions
                if position["outcomeSide"] == outcome_side
            ), None)
        else:
            return positions or []


    async def get_rank(self):
        r = await self.send_request(
            method="GET",
            url=f'https://proxy.opinion.trade:8443/api/bsc/api/v2/leaderboard/{self.address}',
            params={
                "dataType": "volume",
                "chainId": "56",
                "period": "0",
            },
        )
        response = await r.json()
        if response.get("errmsg") or response.get("errno"):
            raise Exception(f'Failed to get rank: {response}')
        return response["result"]["id"] if response["result"] else 0


    async def get_points(self):
        r = await self.send_request(
            method="GET",
            url=f'https://proxy.opinion.trade:8443/api/bsc/api/v2/points?sources=web',
        )
        response = await r.json()
        if response.get("errmsg") or response.get("errno"):
            raise Exception(f'Failed to get points: {response}')
        return round(response["result"]["totalPoints"], 2) if response["result"] else 0


    async def cancel_order(self, trans_no: str):
        r = await self.send_request(
            method="POST",
            url=f'https://proxy.opinion.trade:8443/api/bsc/api/v1/order/cancel/order',
            json={
                "trans_no": trans_no,
                "chainId": 56,
            },
        )
        response = await r.json()
        if response.get("errmsg") or response.get("errno") or not response["result"]["result"]:
            raise Exception(f'Failed to cancel order: {response}')
