import asyncio
import aiohttp
import aiohttp.server
import time

from aiohttp.streams import EOF_MARKER

from . import errors
from .request import Request


__all__ = [
    'RESTRequestHandler',
    ]


class RESTRequestHandler(aiohttp.server.ServerHttpProtocol):

    def __init__(self,  application, *, hostname,
                 session_factory=None,
                 identity_policy=None, auth_policy=None,
                 **kwargs):
        super().__init__(**kwargs)
        self.application = application
        self.hostname = hostname
        self.session_factory = session_factory
        self._identity_policy = identity_policy
        self._auth_policy = auth_policy

    @asyncio.coroutine
    def handle_request(self, message, payload):
        now = time.time()
        # self.log.debug("Start handle request %r at %d", message, now)

        try:
            if payload is not None:
                req_body = bytearray()
                while True:
                    chunk = yield from payload.readany()
                    req_body.extend(chunk)
                    if chunk is EOF_MARKER:
                        break
            else:
                req_body = None

            request = Request(self, self.application, self.hostname, message, req_body,
                              session_factory=self.session_factory,
                              loop=self._loop,
                              identity_policy=self._identity_policy,
                              auth_policy=self._auth_policy)

            bbody = yield from self.application.dispatch(request)
            resp_impl = aiohttp.Response(
                self.writer, request.response.status_code,
                http_version=message.version)

            yield from request._call_response_callbacks()

            resp_impl.add_header('Host', self.hostname)

            # content encoding
            accept_encoding = message.headers.get('ACCEPT-ENCODING',
                                                  '').lower()
            accept_encoding = set(map(str.strip, accept_encoding.split(',')))
            if 'deflate' in accept_encoding:
                resp_impl.add_header('Transfer-Encoding', 'chunked')
                resp_impl.add_header('Content-Encoding', 'deflate')
                resp_impl.add_compression_filter('deflate')
                resp_impl.add_chunking_filter(1025)
            elif 'gzip' in accept_encoding:
                resp_impl.add_header('Transfer-Encoding', 'chunked')
                resp_impl.add_header('Content-Encoding', 'gzip')
                resp_impl.add_compression_filter('gzip')
                resp_impl.add_chunking_filter(1025)
            else:
                resp_impl.add_header('Content-Length', str(len(bbody)))

            headers = request.response.headers.items(getall=True)
            for key, val in headers:
                resp_impl.add_header(key, val)

            resp_impl.send_headers()
            resp_impl.write(bbody)
            yield from resp_impl.write_eof()
            if resp_impl.keep_alive():
                self.keep_alive(True)

            # self.log.debug("Fihish handle request %r at %d -> %s",
            #               message, time.time(), body)
            self.log_access(message, None, resp_impl, time.time() - now)
        except Exception:
            # self.log.exception("Cannot handle request %r", message)
            raise

    @asyncio.coroutine
    def handle_error(self, status=500, message=None, payload=None,
                     exc=None, headers=None):
        now = time.time()
        if isinstance(exc, errors.RESTError):
            resp_impl = aiohttp.Response(self.writer, status, close=True)
            resp_impl.add_header('Host', self.hostname)
            yield from exc.write_response(resp_impl)
            self.log_access(message, None, resp_impl, time.time() - now)
            self.keep_alive(False)
        else:
            super().handle_error(status, message, payload,
                                 exc=exc, headers=headers)