from random import randint
from loguru import logger
from time import sleep
import asyncio
import os

from modules import *
from modules.utils import async_sleep
from modules.retry import DataBaseError, SoftError
from settings import THREADS, SLEEP_AFTER_ACCOUNT, SLEEP_BETWEEN_THREADS



def initialize_account(module_data: dict, group_data: dict = None):
    browser = Browser(
        proxy=module_data['proxy'],
        address=module_data['address'],
        db=db,
    )
    wallet = Wallet(
        privatekey=module_data["privatekey"],
        encoded_pk=module_data["encoded_privatekey"],
        db=db,
    )
    opinion = Opinion(wallet=wallet, browser=browser, label=module_data["label"], group_data=group_data)
    if browser.proxy:
        opinion.log_message(f'Got proxy <white>{browser.proxy}</white>')
    else:
        opinion.log_message(f'<yellow>Dont use proxies</yellow>!')

    return opinion


async def thread_sleep(label: str, sleep_history: list):
        if len(sleep_history) < THREADS:
            async with SLEEP_LOCK:
                if len(sleep_history) == 0:
                    sleep_history.append(0)
                else:
                    sleep_history.append(randint(*SLEEP_BETWEEN_THREADS))
                to_sleep = sleep_history[-1]
                if to_sleep:
                    logger.debug(f'[•] {label} | Sleep {to_sleep}s before start...')
                    await asyncio.sleep(to_sleep)


async def run_modules(
        mode: int,
        module_data: dict,
        sem: asyncio.Semaphore,
        sleep_history: list,
):
    async with address_locks[module_data["address"]]:
        async with sem:
            await thread_sleep(module_data["label"], sleep_history)

            try:
                opinion = initialize_account(module_data)
                module_data["module_info"]["status"] = await opinion.run(mode=mode)

            except DataBaseError:
                module_data = None
                raise

            except Exception as err:
                logger.error(f'[-] Soft | {opinion.wallet.address} | Global error: {err}')
                await db.append_report(encoded_pk=module_data["encoded_privatekey"], text=str(err), success=False)

            finally:
                if type(module_data) == dict:
                    await opinion.browser.close_sessions()
                    if mode  == 1:
                        last_module = await db.remove_module(module_data)
                    else:
                        last_module = await db.remove_account(module_data)

                    reports = await db.get_account_reports(
                        key=module_data["encoded_privatekey"],
                        address=module_data["address"],
                        label=module_data["label"],
                        last_module=last_module,
                        mode=mode,
                    )
                    await TgReport().send_log(logs=reports)

                    await async_sleep(randint(*SLEEP_AFTER_ACCOUNT))


async def run_pair(
        mode: int,
        group_data: dict,
        sem: asyncio.Semaphore,
        sleep_history: list,
):
    async with MultiLock([wallet_data["address"] for wallet_data in group_data["wallets_data"]]):
        async with sem:
            await thread_sleep(f"Group {group_data['group_number']}", sleep_history)

            try:
                opinion_accounts = [
                    initialize_account(wallet_data, group_data=group_data)
                    for wallet_data in group_data["wallets_data"]
                ]
                group_data["module_info"]["status"] = await PairAccounts(
                    accounts=opinion_accounts,
                    group_data=group_data
                ).run()

            except Exception as err:
                logger.error(f'[-] Group {group_data["group_number"]} | Global error | {err}')
                await db.append_report(encoded_pk=group_data["group_index"], text=str(err), success=False)

            finally:
                for opinion in opinion_accounts:
                    await opinion.browser.close_sessions()

                await db.remove_group(group_data=group_data)

                reports = await db.get_account_reports(
                    key=group_data["group_index"],
                    label=f"Group {group_data['group_number']}",
                    last_module=False,
                    mode=mode,
                )
                await TgReport().send_log(logs=reports)

                if group_data["module_info"]["status"] is True:
                    to_sleep = randint(*SLEEP_AFTER_ACCOUNT)
                    logger.opt(colors=True).debug(f'[•] <white>Group {group_data["group_number"]}</white> | Sleep {to_sleep}s')
                    await async_sleep(to_sleep)
                else:
                    await async_sleep(10)


async def runner(mode: int):
    sem = asyncio.Semaphore(THREADS)

    sleep_history = []
    if mode == 4:
        all_groups = db.get_all_groups()
        if all_groups != 'No more accounts left':
            await asyncio.gather(*[
                run_pair(group_data=group_data, mode=mode, sem=sem, sleep_history=sleep_history)
                for group_data in all_groups
            ])

    else:
        all_modules = db.get_all_modules(unique_wallets=mode in [2, 3, 5])
        if all_modules != 'No more accounts left':
            await asyncio.gather(*[
                run_modules(
                    mode=mode,
                    module_data=module_data,
                    sem=sem,
                    sleep_history=sleep_history,
                )
                for module_data in all_modules
            ])

    logger.success(f'All accounts done.')
    return 'Ended'


if __name__ == '__main__':
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        db = DataBase()
        SLEEP_LOCK = asyncio.Lock()

        while True:
            mode = choose_mode()

            match mode.type:
                case "database":
                    db.create_modules(mode=mode.soft_id)

                case "module":
                    if asyncio.run(runner(mode=mode.soft_id)) == "Ended": break
                    print('')


        sleep(0.1)
        input('\n > Exit\n')

    except DataBaseError as e:
        logger.error(f'[-] Database | {e}')

    except SoftError as e:
        logger.error(f'[-] Soft | {e}')

    except KeyboardInterrupt:
        pass

    finally:
        logger.info('[•] Soft | Closed')



