"""Local web bridge for the installed kugo-mcp service."""
import asyncio
import argparse
import base64
import csv
import io
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import socket
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from contextlib import AsyncExitStack
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import ProxyHandler, build_opener

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from native_client import NativeClient
from kugou_process import client_ui_ready, client_recent_input

ROOT = Path(__file__).resolve().parent
MCP_HOME = Path(os.environ.get('KUGO_MCP_HOME', str(Path.home() / '.codex' / 'mcp' / 'kugo-mcp')))
PORT = 8765
SAMPLE_INTERVAL = 4.4
METADATA_CACHE_SIZE = 5
METADATA_IDLE_SECONDS = 30
state = {'mcp_connected': False, 'connected': False, 'control_busy': False, 'startup_status': 'checking', 'message': '正在连接酷狗…', 'song': None, 'cover': '', 'lyrics': '', 'lyrics_status': '等待歌曲', 'position_ms': None, 'position_sampled_at': None, 'updated_at': None}
mutex = threading.Lock()
loop = asyncio.new_event_loop()
session = None
metadata_needed = None
metadata_ready = None
metadata_last_used = 0.0
metadata_inflight = 0
generation = 0
refresh_signal = None
reconnect_signal = None
last_viewer = 0.0
client_ready = None
startup_task = None
native_lock = None
native = NativeClient()
next_poll_at = 0.0
control_pending = False
log = logging.getLogger('kugou')
bridge_task = None
auto_launch_attempted = False
last_sample_at = 0.0
metadata_cache = OrderedDict()


class ClientNotReady(RuntimeError):
    pass


class ClientInteracting(RuntimeError):
    pass


def update(**values):
    with mutex:
        state.update(values)


async def call(name, arguments):
    global metadata_last_used, metadata_inflight
    if session is None:
        raise RuntimeError('酷狗服务尚未连接')
    metadata_last_used = time.monotonic()
    metadata_inflight += 1
    try:
        result = await session.call_tool(name, arguments)
    except Exception:
        if reconnect_signal is not None:
            reconnect_signal.set()
        raise
    finally:
        metadata_inflight -= 1
        metadata_last_used = time.monotonic()
    if result.isError:
        raise RuntimeError(result.content[0].text if result.content else '调用失败')
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(result.content[0].text)


def update_song(song):
    with mutex:
        old_hash = (state.get('song') or {}).get('hash')
        new_hash = song.get('hash')
        if old_hash != new_hash:
            state.update(cover='', lyrics='', lyrics_status='正在加载歌词…', position_ms=None, position_sampled_at=None)
        state.update(song=song, connected=bool(song.get('available')), message='', updated_at=int(time.time() * 1000))


def update_if_current(song_hash, **values):
    with mutex:
        if (state.get('song') or {}).get('hash') == song_hash:
            state.update(values)


def remember_metadata(song_hash, **values):
    entry = metadata_cache.setdefault(song_hash, {})
    entry.update(values)
    metadata_cache.move_to_end(song_hash)
    while len(metadata_cache) > METADATA_CACHE_SIZE:
        metadata_cache.popitem(last=False)
    update_if_current(song_hash, **values)


async def load_cover(song_hash):
    try:
        pic = await call('pic', {'platform': 'kugou', 'id': song_hash})
        if pic.get('ok') is False:
            raise RuntimeError(pic.get('message', '封面接口不可用'))
        url = (pic.get('data') or {}).get('url') or ''
        if url.startswith('http://imge.kugou.com/'):
            url = 'https://' + url[7:]
        # A successful "no cover" result is immutable metadata too, not a
        # network error that should be retried for the entire song.
        remember_metadata(song_hash, cover=url)
    except Exception as exc:
        log.warning('cover metadata failed: %s', type(exc).__name__)


async def load_lyrics(song_hash):
    try:
        result = await call('lyric', {'platform': 'kugou', 'id': song_hash})
        if result.get('ok') is False:
            raise RuntimeError(result.get('message', '歌词接口不可用'))
        lyrics = (result.get('data') or {}).get('lyric') or ''
        remember_metadata(song_hash, lyrics=lyrics, lyrics_status='' if lyrics else '暂无歌词')
    except Exception as exc:
        update_if_current(song_hash, lyrics='', lyrics_status='歌词暂时不可用')
        log.warning('lyrics metadata failed: %s', type(exc).__name__)


async def ensure_metadata():
    global metadata_last_used
    while session is None:
        metadata_needed.set()
        await metadata_ready.wait()
    # Reserve the session until the parallel requests have actually started.
    metadata_last_used = time.monotonic()


async def load_metadata(song_hash):
    cached = metadata_cache.get(song_hash, {})
    if cached:
        metadata_cache.move_to_end(song_hash)
        update_if_current(song_hash, **cached)
    if 'cover' in cached and 'lyrics' in cached:
        return
    await ensure_metadata()
    work = []
    if 'cover' not in cached:
        work.append(load_cover(song_hash))
    if 'lyrics' not in cached:
        work.append(load_lyrics(song_hash))
    if work:
        # These are external Meting requests, never native KuGou queries.
        await asyncio.gather(*work)


async def monitor_mcp():
    while True:
        try:
            await asyncio.wait_for(session.send_ping(), timeout=10)
        except Exception as exc:
            update(mcp_connected=False)
            log.warning('metadata MCP health failed: %s', type(exc).__name__)
            reconnect_signal.set()
            return
        await asyncio.sleep(30)


def debug_available():
    port = int(os.environ.get('KUGOU_CDP_PORT', '9225'))
    try:
        with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{port}/json/list', timeout=2) as response:
            targets = json.load(response)
        return any(t.get('type') == 'page' and urlparse(t.get('url', '')).hostname == 'pc.service.kugou.com'
                   and urlparse(t.get('url', '')).path.startswith(('/yueku/', '/search/2024/')) for t in targets)
    except (OSError, ValueError):
        return False


def kugou_is_running():
    result = subprocess.run(['tasklist.exe', '/FI', 'IMAGENAME eq KuGou.exe', '/FO', 'CSV', '/NH'],
                            capture_output=True, text=True, timeout=8,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=True)
    return any(row and row[0].lower() == 'kugou.exe' for row in csv.reader(io.StringIO(result.stdout)))


async def ensure_kugou():
    global auto_launch_attempted
    try:
        update(startup_status='checking')
        if await asyncio.to_thread(debug_available):
            if await asyncio.to_thread(client_ui_ready):
                update(startup_status='ready')
                client_ready.set()
                refresh_signal.set()
            else:
                client_ready.clear()
                update(startup_status='waiting_for_client', connected=False,
                       message='酷狗正在加载或未响应，等待客户端恢复…')
            return
        if await asyncio.to_thread(kugou_is_running):
            client_ready.clear()
            update(startup_status='running_without_debug', connected=False,
                   message='酷狗已运行但无法连接，请完全退出酷狗，程序将自动重试。')
            return
        client_ready.clear()
        if auto_launch_attempted:
            update(startup_status='launch_failed', connected=False,
                   message='酷狗已退出，本次不再自动重启，请手动打开酷狗后重试')
            return
        update(startup_status='starting', connected=False, message='正在启动酷狗，请稍候…')
        log.info('starting KuGou; waiting for initialization')
        # Start the client without the MCP CLI's immediate CDP probes. The
        # debug page can appear before the client has finished initializing.
        from kugou_native_mcp.installation import find_executable
        executable = await asyncio.to_thread(find_executable)
        port = int(os.environ.get('KUGOU_CDP_PORT', '9225'))
        auto_launch_attempted = True
        # A detached GUI client must not belong to the asyncio transport lifetime.
        await asyncio.to_thread(subprocess.Popen,
            [str(executable), f'--remote_debug_port={port}', '--remote-debugging-address=127.0.0.1'],
            cwd=str(executable.parent), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        update(message='正在等待酷狗完成启动，请稍候…')
        # User-requested quiet period: no window/port/CDP probes after launch.
        await asyncio.sleep(3)
        deadline = time.monotonic() + 85
        stable = 0
        while stable < 3:
            if time.monotonic() >= deadline:
                raise RuntimeError('等待酷狗窗口就绪超时')
            ready = await asyncio.to_thread(client_ui_ready)
            ready = ready and await asyncio.to_thread(debug_available)
            stable = stable + 1 if ready else 0
            if stable == 3:
                break
            await asyncio.sleep(2)
        update(startup_status='ready')
        client_ready.set()
        refresh_signal.set()
    except Exception as exc:
        client_ready.clear()
        update(startup_status='error', connected=False, message='无法自动启动酷狗，请确认客户端已安装后重试')
        log.warning('KuGou startup failed: %s', type(exc).__name__)


def schedule_startup():
    global startup_task
    if startup_task is None or startup_task.done():
        startup_task = asyncio.create_task(ensure_kugou())


async def native_work(function, *args):
    # Cancellation must not release native_lock while its worker still uses CDP.
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            pass
        raise


def read_client_snapshot():
    if not client_ui_ready():
        raise ClientNotReady('酷狗窗口正在加载或未响应')
    if client_recent_input():
        raise ClientInteracting()
    return native.snapshot()


def send_client_control(action, expires_at):
    if not client_ui_ready():
        raise ClientNotReady('酷狗窗口未响应，暂不发送控制')
    return native.request(action, expires_at)


def retry_delay(failures):
    return min(30 * (2 ** min(failures - 1, 1)), 60)


async def refresh():
    global next_poll_at, last_sample_at
    last_hash = None
    metadata_task = None
    next_retry = 0
    failures = 0
    try:
        while True:
            await client_ready.wait()
            # With no viewer, wait for demand instead of querying a hidden player.
            # An outstanding control still gets its one confirmation.
            if time.monotonic() - last_viewer >= 30 and not state['control_busy'] and not control_pending:
                await refresh_signal.wait()
                refresh_signal.clear()
                continue
            delay = next_poll_at - time.monotonic()
            if delay > 0:
                try:
                    await asyncio.wait_for(refresh_signal.wait(), timeout=delay)
                    refresh_signal.clear()
                except asyncio.TimeoutError:
                    pass
                continue
            try:
                async with native_lock:
                    # Controls take priority; refresh requests cannot bypass the rate limit.
                    if control_pending or time.monotonic() < next_poll_at:
                        continue
                    request_generation = generation
                    current, position = await native_work(read_client_snapshot)
                    last_sample_at = time.monotonic()
                    next_poll_at = time.monotonic() + SAMPLE_INTERVAL
                if request_generation != generation:
                    continue
                failures = 0
                update_song(current)
                update(control_busy=False, message='' if current.get('available') else '酷狗当前没有可用歌曲')
                song_hash = current.get('hash')
                update_if_current(song_hash, position_ms=position,
                                  position_sampled_at=int(time.time() * 1000) if position is not None else None)
                if song_hash != last_hash:
                    if metadata_task is not None and not metadata_task.done():
                        metadata_task.cancel()
                        await asyncio.gather(metadata_task, return_exceptions=True)
                        metadata_task = None
                    last_hash, next_retry = song_hash, 0
                    if song_hash in metadata_cache:
                        metadata_cache.move_to_end(song_hash)
                        update_if_current(song_hash, **metadata_cache[song_hash])
                if song_hash and time.monotonic() >= next_retry and (metadata_task is None or metadata_task.done()):
                    cached = metadata_cache.get(song_hash, {})
                    needs_metadata = 'cover' not in cached or 'lyrics' not in cached
                    if needs_metadata:
                        next_retry = time.monotonic() + 30
                        metadata_task = asyncio.create_task(load_metadata(song_hash))
            except ClientInteracting:
                # Keep the valid cache, and do not mark user interaction as disconnect.
                next_poll_at = time.monotonic() + .5
            except ClientNotReady:
                next_poll_at = time.monotonic() + SAMPLE_INTERVAL
                update(connected=False, control_busy=False, position_ms=None, position_sampled_at=None,
                       message='酷狗正在加载或未响应，等待客户端恢复…')
            except Exception as exc:
                failures += 1
                next_poll_at = time.monotonic() + retry_delay(failures)
                update(connected=False, control_busy=False, position_ms=None, position_sampled_at=None,
                       message='酷狗暂未响应，已暂停请求，稍后自动重试')
                log.warning('snapshot failed; retry in %ss: %s', retry_delay(failures), type(exc).__name__)
                client_ready.clear()
                schedule_startup()
    finally:
        if metadata_task is not None:
            metadata_task.cancel()
            await asyncio.gather(metadata_task, return_exceptions=True)


class ControlBusy(RuntimeError):
    pass


async def perform_control(action, expires_at):
    global generation, control_pending, next_poll_at
    if action not in ('play', 'pause', 'next', 'prev'):
        raise ValueError('不支持此操作')
    with mutex:
        if control_pending or state['control_busy']:
            raise ControlBusy('正在等待酷狗状态更新，请稍候')
        if not state['connected']:
            raise ControlBusy('酷狗尚未连接，请稍候')
        state['control_busy'] = True
    control_pending = True
    generation += 1
    try:
        async with native_lock:
            if time.monotonic() >= expires_at:
                raise TimeoutError('操作已过期，未发送')
            result = await native_work(send_client_control, action, expires_at)
            # The next normal snapshot confirms the outcome. Never retry the command.
            next_poll_at = time.monotonic() + .6
            update(position_ms=None, position_sampled_at=None)
        return result
    except BaseException:
        next_poll_at = time.monotonic() + 30
        update(connected=False, control_busy=False, position_ms=None, position_sampled_at=None,
               message='操作未确认，已暂停请求，稍后读取实际状态')
        raise
    finally:
        control_pending = False
        generation += 1
        refresh_signal.set()


def request_refresh():
    # Coalesce requests; do not turn UI polling or retries into extra native reads.
    if refresh_signal is not None:
        refresh_signal.set()
    if client_ready is not None and not client_ready.is_set():
        schedule_startup()


def viewer_resumed():
    global next_poll_at
    if refresh_signal is not None:
        refresh_signal.set()
    with mutex:
        if not state['connected'] or state['control_busy']:
            return
    # Restore the normal cadence after idle; never bypass a recent native sample.
    next_poll_at = min(next_poll_at, max(time.monotonic(), last_sample_at + SAMPLE_INTERVAL))


async def maintain_startup():
    while True:
        if not client_ready.is_set():
            schedule_startup()
        await asyncio.sleep(30)


async def metadata_bridge():
    global session, metadata_last_used
    while True:
        await metadata_needed.wait()
        metadata_needed.clear()
        try:
            # Only Meting is needed for cover/lyrics. Do not spawn another native
            # MCP and its independent control connection for these network lookups.
            params = StdioServerParameters(
                command=str(MCP_HOME / 'node' / 'node.exe'),
                args=[str(MCP_HOME / 'meting' / 'node_modules' / '@eldment' / 'meting-agent' / 'src' / 'index.js')],
                env=dict(os.environ))
            async with AsyncExitStack() as stack:
                read, write = await stack.enter_async_context(stdio_client(params))
                metadata = await stack.enter_async_context(ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)))
                await metadata.initialize()
                session = metadata
                metadata_last_used = time.monotonic()
                metadata_needed.clear()
                update(mcp_connected=True)
                reconnect_signal.clear()
                metadata_ready.set()
                log.info('metadata MCP started on demand')
                health = asyncio.create_task(monitor_mcp())
                try:
                    while True:
                        timeout = METADATA_IDLE_SECONDS if metadata_inflight else max(.01, METADATA_IDLE_SECONDS - (time.monotonic() - metadata_last_used))
                        try:
                            await asyncio.wait_for(reconnect_signal.wait(), timeout=timeout)
                            break
                        except asyncio.TimeoutError:
                            if not metadata_inflight and time.monotonic() - metadata_last_used >= METADATA_IDLE_SECONDS:
                                log.info('metadata MCP idle; releasing process')
                                break
                finally:
                    metadata_ready.clear()
                    session = None
                    update(mcp_connected=False)
                    health.cancel()
                    await asyncio.gather(health, return_exceptions=True)
        except Exception as exc:
            metadata_ready.clear()
            session = None
            update(mcp_connected=False)
            log.warning('metadata MCP disconnected: %s', type(exc).__name__)
            await asyncio.sleep(5)
            metadata_needed.set()  # A waiting metadata request survives startup failure.


async def bridge():
    global refresh_signal, reconnect_signal, client_ready, native_lock, metadata_needed, metadata_ready
    refresh_signal = asyncio.Event()
    reconnect_signal = asyncio.Event()
    client_ready = asyncio.Event()
    native_lock = asyncio.Lock()
    metadata_needed = asyncio.Event()
    metadata_ready = asyncio.Event()
    try:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(maintain_startup())
            tasks.create_task(refresh())
            tasks.create_task(metadata_bridge())
    finally:
        if startup_task is not None:
            startup_task.cancel()
            await asyncio.gather(startup_task, return_exceptions=True)
        native.close()


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_cover(self):
        with mutex:
            url = state.get('cover', '')
            song_hash = (state.get('song') or {}).get('hash')
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname != 'imge.kugou.com':
            return self.send_json(404, {'error': '封面不可用'})
        try:
            with build_opener(ProxyHandler({})).open(url, timeout=8) as response:
                final = urlparse(response.geturl())
                if final.scheme != 'https' or final.hostname != 'imge.kugou.com':
                    raise ValueError('封面地址不受支持')
                data = response.read(3 * 1024 * 1024 + 1)
                mime = response.headers.get_content_type()
            if len(data) > 3 * 1024 * 1024 or mime not in ('image/jpeg', 'image/png', 'image/webp'):
                raise ValueError('封面格式或大小不受支持')
            with mutex:
                if url != state.get('cover') or song_hash != (state.get('song') or {}).get('hash'):
                    raise ValueError('歌曲已变化')
            return self.send_json(200, {'hash': song_hash, 'url': url, 'data': 'data:' + mime + ';base64,' + base64.b64encode(data).decode('ascii')})
        except Exception as exc:
            print(f'Cover download: {exc}', file=sys.stderr, flush=True)
            return self.send_json(503, {'error': '封面加载失败'})

    def valid_host(self):
        return self.headers.get('Host', '') in (f'127.0.0.1:{PORT}', f'localhost:{PORT}')

    def do_GET(self):
        global last_viewer
        if not self.valid_host():
            return self.send_error(403)
        path = urlparse(self.path).path
        if path == '/api/identity':
            return self.send_json(200, {'service': 'kugoumusicplay', 'version': 6, 'pid': os.getpid()})
        if path == '/api/cover':
            return self.send_cover()
        if path == '/api/state':
            now = time.monotonic()
            resumed = now - last_viewer >= 30
            last_viewer = now
            if resumed and loop.is_running():
                loop.call_soon_threadsafe(viewer_resumed)
            with mutex:
                snapshot = dict(state)
            return self.send_json(200, snapshot)
        assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/kugou-api.js': ('kugou-api.js', 'text/javascript; charset=utf-8')}
        if path not in assets:
            return self.send_error(404)
        filename, kind = assets[path]
        data = (ROOT / filename).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not self.valid_host() or self.headers.get('Origin') not in (f'http://127.0.0.1:{PORT}', f'http://localhost:{PORT}'):
            return self.send_error(403)
        if self.path not in ('/api/control', '/api/refresh') or self.headers.get('Content-Type') != 'application/json':
            return self.send_error(404)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 256:
                raise ValueError('无效请求')
            body = json.loads(self.rfile.read(length))
            if self.path == '/api/refresh':
                loop.call_soon_threadsafe(request_refresh)
                return self.send_json(202, {'accepted': True})
            action = body.get('action')
            if action not in ('play', 'pause', 'next', 'prev'):
                raise ValueError('不支持此操作')
            future = asyncio.run_coroutine_threadsafe(perform_control(action, time.monotonic() + 14), loop)
            try:
                result = future.result(timeout=16)
            except TimeoutError:
                future.cancel()
                raise
            self.send_json(202, result)
        except ControlBusy as exc:
            self.send_json(409, {'error': str(exc)})
        except Exception as exc:
            self.send_json(503, {'error': '操作未能确认，请查看酷狗当前状态'})
            log.warning('control unconfirmed: %s', type(exc).__name__)


def run_loop():
    global bridge_task
    asyncio.set_event_loop(loop)
    bridge_task = loop.create_task(bridge())
    try:
        loop.run_until_complete(bridge_task)
    except asyncio.CancelledError:
        pass
    finally:
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()


def watch_parent(parent_pid, httpd):
    # Keep a process handle so a reused PID cannot keep an orphan bridge alive.
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, parent_pid)
    try:
        if handle:
            while kernel.WaitForSingleObject(handle, 1000) == 0x102:
                pass
        log.info('owning widget exited; stopping bridge')
        httpd.shutdown()
    finally:
        if handle:
            kernel.CloseHandle(handle)


class LocalServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--parent-pid', type=int)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    with LocalServer(('127.0.0.1', args.port), Handler) as httpd:
        PORT = httpd.server_address[1]
        handler = RotatingFileHandler(Path(os.environ.get('KUGO_BRIDGE_LOG', str(ROOT / 'bridge.log'))), maxBytes=512 * 1024, backupCount=1, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.info('bridge v4 started pid=%s owner=%s port=%s', os.getpid(), args.parent_pid, PORT)
        print(json.dumps({'service': 'kugoumusicplay', 'version': 6, 'port': PORT}), flush=True)
        worker = threading.Thread(target=run_loop, daemon=True)
        worker.start()
        if args.parent_pid:
            threading.Thread(target=watch_parent, args=(args.parent_pid, httpd), daemon=True).start()
        try:
            httpd.serve_forever()
        finally:
            if bridge_task is not None and not loop.is_closed():
                loop.call_soon_threadsafe(bridge_task.cancel)
            worker.join(timeout=15)
            if worker.is_alive():
                # A stuck native worker must not keep an ownerless polling service
                # alive. This terminates only this service, never the KuGou client.
                log.error('service cleanup timed out; stopping own process')
                os._exit(0)
            log.info('bridge stopped')
