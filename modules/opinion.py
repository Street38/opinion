from random import uniform, randint, random, choice, shuffle
from datetime import datetime, timezone
from decimal import Decimal
from loguru import logger
from time import time
import asyncio

from modules.utils import round_cut, async_sleep, make_border, TgReport
from modules.retry import CustomError, retry
from modules.browser import Browser
from modules.wallet import Wallet
from settings import (
    SLEEP_BETWEEN_CLOSE_ORDERS,
    SLEEP_BETWEEN_OPEN_ORDERS,
    SLEEP_BETWEEN_ORDERS,
    LIMIT_SETTINGS,
    LIMIT_HOLDING,
    PAIR_SETTINGS,
    BID_SETTINGS,
    SELL_SETTINGS,
    BID_TYPES,
)


class Opinion:

    TYPED_DATA: dict = {
        "primaryType": "Order",
        "types": {
            "EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
            "Order": [
                {"name": "salt", "type": "uint256"}, {"name": "maker", "type": "address"},
                {"name": "signer", "type": "address"}, {"name": "taker", "type": "address"},
                {"name": "tokenId", "type": "uint256"}, {"name": "makerAmount", "type": "uint256"},
                {"name": "takerAmount", "type": "uint256"}, {"name": "expiration", "type": "uint256"},
                {"name": "nonce", "type": "uint256"}, {"name": "feeRateBps", "type": "uint256"},
                {"name": "side", "type": "uint8"}, {"name": "signatureType", "type": "uint8"}
            ]
        },
        "domain": {
            "name": "OPINION CTF Exchange",
            "version": "1",
            "chainId": 56,
            "verifyingContract": "0x5f45344126d6488025b0b84a3a8189f2487a7246"
        },
        "message": {
            "taker": "0x0000000000000000000000000000000000000000",
            "expiration": "0",
            "nonce": "0",
            "feeRateBps": "0",
            "signatureType": "2",
        },
    }

    def __init__(self, wallet: Wallet, browser: Browser, label: str, group_data: dict = None):
        self.wallet = wallet
        self.browser = browser
        self.encoded_pkey = wallet.encoded_pk
        self.label = label

        if group_data:
            self.group_number = group_data["group_number"]
            self.encoded_pkey = group_data["group_index"]
            self.prefix = f"[<i>{label}</i>] "
        else:
            self.group_number = None
            self.prefix = ""

        self.profile_info = None
        self.proxy_wallet = None


    @retry(source="Opinion")
    async def run(self, mode: int):
        status = None
        await self.login()

        if mode == 1:
            status = await self.buy_sell_position()

        elif mode == 2:
            status = await self.sell_all()

        elif mode == 3:
            status = await self.parse()

        elif mode == 5:
            status = await self.limit_holding()

        return status


    async def login(self):
        if not await self.browser.is_user_registered():
            raise CustomError(f"User {self.label} is not registered")

        date_now = datetime.now(timezone.utc)
        nonce = randint(65535, 0xffffffffffff)
        sign_message = f"""app.opinion.trade wants you to sign in with your Ethereum account:
{self.wallet.address}

Welcome to opinion.trade! By proceeding, you agree to our Privacy Policy and Terms of Use.

URI: https://app.opinion.trade
Version: 1
Chain ID: 56
Nonce: {nonce}
Issued At: {date_now.isoformat()[:-9] + 'Z'}"""
        signature = self.wallet.sign_message(sign_message).removeprefix("0x")

        await self.browser.user_login(
            sign_message,
            signature,
            int(date_now.timestamp()),
            nonce,
        )

        self.profile_info = await self.browser.get_profile_info()
        self.proxy_wallet = self.profile_info["multiSignedWalletAddress"].get("56")
        if not self.proxy_wallet:
            raise CustomError(f'No proxy wallet created for {self.label}')
        elif not await self.browser.is_approved(self.proxy_wallet):
            raise CustomError(f"Wallet {self.label} is not approved")


    async def buy_sell_position(self):
        buy_order_data = await self.create_order(
            order_side="buy",
            order_type=choice(BID_TYPES["open"]),
        )
        await async_sleep(randint(*SLEEP_BETWEEN_ORDERS))

        sell_order_data = await self.create_order(
            order_side="sell",
            order_type=choice(BID_TYPES["close"]),
            event=buy_order_data["event"],
            order=buy_order_data["order"],
        )

        profit = round(float(sell_order_data["order"]["totalPrice"]) - float(buy_order_data["order"]["totalPrice"]), 2)
        volume = round(float(buy_order_data["order"]["totalPrice"]) + float(sell_order_data["order"]["totalPrice"]), 2)

        await self.wallet.db.append_report(
            encoded_pk=self.encoded_pkey,
            text=f"\n🎰 <b>Profit {profit}$\n📌 Volume {volume}$</b>",
        )

        return True


    async def sell_all(self, silent: bool = False):
        sold_any = False

        if SELL_SETTINGS["cancel_orders"]:
            open_orders = await self.browser.get_orders(order_type="limit")
            for open_order in open_orders:
                await self.browser.cancel_order(open_order["transNo"])
                pos_name = f'{open_order["mutilTitle"]} {open_order["topicTitle"]}' if open_order["mutilTitle"] else open_order["topicTitle"]
                self.log_message(f'Cancelled order in "{pos_name}"', level="INFO")
                await self.wallet.db.append_report(
                    encoded_pk=self.encoded_pkey,
                    text=f'{self.prefix}cancel order "{pos_name}"',
                    success=True,
                )
                sold_any = True

        if SELL_SETTINGS["close_positions"]:
            positions = await self.browser.get_position()
            for position in sorted(positions, key=lambda x: x["value"], reverse=True):
                if float(position["value"]) >= SELL_SETTINGS["min_sell_usd"]:
                    await self.create_order(
                        order_side="sell",
                        order_type=choice(BID_TYPES["close"]),
                        position=position,
                    )
                    sold_any = True

        if not sold_any and not silent:
            self.log_message(f"No positions found to sell", level="INFO")
            await self.wallet.db.append_report(
                encoded_pk=self.encoded_pkey,
                text=f"{self.prefix}no positions found to sell",
                success=True,
            )

        return True


    async def parse(self):
        balance = round(float(self.profile_info["balance"][0]["balance"]), 2)
        profit = round(float(self.profile_info["totalProfit"]), 2)
        volume = round(float(self.profile_info["Volume"]), 2)

        positions, rank, points = await asyncio.gather(*[
            self.browser.get_position(),
            self.browser.get_rank(),
            self.browser.get_points(),
        ])
        total_positions = len([p for p in positions if float(p["value"]) >= 1])

        log_text = ({
            "Points": points,
            "Rank": rank,
            "Volume": volume,
            "Positions": total_positions,
            "Total Balance": f"{balance}$",
            "Profit": f"{profit}$",
        })
        self.log_message(f"Account statistics:\n{make_border(log_text)}", level="SUCCESS")
        tg_log = f"""🎖 Points: {points}
💎 Rank: {rank}
📈 Volume: {volume}$
📌 Positions: {total_positions}
💰 Total Balance: {balance}$
💵 Profit: {profit}$
"""
        await self.wallet.db.append_report(
            encoded_pk=self.encoded_pkey,
            text=tg_log
        )

        return True


    async def limit_holding(self):
        if LIMIT_HOLDING["side"] == "buy":
            order_data = await self.create_order(
                order_side="buy",
                order_type="limit",
                holding=True,
            )

        elif LIMIT_HOLDING["side"] == "sell":
            positions = await self.browser.get_position()
            any_matched = None
            for position in sorted(positions, key=lambda x: x["value"], reverse=True):
                any_matched = any([
                    (
                            single_params["event_name"] == position["topicTitle"] and
                            single_params["vote"] == position["outcomeSide"]
                    )
                    for single_params in BID_SETTINGS["SINGLE_BUY"]
                ])
                if any_matched:
                    if float(position["value"]) >= SELL_SETTINGS["min_sell_usd"]:
                        order_data = await self.create_order(
                            order_side="sell",
                            order_type=choice(BID_TYPES["close"]),
                            position=position,
                            holding=True,
                        )
                        break
                    else:
                        any_matched = False
                        event_name = position["mutilTitle"] + (" " if position["mutilTitle"] else "") + position["topicTitle"]
                        self.log_message(
                            f'Too low position value {round(float(position["value"]), 2)}$ in "{event_name}" to sell. Minimal is {SELL_SETTINGS["min_sell_usd"]}$',
                            level="WARNING"
                        )

            if not any_matched:
                raise CustomError(f'Not found any position to sell')

        await self.wallet.db.append_report(
            encoded_pk=self.encoded_pkey,
            text=f"❗ <b>limit filled</b>",
        )

        return True


    async def create_order(
            self,
            order_side: str,
            order_type: str,
            event: dict = None,
            order: dict = None,
            position: dict = None,
            usd_amount: float = None,
            force_vote: int = None,
            holding: bool = False,
            to_sleep: int = 0,
    ):
        if to_sleep:
            self.log_message(f"Sleep {to_sleep}s before {order_side}")
            await async_sleep(to_sleep)

        if order_side == "buy":
            side = 0
            if not event:
                event = await self.browser.get_events()
                if not event:
                    raise Exception(f'No events found')

            if usd_amount:
                amount = usd_amount
            else:
                amount = float(await self.calculate_order_amount())
            usd_amount = amount
            if force_vote is not None:
                event_choice_index = force_vote
            elif event.get("force_vote"):
                event_choice_index = event["force_vote"] - 1
            else:
                event_choice_index = choice([0, 1])
            token_id = event["tokens"][event_choice_index]
            label = event["labels"][event_choice_index]

            action_name = "Bidding"

        elif order_side == "sell":
            side = 1
            if position:
                event_choice_index = position["outcomeSide"] - 1
                label = position["outcome"]
                event = await self.browser.get_events(
                    event_to_find={
                        "link": f"?topicId={position['mutilTopicId'] or position['topicId']}{'&type=multi' if position['mutilTopicId'] else ''}",
                        "event_name": position["topicTitle"],
                        "vote": position["outcomeSide"],
                    },
                )

            elif order and event:
                event_choice_index = order["outcomeSide"] - 1
                position = await self.browser.get_position(
                    topic_id=event["raw_event"]["topicId"],
                    outcome_side=order["outcomeSide"]
                )
                if not position:
                    raise Exception(f'Failed to found active position "{event["name"]}"')

                label = event["labels"][event_choice_index]

            else:
                raise Exception(f'One of `position` or `order` & `event` must be provided for sell')

            amount = float(round_cut(position["tokenAmount"], 2))
            usd_amount = round_cut(position["value"], 2)

            token_id = position["tokenId"]

            action_name = "Selling"

        else:
            raise Exception(f'Unsupported order_side: `{order_side}`')

        book = await self.browser.get_event_book(
            question_id=event["raw_event"]["questionId"],
            symbol=event["raw_event"]["yesPos" if event_choice_index == 0 else "noPos"],
            event_choice_index=event_choice_index,
        )
        if order_type == "market":
            price = book["asks" if order_side == "buy" else "bids"][0]
            taker_amount = 0

        elif order_type == "limit":
            price = self._calculate_limit_price(order_side, book, holding)

            if order_side == "buy":
                taker_amount = float(round_cut(amount / price, 2))
                amount = float(Decimal(str(taker_amount)) * Decimal(str(price)))
            else:
                taker_amount = float(Decimal(str(amount)) * Decimal(str(price)))

        else:
            raise CustomError(f'Unsupported order type `{order_type}`')

        typed_data = self.TYPED_DATA.copy()
        typed_data["message"].update({
            "salt": str(int(random() * int(time() * 1e3))),
            "maker": self.proxy_wallet,
            "signer": self.wallet.address,
            "tokenId": token_id,
            "makerAmount": str(int(Decimal(str(amount)) * Decimal('1e18'))),
            "takerAmount": str(int(Decimal(str(taker_amount)) * Decimal('1e18'))),
            "side": str(side),
        })
        signature = self.wallet.sign_message(typed_data=typed_data)

        self.log_message(
            f'{action_name} <green>{usd_amount} USDT</green> for {label} in <blue>{event["name"]}</blue> <green>at {round(price * 100, 2)}¢</green>',
            level="INFO"
        )
        order_data = await self.browser.create_order(
            typed_message=typed_data["message"],
            signature=signature,
            event_id=event["raw_event"]["topicId"],
            safe_rate="0" if (order_side == "buy" and order_type == "market") else "0.05",
            price=str(price) if order_type == "limit" else "0"
        )

        if order_type == "limit":
            to_wait_sec = LIMIT_SETTINGS[f"to_wait_{order_side}"] * 60
            deadline_ts = int(time()) + to_wait_sec
            minutes_str = f"{LIMIT_SETTINGS[f'to_wait_{order_side}']} minute{'s' if LIMIT_SETTINGS[f'to_wait_{order_side}'] > 1 else ''}"
        else:
            minutes_str = ""

        if holding:
            limit_last_price = book["bids" if order_side == "buy" else "asks"][0]
            multiplier = Decimal(-1 if order_side == "buy" else 1)
            range_prices = "-".join([
                str(round(Decimal(str(limit_last_price)) * Decimal("100") + Decimal(str(offset)) * multiplier, 1)) + "¢"
                for offset in LIMIT_HOLDING["price_max_offset"]
            ])
            self.log_message(f"Waiting for last limit price will be out of range <white>{range_prices}</white> to change price")
            await self.wallet.db.append_report(
                encoded_pk=self.encoded_pkey,
                text=f"{self.prefix}open {order_type} {order_side} «{label}» for {usd_amount}$ at {round_cut(price * 100 , 2)}¢ in «{event['name']}»",
                success=True
            )

        else:
            self.log_message(f"Waiting for {order_type} {order_side} order filled" + (f" {minutes_str}" if minutes_str else ""))
        while True:
            filled_order = await self.browser.get_orders(
                topic_id=event["raw_event"]["topicId"],
                trans_no=order_data["transNo"],
                is_parent=event["is_child"],
                order_type="market",
            )

            if filled_order and round(float(filled_order["filled"].split('/')[0]), 2)  == round(float(filled_order["filled"].split('/')[1]), 2):
                final_price = round(float(filled_order["price"]) * 100, 2)
                total_price = round_cut(filled_order["totalPrice"], 2)
                self.log_message(f"Filled {order_type} {order_side} order for <green>{total_price}$ at {final_price}¢</green>", level="INFO")
                await self.wallet.db.append_report(
                    encoded_pk=self.encoded_pkey,
                    text=f"{self.prefix}{order_type} {order_side} «{label}» for {usd_amount}$ at {final_price}¢ in «{event['name']}»",
                    success=True
                )
                break

            elif order_type == "limit":
                if holding or time() > deadline_ts:
                    book = await self.browser.get_event_book(
                        question_id=event["raw_event"]["questionId"],
                        symbol=event["raw_event"]["yesPos" if event_choice_index == 0 else "noPos"],
                        event_choice_index=event_choice_index,
                    )
                    if holding:
                        current_price = book["bids" if order_side == "buy" else "asks"][0]
                        limits_diff = float(round_cut(abs(price - current_price) * 100, 1))
                        if not LIMIT_HOLDING["price_max_offset"][0] <= limits_diff <= LIMIT_HOLDING["price_max_offset"][1]:
                            self.log_message(f"Last limit price is {round_cut(current_price * 100, 1)}¢, changing limit price...")
                            await self.wallet.db.append_report(
                                encoded_pk=self.encoded_pkey,
                                text=f"⚠️ changing limit price: last price <i>{round_cut(current_price * 100, 1)}¢</i>, current price <i>{round(price * 100, 2)}¢</i>",
                            )
                            reports = await self.wallet.db.get_account_reports(
                                key=self.encoded_pkey,
                                address=self.wallet.address,
                                label=self.label,
                                last_module=False,
                                mode=5,
                            )
                            await TgReport().send_log(logs=reports)

                            await self.browser.cancel_order(order_data["transNo"])
                            self.log_message(f'Cancelled order in "{event["name"]}"', level="INFO")
                            event["force_vote"] = event_choice_index + 1
                            return await self.create_order(
                                    order_side=order_side,
                                    order_type=order_type,
                                    holding=holding,
                                    event=event,
                                    usd_amount=usd_amount,
                                    position=position,
                            )

                    else:
                        if price == self._calculate_limit_price(order_side, book, holding):
                            self.log_message(f"Limit order not filled in {minutes_str}, but price not changed, waiting again...")
                            deadline_ts = int(time()) + to_wait_sec
                        else:
                            self.log_message(f"Limit order not filled in {minutes_str}, changing price...")

                            await self.browser.cancel_order(order_data["transNo"])
                            self.log_message(f'Cancelled order in "{event["name"]}"', level="INFO")

                            if order_side == "buy":
                                event["force_vote"] = event_choice_index + 1

                            return await self.create_order(
                                    order_side=order_side,
                                    order_type=order_type,
                                    event=event,
                                    order=order,
                                    position=position,
                            )

            await async_sleep(3)

        return {
            "order": filled_order,
            "event": event,
        }


    async def get_balance(self):
        profile_info = await self.browser.get_profile_info()
        return float(profile_info["balance"][0]["balance"])


    async def calculate_order_amount(self):
        balance = await self.get_balance()
        if BID_SETTINGS["AMOUNTS"]["amounts"] != [0, 0]:
            amounts = BID_SETTINGS["AMOUNTS"]["amounts"][:]
            if amounts[0] > balance:
                raise Exception(f'Not enough balance: need {amounts[0]} have {round(balance, 2)}')
            elif amounts[1] > balance:
                amounts[1] = balance
            amount = uniform(*amounts)
        else:
            percent = uniform(*BID_SETTINGS["AMOUNTS"]["percents"]) / 100
            amount = balance * percent

        return round_cut(amount, 2)


    @classmethod
    def _calculate_limit_price(cls, order_side: str, book: dict, holding: bool):
        if holding:
            diff = uniform(*LIMIT_HOLDING["last_price_step"])
            price_diff = float(round_cut(diff / 100, 3))
        else:
            price_diff = float(round_cut(LIMIT_SETTINGS[f"diff_price_{order_side}"] / 100, 3))
        if order_side == "sell":
            price_diff *= -1
        price = float(round_cut(book["bids" if order_side == "buy" else "asks"][0] - price_diff, 3))
        return price


    def log_message(
            self,
            text: str,
            smile: str = "•",
            level: str = "DEBUG",
            colors: bool = True
    ):

        if self.group_number:
            if colors:
                label = f"<white>Group {self.group_number}</white> | <white>{self.label}</white>"
            else:
                label = f"Group {self.group_number} | {self.label}"
        else:
            label = f"<white>{self.label}</white>" if colors else self.label
        logger.opt(colors=colors).log(level.upper(), f'[{smile}] {label} | {text}')


class PairAccounts:
    def __init__(self, accounts: list[Opinion], group_data: dict):
        self.accounts = accounts
        self.group_number = f"Group {group_data['group_number']}"
        self.group_index = group_data["group_index"]


    async def run(self):
        await asyncio.gather(*[
            acc.login()
            for acc in self.accounts
        ])

        await self.open_and_close_position()
        return True


    async def open_and_close_position(self):
        event = await self.accounts[0].browser.get_events()
        if not event:
            raise Exception(f'No events found')

        open_values = self.calculate_zero_strategy(
            accounts=[acc.wallet.address for acc in self.accounts],
            probabilities=event["prices"],
            stake_range=await self.get_bid_amounts(),
        )

        buy_order_type = choice(BID_TYPES["open"])
        if buy_order_type == "limit":
            max_valuable_account_address = max(open_values, key=lambda k: open_values[k]['amount'])
        else:
            max_valuable_account_address = None
        max_valuable_account = next((acc for acc in self.accounts if acc.wallet.address == max_valuable_account_address), None)
        market_accounts = [acc for acc in self.accounts if acc.wallet.address != max_valuable_account_address]

        if max_valuable_account:
            open_order_data = await max_valuable_account.create_order(
                order_side="buy",
                order_type=buy_order_type,
                event=event,
                usd_amount=open_values[max_valuable_account_address]["amount"],
                force_vote=open_values[max_valuable_account_address]["index"],
            )

        tasks = []
        to_sleep_total = 0
        for acc_index, account in enumerate(market_accounts):
            random_sleep = randint(*SLEEP_BETWEEN_OPEN_ORDERS) if acc_index else 0
            to_sleep = to_sleep_total + random_sleep
            to_sleep_total += random_sleep
            tasks.append(
                account.create_order(
                    order_side="buy",
                    order_type="market",
                    event=event,
                    usd_amount=open_values[account.wallet.address]["amount"],
                    force_vote=open_values[account.wallet.address]["index"],
                    to_sleep=to_sleep,
                )
            )

        try:
            opened_positions = await asyncio.gather(*tasks)
            formatted_positions = {account.wallet.address: opened_positions[acc_index] for acc_index, account in enumerate(market_accounts)}
            if max_valuable_account:
                formatted_positions[max_valuable_account.wallet.address] = open_order_data
                opened_positions.append(open_order_data)
        except Exception as e:
            self.log_group_message(
                text=f'Failed to open "{event["name"]}" positions: {e}. Closing all positions...',
                smile="-",
                level="ERROR",
            )
            await self.accounts[-1].wallet.db.append_report(
                encoded_pk=self.accounts[-1].encoded_pkey,
                text=f'failed to open "{event["name"]}" positions',
                success=False
            )

            for account in self.get_randomized_accs(self.accounts):
                await account.sell_all(silent=True)
            return False

        to_sleep = randint(*PAIR_SETTINGS["position_hold"])
        self.log_group_message(text=f"Sleeping {to_sleep}s before close positions...")
        await async_sleep(to_sleep)

        sell_order_type = choice(BID_TYPES["close"])
        if sell_order_type == "limit":
            max_valuable_account_address = max(open_values, key=lambda k: open_values[k]['amount'])
        else:
            max_valuable_account_address = None
        max_valuable_account = next((acc for acc in self.accounts if acc.wallet.address == max_valuable_account_address), None)
        market_accounts = [acc for acc in self.accounts if acc.wallet.address != max_valuable_account_address]

        if max_valuable_account:
            close_order_data = await max_valuable_account.create_order(
                order_side="sell",
                order_type=sell_order_type,
                event=event,
                order=formatted_positions[max_valuable_account.wallet.address]["order"],
            )

        tasks = []
        to_sleep_total = 0
        randomized_accs = self.get_randomized_accs(market_accounts)
        for acc_index, account in enumerate(randomized_accs):
            random_sleep = randint(*SLEEP_BETWEEN_CLOSE_ORDERS) if acc_index else 0
            to_sleep = to_sleep_total + random_sleep
            to_sleep_total += random_sleep
            tasks.append(
                account.create_order(
                    order_side="sell",
                    order_type="market",
                    event=event,
                    order=formatted_positions[account.wallet.address]["order"],
                    to_sleep=to_sleep,
                )
            )
        try:
            closed_positions = await asyncio.gather(*tasks)
            if max_valuable_account:
                closed_positions.append(close_order_data)

        except Exception as e:
            self.log_group_message(
                text=f'Failed to close "{event["name"]}" positions: {e}. Closing all positions...',
                smile="-",
                level="ERROR",
            )
            await self.accounts[-1].wallet.db.append_report(
                encoded_pk=self.accounts[-1].encoded_pkey,
                text=f'failed to close "{event["name"]}" positions',
                success=False
            )

            for account in self.get_randomized_accs(self.accounts):
                await account.sell_all(silent=True)
            return False

        total_profit = 0
        total_volume = 0
        for pos in opened_positions + closed_positions:
            pos_amount = Decimal(pos["order"]["totalPrice"])
            total_volume += pos_amount
            if pos["order"]["side"] == 1: # if buy
                pos_amount *= -1
            total_profit += pos_amount

        total_profit = round(total_profit, 3)
        total_volume = round(total_volume, 1)
        self.log_group_message(
            text=f"Profit: <green>{total_profit}$</green> | "
                f"Total Volume: <green>{total_volume}$</green>",
            smile="+",
            level="INFO"
        )
        await self.accounts[-1].wallet.db.append_report(
            encoded_pk=self.accounts[-1].encoded_pkey,
            text=f'\n💰 <b>profit {total_profit}$</b>'
                 f'\n💵 <b>volume {total_volume}$</b>',
        )
        return True


    async def get_bid_amounts(self):
        balances_raw = await asyncio.gather(*[acc.get_balance() for acc in self.accounts])
        balances = {acc.wallet.address: balance for acc, balance in zip(self.accounts, balances_raw)}
        min_balance = min(balances.values())

        if BID_SETTINGS["AMOUNTS"]["amounts"] != [0, 0]:
            amounts = BID_SETTINGS["AMOUNTS"]["amounts"][:]

            ", ".join([f"{address} balance {balance}$" for address, balance in balances.items() if balance < 20])
            not_enough_error = ", ".join([f"{address}: {balance}$" for address, balance in balances.items() if balance < amounts[0]])

            if not_enough_error:
                raise Exception(f"Not enought balance: need {amounts[0]}, have {not_enough_error}")
            elif amounts[1] > min_balance:
                amounts[1] = min_balance

        else:
            percents = BID_SETTINGS["AMOUNTS"]["percents"]
            amounts = [min_balance * percents[0] / 100, min_balance * percents[1] / 100]

        if amounts[1] < 5:
            raise Exception(f'Minimal bid is 5$ but you have less')
        elif amounts[0] < 5:
            amounts[0] = 5

        return amounts


    def get_randomized_accs(self, lst: list):
        randomized_accounts = lst[:]
        shuffle(randomized_accounts)
        return randomized_accounts


    def calculate_zero_strategy(
            self,
            accounts: list,
            probabilities: list,
            stake_range: list
    ):
        min_stake, max_stake = stake_range
        p_a, p_b = probabilities
        retries = 0

        while True:
            retries += 1
            random_accounts = accounts[:-1]
            account_stakes = []
            total_stake_partial = 0
            for acc in random_accounts:
                stake = round(uniform(min_stake, max_stake), 2)
                account_stakes.append({"account": acc, "stake": stake})
                total_stake_partial += stake

            target_a_partial = total_stake_partial * p_a
            account_stakes.sort(key=lambda x: x['stake'], reverse=True)

            assignments = []
            partial_t_a = 0
            partial_t_b = 0

            for item in account_stakes:
                if (partial_t_a + item['stake']) <= target_a_partial:
                    partial_t_a += item['stake']
                    assignments.append({"index": 0, "stake": item['stake']})
                else:
                    partial_t_b += item['stake']
                    assignments.append({"index": 1, "stake": item['stake']})

            liability_a = partial_t_a / (p_a + 1e-9)
            liability_b = partial_t_b / (p_b + 1e-9)

            final_stake = None

            s_N_case_A = round((liability_b * p_a) - partial_t_a, 2)
            s_N_case_B = round((liability_a * p_b) - partial_t_b, 2)

            if min_stake <= s_N_case_A <= max_stake:
                final_stake = s_N_case_A
                assignments.append({"index": 0, "stake": final_stake})

            elif min_stake <= s_N_case_B <= max_stake:
                final_stake = s_N_case_B
                assignments.append({"index": 1, "stake": final_stake})

            if final_stake is not None:
                shuffle(assignments)
                return {
                    address: {
                        'index': item['index'],
                        'amount': item['stake'],
                    }
                    for address, item in zip(accounts, assignments)
                }

                final_result = {}
                for item in zip(accounts, assignments):
                    final_result[item['account']] = {
                        'index': item['index'],
                        'amount': item['stake']
                    }

                return final_result

            elif retries >= 1000:
                raise Exception(f'Failed to calculate zero imbalance strategy')


    def log_group_message(
            self,
            text: str,
            smile: str = "•",
            level: str = "DEBUG",
            colors: bool = True,
            account_label: str = ""
    ):
        label = f"<white>{self.group_number}</white>" if colors else self.group_number
        if account_label:
            if colors:
                label += f" | <white>{account_label}</white>"
            else:
                label += f" | {account_label}"
        logger.opt(colors=colors).log(level.upper(), f'[{smile}] {label} | {text}')
