from collections import defaultdict
import asyncio


address_locks = defaultdict(asyncio.Lock)

class MultiLock:
    def __init__(self, addresses: list[str]):
        self.locks = [address_locks[addr] for addr in sorted(addresses)]
        self.acquired: list[asyncio.Lock] = []

    async def __aenter__(self):
        while True:
            if all(not lock.locked() for lock in self.locks):
                for lock in self.locks:
                    await lock.acquire()
                    self.acquired.append(lock)
                return self
            await asyncio.sleep(1)

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        for lock in reversed(self.acquired):
            lock.release()
        self.acquired.clear()
