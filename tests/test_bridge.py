import asyncio
import threading
import unittest
import time
from unittest.mock import patch
import server


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        server.client_ready = asyncio.Event()
        server.refresh_signal = asyncio.Event()
        server.auto_launch_attempted = False
        ready = patch.object(server, 'client_ui_ready', return_value=True)
        ready.start()
        self.addCleanup(ready.stop)

    async def test_reuses_existing_debug_client(self):
        with patch.object(server, 'debug_available', return_value=True), patch('server.subprocess.Popen') as launch:
            await server.ensure_kugou()
        launch.assert_not_called()
        self.assertTrue(server.client_ready.is_set())

    async def test_normal_client_is_not_killed_or_restarted(self):
        with patch.object(server, 'debug_available', return_value=False), patch.object(server, 'kugou_is_running', return_value=True), patch('server.subprocess.Popen') as launch:
            await server.ensure_kugou()
        launch.assert_not_called()
        self.assertFalse(server.client_ready.is_set())
        self.assertEqual(server.state['startup_status'], 'running_without_debug')

    async def test_launches_with_original_connection_arguments(self):
        from pathlib import Path
        with patch.object(server, 'debug_available', side_effect=[False, True, True, True]), patch.object(server, 'kugou_is_running', return_value=False), patch('kugou_native_mcp.installation.find_executable', return_value=Path('KuGou.exe')), patch('server.subprocess.Popen') as launch, patch('server.asyncio.sleep') as wait:
            await server.ensure_kugou()
        self.assertIn('--remote_debug_port=9225', launch.call_args.args[0])
        self.assertEqual([c.args[0] for c in wait.await_args_list], [3, 2, 2])
        self.assertTrue(server.client_ready.is_set())

    async def test_open_debug_port_does_not_mark_hung_client_ready(self):
        with patch.object(server, 'debug_available', return_value=True), patch.object(server, 'client_ui_ready', return_value=False):
            await server.ensure_kugou()
        self.assertFalse(server.client_ready.is_set())
        self.assertEqual(server.state['startup_status'], 'waiting_for_client')

    async def test_failed_client_is_not_automatically_relaunched(self):
        server.auto_launch_attempted = True
        with patch.object(server, 'debug_available', return_value=False), patch.object(server, 'kugou_is_running', return_value=False), patch('server.subprocess.Popen') as launch:
            await server.ensure_kugou()
            await server.ensure_kugou()
        launch.assert_not_called()
        self.assertEqual(server.state['startup_status'], 'launch_failed')

    async def test_unresponsive_window_never_reaches_native_api(self):
        with patch.object(server, 'client_ui_ready', return_value=False), patch.object(server.native, 'snapshot') as read, patch.object(server.native, 'request') as send:
            with self.assertRaises(server.ClientNotReady):
                server.read_client_snapshot()
            with self.assertRaises(server.ClientNotReady):
                server.send_client_control('next', time.monotonic() + 14)
        read.assert_not_called()
        send.assert_not_called()

    async def test_user_interaction_defers_read_without_calling_native_api(self):
        with patch.object(server, 'client_ui_ready', return_value=True), patch.object(server, 'client_recent_input', return_value=True), patch.object(server.native, 'snapshot') as read:
            with self.assertRaises(server.ClientInteracting):
                server.read_client_snapshot()
        read.assert_not_called()

    async def test_quiet_period_has_no_post_launch_probes(self):
        from pathlib import Path
        entered, release = asyncio.Event(), asyncio.Event()
        async def wait(seconds):
            if seconds == 3:
                entered.set()
                await release.wait()
        with patch.object(server, 'debug_available', side_effect=[False, True, True, True]) as probe, patch.object(server, 'client_ui_ready', return_value=True) as ready, patch.object(server, 'kugou_is_running', return_value=False), patch('kugou_native_mcp.installation.find_executable', return_value=Path('KuGou.exe')), patch('server.subprocess.Popen'), patch('server.asyncio.sleep', side_effect=wait):
            task = asyncio.create_task(server.ensure_kugou())
            await asyncio.wait_for(entered.wait(), timeout=2)
            self.assertEqual(probe.call_count, 1)  # Only the check before launch.
            ready.assert_not_called()
            self.assertFalse(server.client_ready.is_set())
            release.set()
            await task


class ControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        ready = patch.object(server, 'client_ui_ready', return_value=True)
        ready.start()
        self.addCleanup(ready.stop)
        server.native_lock = asyncio.Lock()
        server.refresh_signal = asyncio.Event()
        server.control_pending = False
        server.next_poll_at = 0
        server.update(connected=True, control_busy=False)

    async def test_duplicate_click_does_not_queue_another_action(self):
        with patch.object(server.native, 'request', return_value={'accepted': True}) as send:
            await server.perform_control('next', time.monotonic() + 2)
            with self.assertRaises(server.ControlBusy):
                await server.perform_control('next', time.monotonic() + 2)
        send.assert_called_once()
        self.assertGreater(server.next_poll_at - time.monotonic(), .5)
        self.assertLess(server.next_poll_at - time.monotonic(), .7)

    async def test_expired_request_is_never_sent(self):
        with patch.object(server.native, 'request') as send:
            with self.assertRaises(TimeoutError):
                await server.perform_control('prev', time.monotonic() - 1)
        send.assert_not_called()

    async def test_failure_is_not_retried(self):
        with patch.object(server.native, 'request', side_effect=TimeoutError) as send:
            with self.assertRaises(TimeoutError):
                await server.perform_control('next', time.monotonic() + 2)
        send.assert_called_once()
        self.assertFalse(server.state['connected'])

    async def test_cancelled_worker_retains_serialization(self):
        entered, release = threading.Event(), threading.Event()
        def work():
            entered.set()
            release.wait(2)
        async def locked():
            async with server.native_lock:
                await server.native_work(work)
        task = asyncio.create_task(locked())
        await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(.02)
        self.assertTrue(server.native_lock.locked())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(server.native_lock.locked())

    async def test_refresh_requests_are_coalesced(self):
        server.client_ready = asyncio.Event()
        server.client_ready.set()
        server.last_viewer = time.monotonic()
        with patch.object(server.native, 'snapshot', return_value=({'available': True, 'hash': ''}, None)) as read:
            task = asyncio.create_task(server.refresh())
            try:
                for _ in range(20):
                    server.request_refresh()
                    await asyncio.sleep(.002)
                self.assertEqual(read.call_count, 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_control_replaces_next_poll_with_one_early_confirmation(self):
        server.client_ready = asyncio.Event()
        server.client_ready.set()
        server.last_viewer = time.monotonic()
        server.update(song={'hash': 'old'})
        server.session = None
        seen = asyncio.Event()
        async def native_work(fn, *args):
            if fn is server.read_client_snapshot:
                seen.set()
                return {'available': True, 'hash': ''}, None
            return {'accepted': True}
        with patch.object(server, 'native_work', side_effect=native_work) as work:
            task = asyncio.create_task(server.refresh())
            try:
                await asyncio.wait_for(seen.wait(), timeout=1)
                await server.perform_control('next', time.monotonic() + 2)
                await asyncio.sleep(.8)
                reads = [c for c in work.call_args_list if c.args[0] is server.read_client_snapshot]
                sends = [c for c in work.call_args_list if c.args[0] is server.send_client_control]
                self.assertEqual(len(reads), 2)  # Initial + confirmation, no extra regular poll.
                self.assertEqual(len(sends), 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


class IdleSamplingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        server.client_ready = asyncio.Event()
        server.client_ready.set()
        server.refresh_signal = asyncio.Event()
        server.native_lock = asyncio.Lock()
        server.control_pending = False
        server.next_poll_at = 0
        server.last_sample_at = 0
        server.last_viewer = time.monotonic() - 31
        server.session = None
        server.update(connected=True, control_busy=False)

    async def test_idle_stops_queries_and_resume_wakes_once(self):
        seen = asyncio.Event()
        async def work(fn, *args):
            seen.set()
            return {'available': True, 'hash': ''}, None
        with patch.object(server, 'native_work', side_effect=work) as read:
            task = asyncio.create_task(server.refresh())
            try:
                await asyncio.sleep(.03)
                read.assert_not_called()
                server.last_viewer = time.monotonic()
                server.viewer_resumed()
                await asyncio.wait_for(seen.wait(), timeout=1)
                for _ in range(10):
                    server.viewer_resumed()
                    await asyncio.sleep(.001)
                self.assertEqual(read.call_count, 1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_hidden_control_still_gets_one_confirmation(self):
        confirmed = asyncio.Event()
        async def work(fn, *args):
            if fn is server.read_client_snapshot:
                confirmed.set()
                return {'available': True, 'hash': ''}, None
            return {'accepted': True}
        with patch.object(server, 'native_work', side_effect=work) as work_mock:
            task = asyncio.create_task(server.refresh())
            try:
                await asyncio.sleep(.02)
                await server.perform_control('next', time.monotonic() + 2)
                await asyncio.wait_for(confirmed.wait(), timeout=1)
                await asyncio.sleep(.02)
                self.assertFalse(server.state['control_busy'])
                self.assertEqual([c.args[0] for c in work_mock.call_args_list],
                                 [server.send_client_control, server.read_client_snapshot])
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_disconnected_resume_wakes_without_bypassing_backoff(self):
        server.update(connected=False)
        server.next_poll_at = deadline = time.monotonic() + .15
        seen = asyncio.Event()
        async def work(fn):
            self.assertGreaterEqual(time.monotonic(), deadline)
            seen.set()
            return {'available': True, 'hash': ''}, None
        with patch.object(server, 'native_work', side_effect=work):
            task = asyncio.create_task(server.refresh())
            try:
                await asyncio.sleep(.02)
                server.last_viewer = time.monotonic()
                server.viewer_resumed()
                await asyncio.wait_for(seen.wait(), timeout=1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


class MetadataTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        server.metadata_cache.clear()
        from unittest.mock import AsyncMock
        ready = patch.object(server, 'ensure_metadata', new=AsyncMock())
        ready.start()
        self.addCleanup(ready.stop)

    async def test_cover_and_lyrics_use_actual_hash_without_search(self):
        from unittest.mock import AsyncMock
        server.update(song={'hash': 'actual-hash'})
        with patch.object(server, 'call', new=AsyncMock(side_effect=[{'data': {'url': 'https://imge.kugou.com/pic.jpg'}}, {'data': {'lyric': '[00:00]line'}}])) as call:
            await server.load_metadata('actual-hash')
        self.assertEqual([c.args[0] for c in call.await_args_list], ['pic', 'lyric'])
        self.assertTrue(all(c.args[1]['id'] == 'actual-hash' for c in call.await_args_list))
        self.assertEqual(server.state['lyrics'], '[00:00]line')

    async def test_stale_metadata_does_not_overwrite_new_song(self):
        server.update(song={'hash': 'new'}, cover='new-cover')
        server.update_if_current('old', cover='stale')
        self.assertEqual(server.state['cover'], 'new-cover')

    async def test_revisiting_song_uses_cache_without_more_mcp_calls(self):
        from unittest.mock import AsyncMock
        server.update(song={'hash': 'cached-song'}, cover='', lyrics='')
        server.remember_metadata('cached-song', cover='https://imge.kugou.com/a.jpg', lyrics='cached lyric', lyrics_status='')
        server.update(song={'hash': 'other'})
        server.update(song={'hash': 'cached-song'}, cover='', lyrics='')
        with patch.object(server, 'call', new=AsyncMock()) as call:
            await server.load_metadata('cached-song')
        call.assert_not_awaited()
        self.assertEqual(server.state['lyrics'], 'cached lyric')

    async def test_cover_and_lyric_requests_run_concurrently(self):
        entered = set()
        both = asyncio.Event()
        async def call(name, args):
            entered.add(name)
            if len(entered) == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 1)
            return {'data': {'url': 'https://imge.kugou.com/a.jpg'}} if name == 'pic' else {'data': {'lyric': ''}}
        server.update(song={'hash': 'parallel'})
        with patch.object(server, 'call', side_effect=call):
            await server.load_metadata('parallel')
        self.assertEqual(entered, {'pic', 'lyric'})
        self.assertEqual(server.state['lyrics_status'], '暂无歌词')

    async def test_partial_cache_only_fetches_the_missing_field(self):
        from unittest.mock import AsyncMock
        server.update(song={'hash': 'partial'})
        server.remember_metadata('partial', lyrics='known', lyrics_status='')
        with patch.object(server, 'call', new=AsyncMock(return_value={'data': {'url': 'https://imge.kugou.com/a.jpg'}})) as call:
            await server.load_metadata('partial')
        call.assert_awaited_once_with('pic', {'platform': 'kugou', 'id': 'partial'})

    async def test_cache_has_bounded_size(self):
        for i in range(30):
            server.remember_metadata(str(i), lyrics='line')
        self.assertEqual(len(server.metadata_cache), 5)
        self.assertNotIn('0', server.metadata_cache)
        self.assertEqual(list(server.metadata_cache), ['25', '26', '27', '28', '29'])

    async def test_successful_empty_metadata_is_read_once(self):
        from unittest.mock import AsyncMock
        server.update(song={'hash': 'empty'})
        with patch.object(server, 'call', new=AsyncMock(return_value={'ok': True, 'data': {}})) as call:
            await server.load_metadata('empty')
            await server.load_metadata('empty')
        self.assertEqual(call.await_count, 2)  # One cover + one lyric; no retry.
        self.assertEqual(server.metadata_cache['empty']['cover'], '')
        self.assertEqual(server.state['lyrics_status'], '暂无歌词')

    async def test_failed_cover_is_not_cached_as_missing(self):
        from unittest.mock import AsyncMock
        server.update(song={'hash': 'retry'})
        server.remember_metadata('retry', lyrics='', lyrics_status='暂无歌词')
        with patch.object(server, 'call', new=AsyncMock(side_effect=[
                {'ok': False, 'message': 'timeout'},
                {'ok': True, 'data': {'url': 'https://imge.kugou.com/retry.jpg'}}])) as call:
            await server.load_metadata('retry')
            self.assertNotIn('cover', server.metadata_cache['retry'])
            await server.load_metadata('retry')
        self.assertEqual(call.await_count, 2)
        self.assertEqual(server.state['cover'], 'https://imge.kugou.com/retry.jpg')


class VisibilityTests(unittest.TestCase):
    def test_resume_from_idle_respects_recent_native_read(self):
        now = time.monotonic()
        server.update(connected=True, control_busy=False)
        server.last_sample_at = now - 1
        server.next_poll_at = now + 19
        server.viewer_resumed()
        self.assertAlmostEqual(server.next_poll_at, server.last_sample_at + 4.4)

    def test_resume_cannot_bypass_failure_backoff_or_control_confirmation(self):
        for connected, busy in ((False, False), (True, True)):
            server.update(connected=connected, control_busy=busy)
            server.next_poll_at = deadline = time.monotonic() + 30
            server.viewer_resumed()
            self.assertEqual(server.next_poll_at, deadline)


class PortTests(unittest.TestCase):
    def test_os_assigned_port_and_host_validation(self):
        import http.client
        with server.LocalServer(('127.0.0.1', 0), server.Handler) as httpd:
            old = server.PORT
            server.PORT = port = httpd.server_address[1]
            worker = threading.Thread(target=httpd.serve_forever, daemon=True)
            worker.start()
            try:
                conn = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
                conn.request('GET', '/api/identity')
                self.assertEqual(conn.getresponse().status, 200)
                conn.close()
                conn = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
                conn.request('GET', '/api/state', headers={'Host': 'attacker.example'})
                self.assertEqual(conn.getresponse().status, 403)
                conn.close()
                conn = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
                conn.request('POST', '/api/control', body='{"action":"next"}', headers={
                    'Content-Type': 'application/json', 'Origin': 'https://attacker.example'})
                self.assertEqual(conn.getresponse().status, 403)
                conn.close()
            finally:
                httpd.shutdown()
                worker.join()
                server.PORT = old
