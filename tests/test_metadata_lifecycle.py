import asyncio
import time
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import server


class MetadataLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        server.session = None
        server.metadata_needed = asyncio.Event()
        server.metadata_ready = asyncio.Event()
        server.reconnect_signal = asyncio.Event()
        server.metadata_last_used = 0
        server.metadata_inflight = 0
        server.metadata_cache.clear()
        server.update(song={'hash': 'first'})
        self.starts = self.active = self.max_active = self.exits = self.requests = 0
        self.fail_start = False
        self.request_gate = None
        test = self

        @asynccontextmanager
        async def stdio(params):
            self.starts += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                yield None, None
            finally:
                self.active -= 1
                self.exits += 1

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def initialize(self):
                if test.fail_start:
                    test.fail_start = False
                    raise RuntimeError('initialization failed')

            async def send_ping(self):
                pass

            async def call_tool(self, name, arguments):
                test.requests += 1
                if test.request_gate is not None:
                    await test.request_gate.wait()
                data = {'url': 'https://imge.kugou.com/pic.jpg'} if name == 'pic' else {'lyric': '[00:01]line'}
                return SimpleNamespace(isError=False, structuredContent={'ok': True, 'data': data})

        for mock in (patch.object(server, 'stdio_client', stdio),
                     patch.object(server, 'ClientSession', side_effect=lambda *a, **k: Session()),
                     patch.object(server, 'METADATA_IDLE_SECONDS', .04)):
            mock.start()
            self.addCleanup(mock.stop)
        self.worker = asyncio.create_task(server.metadata_bridge())

    async def asyncTearDown(self):
        self.worker.cancel()
        await asyncio.gather(self.worker, return_exceptions=True)
        self.assertEqual(self.active, 0)
        self.assertEqual(server.metadata_inflight, 0)
        self.assertIsNone(server.session)
        self.assertFalse(server.metadata_ready.is_set())

    async def until(self, condition):
        deadline = time.monotonic() + 1
        while not condition():
            if time.monotonic() > deadline:
                self.fail('metadata lifecycle transition timed out')
            await asyncio.sleep(.002)

    async def test_lazy_start_idle_exit_cache_hit_and_restart(self):
        await asyncio.sleep(.01)
        self.assertEqual(self.starts, 0)
        await asyncio.wait_for(server.load_metadata('first'), 1)
        self.assertEqual(self.requests, 2)
        await self.until(lambda: self.exits == 1)
        await server.load_metadata('first')
        self.assertEqual(self.starts, 1, 'complete cached metadata must not start another process')
        server.update(song={'hash': 'second'})
        await asyncio.wait_for(server.load_metadata('second'), 1)
        self.assertEqual(self.starts, 2)
        self.assertEqual(self.requests, 4)
        self.assertEqual(self.max_active, 1, 'metadata processes must never overlap')

    async def test_idle_does_not_close_inflight_requests(self):
        self.request_gate = asyncio.Event()
        fetch = asyncio.create_task(server.load_metadata('first'))
        try:
            await self.until(lambda: self.requests == 2)
            await asyncio.sleep(.09)
            self.assertEqual(self.exits, 0)
            self.assertEqual(server.metadata_inflight, 2)
            self.request_gate.set()
            await asyncio.wait_for(fetch, 1)
            await self.until(lambda: self.exits == 1)
        finally:
            fetch.cancel()
            await asyncio.gather(fetch, return_exceptions=True)

    async def test_cancelled_song_releases_requests_and_idle_process(self):
        self.request_gate = asyncio.Event()
        fetch = asyncio.create_task(server.load_metadata('first'))
        await self.until(lambda: self.requests == 2)
        fetch.cancel()
        await asyncio.gather(fetch, return_exceptions=True)
        self.assertEqual(server.metadata_inflight, 0)
        await self.until(lambda: self.exits == 1)

    async def test_startup_failure_retries_waiting_request(self):
        self.fail_start = True
        sleep = asyncio.sleep

        async def short_retry(seconds):
            await sleep(.005 if seconds == 5 else seconds)

        with patch.object(server.asyncio, 'sleep', side_effect=short_retry):
            await asyncio.wait_for(server.load_metadata('first'), 1)
        self.assertEqual(self.starts, 2)
        self.assertEqual(self.requests, 2)
        self.assertEqual(self.max_active, 1)
